"""The Claude Code plugin (issue #201): manifests, skills, commands and hooks.

The plugin is text a model reads, so the tests hold it to the code: every tool
a skill or command tells the model to call exists in the contract, and none of
the tools still to come (#199, #202) is taught early. The hooks are tested as
functions and through `art hook` with a real stdin payload.
"""

import io
import json
import re
from pathlib import Path

import pytest

from harness.contract import BY_NAME

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugin"
TEXTS = sorted([*PLUGIN.glob("skills/*/SKILL.md"), *PLUGIN.glob("commands/*.md")])
# Tools docs/harness.md § 9 plans but the code does not expose yet.
NOT_YET = {"observe", "record_preference", "record_feedback", "art_pins", "check_draft"}


def _json(path):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def _frontmatter(path):
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")   # CRLF checkouts
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    assert m, f"{path.name} has no frontmatter"
    return dict(line.split(":", 1) for line in m.group(1).splitlines() if ":" in line), text


def test_manifests_point_at_the_plugin_and_its_server():
    assert _json("plugin/.claude-plugin/plugin.json")["name"] == "art"
    market = _json(".claude-plugin/marketplace.json")
    assert [p["source"] for p in market["plugins"]] == ["./plugin"]
    server = _json("plugin/.mcp.json")["mcpServers"]["art"]
    assert server["command"] == "uvx" and server["args"][-1] == "art-mcp"


def test_hooks_run_art_hook_events_from_the_same_package_as_the_server():
    from harness.hooks import EVENTS

    hooks = _json("plugin/hooks/hooks.json")["hooks"]
    source = _json("plugin/.mcp.json")["mcpServers"]["art"]["args"][1]
    wiring = {"UserPromptSubmit": ("user-prompt", None), "SessionStart": ("session-start", "compact")}
    assert set(hooks) == set(wiring)
    for event, (arg, matcher) in wiring.items():
        [group] = hooks[event]
        assert group.get("matcher") == matcher
        [hook] = group["hooks"]
        assert hook["command"] == f"uvx --from {source} art hook {arg}"
        assert EVENTS[arg][0] == event


def test_every_skill_and_command_has_frontmatter():
    for path in TEXTS:
        meta, _ = _frontmatter(path)
        assert meta.get("description", "").strip(), path
        if path.name == "SKILL.md":
            assert meta["name"].strip() == path.parent.name


def test_the_plugin_teaches_only_tools_that_exist():
    called = set()
    for path in TEXTS:
        _, text = _frontmatter(path)
        called |= set(re.findall(r"`([a-z_]+)(?:\(|`)", text))
    tools = {t for t in called if t in BY_NAME or t in NOT_YET}
    assert not tools & NOT_YET, tools & NOT_YET
    assert {"art_briefing", "open_job", "execute_plan", "patch_plan", "render",
            "upsert_items", "update_profile", "checkout", "history", "suggest_actions",
            "promote_bullet", "approve_variant", "save_baseline"} <= tools


def test_the_commands_are_the_issues_set_less_the_deferred_library():
    names = {p.stem for p in PLUGIN.glob("commands/*.md")}
    assert names == {"tailor", "setup", "open", "history", "revert", "prefs"}


# ── hooks ────────────────────────────────────────────────────────────────────

@pytest.fixture()
def job(kg, isolated_engine):
    from sqlmodel import Session, select

    from database.models import UserJobResult
    from harness import tree

    with Session(isolated_engine) as s:
        job_id = s.exec(select(UserJobResult).where(UserJobResult.user_id == kg)).first().job_id
    tree.commit_node(kg, job_id, content={"experiences": [
        {"title": "Data Science Intern", "company": "IDX Exchange", "bullets": ["Old bullet"]}]},
        source="host")
    return kg, job_id


def _edit(uid, job_id, bullets, source="editor"):
    from harness import tree
    return tree.commit_node(uid, job_id, source=source, content={"experiences": [
        {"title": "Data Science Intern", "company": "IDX Exchange", "bullets": bullets}]})


def test_the_prompt_hook_reports_only_new_editor_edits(job):
    from harness.hooks import user_prompt

    uid, job_id = job
    payload = {"session_id": "s1", "prompt": "hi"}
    assert user_prompt(uid, payload) is None            # first prompt: set the cursor only
    assert user_prompt(uid, payload) is None            # nothing new

    node = _edit(uid, job_id, ["New bullet"])
    text = user_prompt(uid, payload)
    assert f"job {job_id}" in text and node["node_id"] in text
    assert 'added "New bullet"' in text and 'removed "Old bullet"' in text
    assert "do not overwrite" in text
    assert user_prompt(uid, payload) is None            # reported once

    _edit(uid, job_id, ["Host bullet"], source="host")  # a host commit is not news
    assert user_prompt(uid, payload) is None
    assert user_prompt(uid, {"session_id": "s2"}) is None   # a new session starts at "now"


def test_the_compact_hook_restores_pins_verbatim_and_the_current_job(job):
    from harness.hooks import session_start, user_prompt

    uid, job_id = job
    user_prompt(uid, {"session_id": "s1"})
    _edit(uid, job_id, ["Edited"])
    user_prompt(uid, {"session_id": "s1"})
    text = session_start(uid, {"session_id": "s1", "source": "compact"})
    assert "- Never mention coursework projects" in text        # the kg fixture's pin
    assert f"job {job_id}" in text
    assert session_start(uid, {"session_id": "s1", "source": "startup"}) is None


def test_art_hook_speaks_claude_codes_json_and_never_fails(job, monkeypatch, capsys):
    from harness import hooks

    uid, job_id = job
    monkeypatch.setattr("harness.runtime.bootstrap", lambda *a, **k: (uid, True))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(
        {"session_id": "s9", "source": "compact", "hook_event_name": "SessionStart"})))
    assert hooks.main(["session-start"]) == 0
    reply = json.loads(capsys.readouterr().out)
    assert reply["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "Never mention coursework projects" in reply["hookSpecificOutput"]["additionalContext"]

    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    assert hooks.main(["user-prompt"]) == 0
    assert capsys.readouterr().out == ""
    assert hooks.run("user-prompt", {}, None) is None             # no bound user: silent


# ── Codex (#203) ─────────────────────────────────────────────────────────────

def test_codex_shares_the_plugin_directory_and_its_skills():
    codex = _json("plugin/.codex-plugin/plugin.json")
    claude = _json("plugin/.claude-plugin/plugin.json")
    assert codex["name"] == claude["name"] == "art" and codex["version"] == claude["version"]
    assert (PLUGIN / codex["skills"]).resolve() == (PLUGIN / "skills").resolve()
    assert (PLUGIN / codex["mcpServers"]).is_file()
    [entry] = _json(".agents/plugins/marketplace.json")["plugins"]
    assert entry["name"] == "art" and entry["source"] == {"source": "local", "path": "./plugin"}


def test_one_hook_config_serves_both_hosts():
    from harness.hooks import SOURCE, hook_config

    assert _json("plugin/hooks/hooks.json") == hook_config()
    assert SOURCE == _json("plugin/.mcp.json")["mcpServers"]["art"]["args"][1]


def test_skills_name_no_host_specific_slash_command():
    for path in PLUGIN.glob("skills/*/SKILL.md"):
        assert "/art:" not in path.read_text(encoding="utf-8"), path


def test_art_hooks_codex_merges_without_touching_existing_hooks(tmp_path, monkeypatch, capsys):
    from harness import entry
    from harness.hooks import hook_config

    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    mine = {"hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": "my-lint"}]}],
                      "Stop": [{"hooks": [{"type": "command", "command": "notify"}]}]}}
    (tmp_path / "hooks.json").write_text(json.dumps(mine), encoding="utf-8")

    assert entry.main(["hooks", "codex", "--write", "--from", "art-mcp"]) == 0
    got = json.loads((tmp_path / "hooks.json").read_text(encoding="utf-8"))["hooks"]
    ours = hook_config("art-mcp")["hooks"]
    assert got["Stop"] == mine["hooks"]["Stop"]
    assert got["UserPromptSubmit"] == mine["hooks"]["UserPromptSubmit"] + ours["UserPromptSubmit"]
    assert got["SessionStart"] == ours["SessionStart"]
    assert "/hooks" in capsys.readouterr().err

    entry.main(["hooks", "codex", "--write", "--from", "art-mcp"])    # idempotent
    again = json.loads((tmp_path / "hooks.json").read_text(encoding="utf-8"))["hooks"]
    assert again == got

    assert entry.main(["hooks", "codex"]) == 0                       # print only
    assert json.loads(capsys.readouterr().out) == hook_config()


def test_art_hooks_codex_refuses_to_rewrite_a_broken_file(tmp_path, monkeypatch):
    from harness import entry

    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    (tmp_path / "hooks.json").write_text("{not json", encoding="utf-8")
    assert entry.main(["hooks", "codex", "--write"]) == 2
    assert (tmp_path / "hooks.json").read_text(encoding="utf-8") == "{not json"
