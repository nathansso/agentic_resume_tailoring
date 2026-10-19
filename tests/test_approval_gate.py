"""The quoted-approval gate (issue #393): a plan commits only with the user's own approving words.

A dry run returns `plan_token`, the fingerprint of the exact program. The prompt hook keeps the
user's messages sent after it. A commit through the tool contract (`execute_plan` without
`dry_run`, or `patch_plan`) must carry the token of the program it commits and `approval_quote`,
words found in one of those messages; otherwise `approval_required`. Without hooks the token
alone approves, and the reply says `token_only`. The stored text goes when the plan commits or
expires, and never reaches an export or a log.
"""

import json
import logging
import zipfile
from pathlib import Path

import pytest

from harness import approval
from harness.contract import invoke
from harness.hooks import main as hook_main
from harness.hooks import user_prompt

# The executor's seeded benchmark job, with a one-line measurer (no LaTeX engine needed).
from test_executor import PROJ, env  # noqa: F401  (a fixture)

SECRET = "my salary history is 123456 and nobody should see this"


def _first(uid, job_id):
    """A job's first, empty plan: it commits ART's starting content and needs no approval."""
    out = invoke("execute_plan", uid, {"program": {"job_id": job_id, "parent": None}})
    assert out["error"] is None and out["committed"] and out["approval"] is None, out
    return out["node_id"]


def _plan(job_id, parent, item=PROJ, because="user:too junior"):
    return {"job_id": job_id, "parent": parent, "nodes": [
        {"id": "d", "op": "delete", "item_key": item, "because": because}]}


def _dry(uid, program, **extra):
    out = invoke("execute_plan", uid, {"program": program, "dry_run": True, **extra})
    assert out["error"] is None and out["plan_token"], out
    return out


def _commit(uid, program, **extra):
    return invoke("execute_plan", uid, {"program": program, **extra})


def _say(uid, text, session="s1"):
    """The user sends a message: the prompt hook sees it, as Claude Code runs it."""
    user_prompt(uid, {"session_id": session, "prompt": text})


def _head(uid, job_id):
    return invoke("get_head", uid, {"job_id": job_id})["head"]["node_id"]


def _refused(out, words):
    assert out["error"] is not None and out["error"]["code"] == "approval_required", out
    assert words in out["error"]["message"], out["error"]["message"]
    assert not out["committed"]


def _stored_texts(data_dir: Path):
    return [p.read_text(encoding="utf-8") for p in (data_dir / "approvals").rglob("*")
            if p.is_file()]


@pytest.fixture()
def hooked(env):
    """The seeded job with its first version, on a host whose prompt hook runs."""
    uid, job_id, _ = env
    parent = _first(uid, job_id)
    _say(uid, "hello")                                    # the hook has run: quotes are checked
    assert approval.hooks_present()
    return uid, job_id, parent


# ── the token ────────────────────────────────────────────────────────────────

def test_the_token_fingerprints_the_normalised_program(env):
    uid, job_id, _ = env
    parent = _first(uid, job_id)
    plan = _plan(job_id, parent)
    token = _dry(uid, plan)["plan_token"]
    # Key order, JSON whitespace, defaults left out and the host do not change it.
    reordered = json.loads(json.dumps({"nodes": plan["nodes"], "parent": parent,
                                       "job_id": job_id, "finalize": {}}, indent=3))
    assert _dry(uid, reordered)["plan_token"] == token
    assert _dry(uid, {**plan, "host": {"name": "codex"}})["plan_token"] == token
    # Anything the user would see change does.
    assert _dry(uid, _plan(job_id, parent, because="user:not relevant"))["plan_token"] != token
    assert _dry(uid, {**plan, "skills": ["Python"]})["plan_token"] != token
    assert _dry(uid, {**plan, "finalize": {"max_skills": 8}})["plan_token"] != token
    # A commit carries none.
    assert _commit(uid, plan, plan_token=token)["plan_token"] is None


# ── refusals ─────────────────────────────────────────────────────────────────

def test_a_commit_without_a_token_or_with_an_unknown_one_is_refused(hooked):
    uid, job_id, parent = hooked
    plan = _plan(job_id, parent)
    _refused(_commit(uid, plan), "no plan_token")
    _refused(_commit(uid, plan, plan_token="plan_0000", approval_quote="go ahead"),
             "no dry run of this plan was shown")
    assert _head(uid, job_id) == parent                   # nothing committed


def test_a_program_changed_since_its_dry_run_needs_a_new_dry_run_and_approval(hooked):
    uid, job_id, parent = hooked
    shown = _dry(uid, _plan(job_id, parent))
    _say(uid, "Looks good, go ahead")
    changed = _plan(job_id, parent, because="user:the user said so")
    _refused(_commit(uid, changed, plan_token=shown["plan_token"], approval_quote="looks good"),
             "the program changed since the dry run")
    # Its own dry run, then the old approval no longer counts: it was sent before this plan.
    token = _dry(uid, changed)["plan_token"]
    _refused(_commit(uid, changed, plan_token=token, approval_quote="looks good"),
             "not found in the user's messages since the plan was shown")
    _say(uid, "OK, that one too.")
    out = _commit(uid, changed, plan_token=token, approval_quote="ok, that one too")
    assert out["error"] is None and out["committed"] and out["approval"] == "quoted", out


def test_a_quote_must_come_from_a_message_sent_after_the_dry_run(hooked):
    uid, job_id, parent = hooked
    plan = _plan(job_id, parent)
    _say(uid, "Just do it, don't ask me.")                # before the plan was shown
    token = _dry(uid, plan)["plan_token"]
    _refused(_commit(uid, plan, plan_token=token, approval_quote="just do it, don't ask"),
             "not found in the user's messages since the plan was shown")
    _refused(_commit(uid, plan, plan_token=token), "approval_quote is missing")
    # No message at all since the dry run, on a host that has hooks: the refusal says how to
    # install them, in case this client runs without them.
    _refused(_commit(uid, plan, plan_token=token, approval_quote="just do it"),
             "artie hooks codex --write")
    _say(uid, "Can you look at the skills line first?")
    _refused(_commit(uid, plan, plan_token=token, approval_quote="ok"),   # not inside "look"
             "not found")
    _refused(_commit(uid, plan, plan_token=token, approval_quote="I approve"), "not found")
    assert _head(uid, job_id) == parent


def test_a_quote_found_in_a_later_message_commits_and_the_decision_log_records_it(hooked):
    from harness import tree

    uid, job_id, parent = hooked
    plan = _plan(job_id, parent)
    token = _dry(uid, plan)["plan_token"]
    _say(uid, "Thanks.  JUST do it,\n don’t ask -- ship it")   # the explicit skip, verbatim
    out = _commit(uid, plan, plan_token=token, approval_quote='"Just do it, don\'t ask"')
    assert out["error"] is None and out["committed"] and out["approval"] == "quoted", out
    node = tree.get_node(uid, out["node_id"])
    assert node["provenance"]["approval"] == {
        "mode": "quoted", "plan_token": token, "quote": '"Just do it, don\'t ask"'}


def test_patch_plan_commits_follow_the_same_rules(hooked):
    uid, job_id, parent = hooked
    saved = _dry(uid, _plan(job_id, parent))
    edits = [{"op": "replace", "path": "/nodes/0/because", "value": "user:it is a toy project"}]
    _refused(invoke("patch_plan", uid, {"program_id": saved["program_id"], "edits": edits}),
             "no plan_token")
    # The saved program's token is not the patched program's.
    _say(uid, "fine, go")
    _refused(invoke("patch_plan", uid, {"program_id": saved["program_id"], "edits": edits,
                                        "plan_token": saved["plan_token"],
                                        "approval_quote": "fine, go"}),
             "the program changed since the dry run")
    patched = invoke("patch_plan", uid, {"program_id": saved["program_id"], "edits": edits,
                                         "dry_run": True})
    assert patched["error"] is None and patched["plan_token"] != saved["plan_token"]
    _refused(invoke("patch_plan", uid, {"program_id": saved["program_id"], "edits": edits,
                                        "plan_token": patched["plan_token"],
                                        "approval_quote": "fine, go"}), "not found")
    _say(uid, "Yes, commit that.")
    out = invoke("patch_plan", uid, {"program_id": patched["program_id"], "edits": [],
                                     "plan_token": patched["plan_token"],
                                     "approval_quote": "yes, commit that"})
    assert out["error"] is None and out["committed"] and out["approval"] == "quoted", out


# ── without hooks ────────────────────────────────────────────────────────────

def test_without_hooks_the_token_alone_approves_and_says_so(env):
    uid, job_id, _ = env
    parent = _first(uid, job_id)
    assert not approval.hooks_present()
    plan = _plan(job_id, parent)
    _refused(_commit(uid, plan), "no plan_token")
    _refused(_commit(uid, plan, plan_token="plan_0000"), "no dry run")
    token = _dry(uid, plan)["plan_token"]
    _refused(_commit(uid, _plan(job_id, parent, because="user:other"), plan_token=token),
             "changed")
    out = _commit(uid, plan, plan_token=token)
    assert out["error"] is None and out["committed"] and out["approval"] == "token_only", out


# ── what is kept, and for how long ───────────────────────────────────────────

def test_messages_are_kept_only_while_a_plan_waits_and_go_on_commit(hooked, tmp_path):
    uid, job_id, parent = hooked
    assert not (tmp_path / "approvals" / "messages").exists() or \
        not any((tmp_path / "approvals" / "messages").iterdir())   # no plan waited on "hello"
    plan = _plan(job_id, parent)
    token = _dry(uid, plan)["plan_token"]
    _say(uid, SECRET)
    _say(uid, "go ahead")
    assert any(SECRET in t for t in _stored_texts(tmp_path))
    out = _commit(uid, plan, plan_token=token, approval_quote="go ahead")
    assert out["committed"], out
    assert not any(SECRET in t or "go ahead" in t for t in _stored_texts(tmp_path))
    assert approval.pending(uid) == []
    _say(uid, SECRET)                                     # nothing waits: not kept
    assert not any(SECRET in t for t in _stored_texts(tmp_path))


def test_messages_and_plans_expire(hooked, tmp_path, monkeypatch):
    uid, job_id, parent = hooked
    plan = _plan(job_id, parent)
    token = _dry(uid, plan)["plan_token"]
    _say(uid, SECRET + " go ahead")
    assert any(SECRET in t for t in _stored_texts(tmp_path))
    later = approval._now() + (approval.TTL_SECONDS + 60) * 1_000_000_000
    monkeypatch.setattr(approval, "_now", lambda: later)
    approval.prune()
    assert not any(SECRET in t for t in _stored_texts(tmp_path))
    assert approval.pending(uid) == []
    _refused(_commit(uid, plan, plan_token=token, approval_quote="go ahead"), "expired")


def test_a_session_bound_plan_takes_only_its_own_sessions_messages(hooked):
    uid, job_id, parent = hooked
    plan = _plan(job_id, parent)
    token = _dry(uid, plan, session_id="s1")["plan_token"]
    _say(uid, "go ahead", session="other")
    _refused(_commit(uid, plan, plan_token=token, approval_quote="go ahead"), "not found")
    _say(uid, "go ahead", session="s1")
    assert _commit(uid, plan, plan_token=token, approval_quote="go ahead")["committed"]


def test_nothing_reaches_an_export_or_a_log(hooked, tmp_path, caplog, isolated_engine):
    from harness.export_import import export_bundle

    uid, job_id, parent = hooked
    caplog.set_level(logging.DEBUG)
    _dry(uid, _plan(job_id, parent))
    _say(uid, SECRET)
    assert any(SECRET in t for t in _stored_texts(tmp_path))      # kept, waiting
    bundle = tmp_path / "out" / "backup.zip"
    export_bundle(isolated_engine, uid, bundle, apps_dir=tmp_path / "applications")
    with zipfile.ZipFile(bundle) as zf:
        names = zf.namelist()
        blob = b"".join(zf.read(n) for n in names)
    assert not any("approvals" in n for n in names)
    assert SECRET.encode() not in blob
    assert SECRET not in caplog.text


def test_the_hook_never_raises_when_the_store_is_broken(hooked, tmp_path, monkeypatch, capsys):
    uid, job_id, parent = hooked
    _dry(uid, _plan(job_id, parent))

    def boom(*a, **k):
        raise OSError("disk full")

    note, seen = approval.note_message, approval.hook_seen
    monkeypatch.setattr(approval, "note_message", boom)
    assert user_prompt(uid, {"session_id": "s1", "prompt": "go ahead"}) is None
    monkeypatch.setattr(approval, "hook_seen", boom)
    assert user_prompt(uid, {"session_id": "s1", "prompt": "go ahead"}) is None
    monkeypatch.setattr(approval, "note_message", note)
    monkeypatch.setattr(approval, "hook_seen", seen)
    # A file where the store's directory should be: still no exception, still exit 0.
    import shutil
    shutil.rmtree(tmp_path / "approvals")
    (tmp_path / "approvals").write_text("not a directory", encoding="utf-8")
    assert user_prompt(uid, {"session_id": "s1", "prompt": "go ahead"}) is None
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO("{not json"))
    assert hook_main(["user-prompt"]) == 0


def test_a_short_quote_must_be_the_users_whole_reply(hooked):
    uid, job_id, parent = hooked
    plan = _plan(job_id, parent)
    token = _dry(uid, plan)["plan_token"]
    _say(uid, "ok but change the second bullet")
    out = _commit(uid, plan, plan_token=token, approval_quote="ok")
    _refused(out, "only part of the user's message")
    assert "Pass the user's whole reply" in out["error"]["message"]
    assert "artie hooks" not in out["error"]["message"]          # a message was recorded
    _refused(_commit(uid, plan, plan_token=token, approval_quote="change the second"),
             "only part")                                         # 17 characters: too short
    assert _head(uid, job_id) == parent
    _say(uid, "OK! 👍")
    out = _commit(uid, plan, plan_token=token, approval_quote="ok")
    assert out["error"] is None and out["committed"] and out["approval"] == "quoted", out


def test_a_long_span_of_a_mixed_reply_approves(hooked):
    uid, job_id, parent = hooked
    plan = _plan(job_id, parent)
    token = _dry(uid, plan)["plan_token"]
    _say(uid, "Looks good, go ahead and save it to my Downloads folder please.")
    _refused(_commit(uid, plan, plan_token=token, approval_quote="Looks good"), "only part")
    out = _commit(uid, plan, plan_token=token, approval_quote="looks good, go ahead")   # 20
    assert out["error"] is None and out["committed"] and out["approval"] == "quoted", out


def test_quote_matching_normalises_and_compares_whole_messages():
    from harness.approval import MIN_SPAN_CHARS, core, match

    assert core("“Looks  GOOD, go!” 👍") == "looks good, go"
    assert match("Looks good, go", "looks good,\n  GO!") == "whole"
    assert match('"ship it."', "Ship it.") == "whole"
    assert match("‘just do it, don’t ask’", "Just do it, don't ask!!") == "whole"
    assert match("ok", "ok but change X") == "short"
    assert match("ok", "OK.") == "whole"
    assert match("ok", "look at this") is None
    assert match("go", "good") is None
    assert match(" ", "anything") is None and match("!!", "!!") is None
    span = "and save it to my Downloads"
    assert len(core(span)) >= MIN_SPAN_CHARS
    assert match(span, "Looks good, and save it to my Downloads.") == "span"
    assert match("save it to my Down", "Looks good, and save it to my Downloads.") is None
    assert approval.quote_in("looks good, go", "Looks good, go!")
    assert not approval.quote_in("ok", "ok, but change the second bullet")
