"""The "Active constraints for this job" block and the post-compaction restore (issue #299).

A reminder right before the host writes (`suggest_actions`, an `execute_plan` dry run), and the
hook that brings the hard page rules and the pending bans back after compaction. The benchmark
that measures whether any of it works is `tests/test_preference_compliance.py`.
"""

import json

import pytest

from harness.contract import invoke


@pytest.fixture()
def env(isolated_engine, monkeypatch):
    """The benchmark profile as one user with two jobs of different role families."""
    from eval.preference_compliance import open_job
    from eval.profile_fixture import load_profile
    from eval.scripted_host import seed_profile
    from eval.tailoring_benchmark import PROFILES_DIR, load_tasks
    from harness import executor

    monkeypatch.setattr(executor, "MEASURER", lambda texts: [1 for _ in texts])
    uid = seed_profile(load_profile(PROFILES_DIR / "benchmark_profile.md"))
    tasks = load_tasks(limit=5)
    jobs = [open_job(uid, tasks[0]), open_job(uid, tasks[1])]
    families = [invoke("art_briefing", uid, {"job_id": j})["role_family"] for j in jobs]
    assert families[0] != families[1]
    return uid, jobs, families


def _pref(uid, text, target, polarity="suppress", strength=5, scope="global", scope_value=None):
    out = invoke("record_preference", uid, {
        "text": text, "polarity": polarity, "target": target, "strength": strength,
        "scope": scope, "scope_value": scope_value})
    assert out["status"] in ("stored", "superseded"), out
    return out


def _rule(uid, text, kind, params=None, strength=5, scope="global", scope_value=None):
    out = invoke("record_rule", uid, {
        "text": text, "kind": kind, "params": params, "strength": strength,
        "scope": scope, "scope_value": scope_value})
    assert out["status"] in ("stored", "superseded"), out
    return out


def _seed_everything(uid, jobs, families):
    _pref(uid, "Never include StreamBoard.", "StreamBoard")                          # hard pin
    _pref(uid, "Never mention Redpanda.", "Redpanda")                                # hard, pending
    _pref(uid, "I would rather not mention Dagster.", "Dagster", strength=3)         # soft, pending
    _pref(uid, "I like LLM Resume Coach on my resume.", "LLM Resume Coach",
          polarity="emphasize", strength=3)                                          # item, soft
    _pref(uid, f"For {families[0]} roles, never include Harbor Labs.", "Software Engineer @ Harbor Labs",
          scope="role_family", scope_value=families[0])
    _pref(uid, "On this one, never list Looker.", "Looker", scope="job", scope_value=jobs[0])
    _rule(uid, "Keep it to one page.", "limit", {"pages": 1})
    _rule(uid, "At most three bullets under a role.", "limit", {"bullets_per_item": 3}, strength=3)
    _rule(uid, "Keep the tone plain.", "style_note", strength=3)


# ── suggest_actions ──────────────────────────────────────────────────────────

def test_suggest_actions_leads_with_every_constraint_for_the_job_with_its_scope(env):
    uid, jobs, families = env
    _seed_everything(uid, jobs, families)
    out = invoke("suggest_actions", uid, {"job_id": jobs[0]})

    assert next(k for k in out if k != "error") == "active_constraints"
    block = out["active_constraints"]
    assert block["title"] == "Active constraints for this job"
    assert block["text"].startswith("Active constraints for this job")
    assert block["job_id"] == jobs[0] and block["role_family"] == families[0]

    pins = {p["text"]: p for p in block["hard_pins"]}
    assert set(pins) == {"Never include StreamBoard.",
                         f"For {families[0]} roles, never include Harbor Labs."}
    assert pins["Never include StreamBoard."]["scope_label"] == "global"
    assert pins[f"For {families[0]} roles, never include Harbor Labs."]["scope_label"] == \
        f"role family {families[0]}"

    bans = {b["text"]: b for b in block["pending_bans"]}
    assert set(bans) == {"Never mention Redpanda.", "I would rather not mention Dagster.",
                         "On this one, never list Looker."}                  # the job-scoped one too
    assert bans["On this one, never list Looker."]["scope_label"] == "this job"
    assert bans["Never mention Redpanda."]["hard"] and not bans["I would rather not mention Dagster."]["hard"]
    assert bans["Never mention Redpanda."]["target_term"].lower() == "redpanda"

    items = {p["text"]: p for p in block["item_preferences"]}
    assert set(items) == {"I like LLM Resume Coach on my resume."}
    assert items["I like LLM Resume Coach on my resume."]["polarity"] == "emphasize"

    rules = block["rules"]
    assert [(r["param"], r["value"], r["scope_label"]) for r in rules["hard"]] == [("pages", 1, "global")]
    assert [(r["param"], r["value"]) for r in rules["soft"]] == [("bullets_per_item", 3)]
    assert [r["text"] for r in rules["style_notes"]] == ["Keep the tone plain."]

    for text in ("Never include StreamBoard.", "Never mention Redpanda.", "Keep it to one page.",
                 "On this one, never list Looker."):
        assert text in block["text"]


def test_the_block_is_scoped_as_execute_plan_scopes_it(env):
    uid, jobs, families = env
    _seed_everything(uid, jobs, families)
    other = invoke("suggest_actions", uid, {"job_id": jobs[1]})["active_constraints"]
    texts = [e["text"] for e in other["hard_pins"] + other["item_preferences"] + other["pending_bans"]]
    assert "Never include StreamBoard." in texts and "Never mention Redpanda." in texts
    assert not any("Harbor Labs" in t or "Looker" in t for t in texts)      # another family, another job
    assert other["role_family"] == families[1]


def test_a_user_with_nothing_stated_gets_a_block_that_says_so(env):
    uid, jobs, _ = env
    block = invoke("suggest_actions", uid, {"job_id": jobs[0]})["active_constraints"]
    assert block["counts"] == {"hard_pins": 0, "pending_bans": 0, "item_preferences": 0,
                               "rules_hard": 0, "rules_soft": 0, "style_notes": 0}
    assert "nothing the user has said applies" in block["text"]


def test_suggest_actions_keeps_every_field_it_had(env):
    uid, jobs, families = env
    before = invoke("suggest_actions", uid, {"job_id": jobs[0]})
    _seed_everything(uid, jobs, families)
    after = invoke("suggest_actions", uid, {"job_id": jobs[0]})
    assert set(after) - set(before) == set()
    for field in ("job_id", "node_id", "floor"):
        assert after[field] == before[field]
    assert [i["item_key"] for i in after["items"]] == [i["item_key"] for i in before["items"]]


# ── execute_plan dry run ─────────────────────────────────────────────────────

def test_a_dry_run_leads_with_the_block_and_a_commit_does_not_carry_it(env):
    from eval.scripted_host import build_program
    from eval.tailoring_benchmark import load_tasks

    uid, jobs, families = env
    _seed_everything(uid, jobs, families)
    task = load_tasks(limit=5)[0]
    program = invoke("get_head", uid, {"job_id": jobs[0]})                     # no history yet
    assert program["head"] is None
    plan = build_program(uid, jobs[0], task["description"])

    dry = invoke("execute_plan", uid, {"program": plan, "dry_run": True})
    assert next(k for k in dry if k != "error") == "active_constraints"
    block = dry["active_constraints"]
    assert block == invoke("suggest_actions", uid, {"job_id": jobs[0]})["active_constraints"]
    assert block["counts"]["hard_pins"] == 2 and block["counts"]["rules_hard"] == 1

    # additive: every field a dry run had is still there with the same value
    plain = {k: v for k, v in dry.items() if k != "active_constraints"}
    assert {"program_id", "parent", "dry_run", "committed", "nodes", "violations", "line_budget",
            "metrics", "cut_hints"} <= set(plain)
    assert dry["dry_run"] is True and dry["committed"] is False

    committed = invoke("execute_plan", uid, {"program": plan})
    assert "active_constraints" not in committed or committed["active_constraints"] is None
    assert {k: v for k, v in committed.items()
            if k not in ("active_constraints", "dry_run", "committed", "node_id")} == \
        {k: v for k, v in plain.items() if k not in ("dry_run", "committed", "node_id")}


def test_the_block_reads_the_scope_of_the_job_it_is_for(env):
    from eval.scripted_host import build_program
    from eval.tailoring_benchmark import load_tasks

    uid, jobs, families = env
    _seed_everything(uid, jobs, families)
    plan = build_program(uid, jobs[1], load_tasks(limit=5)[1]["description"])
    block = invoke("execute_plan", uid, {"program": plan, "dry_run": True})["active_constraints"]
    assert block["job_id"] == jobs[1] and block["role_family"] == families[1]
    assert "Harbor Labs" not in block["text"]


# ── briefing and pins ────────────────────────────────────────────────────────

def test_art_pins_lists_every_pending_ban_with_its_strength(env):
    uid, jobs, families = env
    _seed_everything(uid, jobs, families)
    pins = invoke("art_pins", uid, {"job_id": jobs[0]})
    assert {b["text"]: b["hard"] for b in pins["pending_bans"]} == {
        "Never mention Redpanda.": True, "I would rather not mention Dagster.": False,
        "On this one, never list Looker.": True}
    assert [r["param"] for r in pins["rules"]] == ["pages"]
    # a strength-5 pending ban is a pin too, and its softer sibling is not
    assert "Never mention Redpanda." in [p["text"] for p in pins["pins"]]
    assert "I would rather not mention Dagster." not in [p["text"] for p in pins["pins"]]


# ── the hook after compaction ────────────────────────────────────────────────

def test_the_compact_hook_restores_hard_rules_and_pending_bans_for_the_job(env):
    from harness import hooks

    uid, jobs, families = env
    _seed_everything(uid, jobs, families)
    _rule(uid, f"For {families[0]} roles, only two bullets under a role.", "limit",
          {"bullets_per_item": 2}, scope="role_family", scope_value=families[0])
    hooks.save_state("s-a", {"cursor": 0, "job_id": jobs[0]})
    text = hooks.session_start(uid, {"session_id": "s-a", "source": "compact"})
    lines = text.splitlines()

    assert "ART pinned preferences, verbatim" in lines[0]
    for want in ("- Never include StreamBoard.", "- Never mention Redpanda.",
                 "- On this one, never list Looker."):
        assert want in lines
    assert "ART hard page rules, verbatim (a plan that loosens one is refused):" in lines
    assert any(ln.startswith("- Keep it to one page. (pages: 1)") for ln in lines)
    assert any(ln.startswith(f"- For {families[0]} roles, only two bullets under a role. "
                             f"(bullets_per_item: 2) [role family {families[0]}]") for ln in lines)
    # the soft ban is not a pin, so it comes in its own block; the hard one is not listed twice
    assert "- I would rather not mention Dagster." in lines
    assert lines.count("- Never mention Redpanda.") == 1
    assert "Keep the tone plain." not in text and "Keep it to one page. (pages" in text

    # the other job's session: no job-scoped pin, no other family's rule
    hooks.save_state("s-b", {"cursor": 0, "job_id": jobs[1]})
    other = hooks.session_start(uid, {"session_id": "s-b", "source": "compact"})
    assert "Looker" not in other and "only two bullets" not in other
    assert "Keep it to one page." in other and "Never mention Redpanda." in other


def test_the_hook_says_nothing_when_nothing_is_pinned(env):
    from harness import hooks

    uid, _, _ = env
    assert hooks.session_start(uid, {"session_id": "none", "source": "compact"}) is None
    assert hooks.session_start(uid, {"session_id": "none", "source": "startup"}) is None
