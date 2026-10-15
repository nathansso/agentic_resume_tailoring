"""Document-level rules (issue #298): `ResumeRule`, and what enforces it at finalize.

A rule is about the page, not an item on it. `limit` and `section_order` become the finalize
defaults a plan may only tighten; `style_note` is pushed and never blocks. The scope-precedence
rule (a narrower scope may only tighten a hard rule) is tested here too.
"""

import copy

import pytest
from sqlmodel import Session, select

import services
from database.models import ResumeRule, UserPreference
from harness import rules as R
from harness import tools
from test_executor import EXP, EXP2, PROJ, _run, env  # noqa: F401  (fixture)


def _rule(rule_id, kind, params, strength=5, scope="global", value=None, text=None, stamp="2026-01-01"):
    return {"rule_id": rule_id, "kind": kind, "params": params, "text": text or f"rule {rule_id}",
            "strength": strength, "scope_type": scope, "scope_value": value, "status": "active",
            "created_at": stamp}


def _record(uid, text, kind="limit", params=None, strength=5, scope="global", value=None):
    return tools.record_rule(uid, text, kind, params, strength, scope, value)


def _head_content(uid, job_id):
    from harness import tree
    return tree.get_head(uid, job_id)["head"]["content"]


def _plan(uid, job_id, dry_run=False, **program):
    """`execute_plan` on top of the job's HEAD, as a host that read get_head would."""
    from harness import tree
    head = tree.get_head(uid, job_id)["head"]
    return _run(uid, {"job_id": job_id, "parent": head["node_id"] if head else None, **program},
                dry_run=dry_run)


# ── params, and the words that state one ─────────────────────────────────────

@pytest.mark.parametrize("kind,params", [
    ("limit", {"pages": 1}), ("limit", {"bullets_per_item": 3, "max_skills": 12}),
    ("limit", {"projects": 0}), ("section_order", {"order": ["skills", "experience"]}),
    ("style_note", {}),
])
def test_valid_params_are_normalized(kind, params):
    assert R.normalize_params(kind, params) == params


@pytest.mark.parametrize("kind,params", [
    ("limit", {}), ("limit", {"pages": 0}), ("limit", {"pages": 9}), ("limit", {"projects": 1}),
    ("limit", {"sections": 0}), ("limit", {"skills": 0, "max_skills": 5}),
    ("section_order", {"order": []}), ("section_order", {"order": ["skills", "skills"]}),
    ("section_order", {"order": ["hobbies"]}), ("style_note", {"tone": "formal"}),
    ("nope", {}),
])
def test_invalid_params_are_refused_with_the_shape(kind, params):
    with pytest.raises(R.RuleError):
        R.normalize_params(kind, params)


@pytest.mark.parametrize("text,target,polarity,expected", [
    ("Keep it to one page", None, None, {"pages": 1}),
    ("two pages is the most I want", None, None, {"pages": 2}),
    ("Never go over a single page", None, None, {"pages": 1}),
    ("No projects section", None, None, {"projects": 0}),
    ("Skip the education section", None, None, {"education": 0}),
    ("Leave off the work experience section", None, None, {"experience": 0}),
    ("Skip achievements", "section:achievements", "suppress", {"achievements": 0}),
    ("Keep it short", None, None, None),                          # no number to read
    ("One page and no projects section", None, None, None),       # two readings
    ("One page, or maybe two pages", None, None, None),
    ("Don't keep it to one page", None, None, None),              # negated
    ("Skip the projects I did in school", None, None, None),      # not the section
    ("Lead with projects", "section:projects", "emphasize", None),
])
def test_only_the_obvious_limits_are_read_from_words(text, target, polarity, expected):
    assert R.infer_limit(text, target, polarity) == expected


# ── scope precedence ─────────────────────────────────────────────────────────

def test_a_narrower_hard_rule_cannot_loosen_a_broader_one():
    rules = [_rule("a1", "limit", {"max_skills": 6}, text="Six skills, no more"),
             _rule("b2", "limit", {"max_skills": 10}, scope="role_family", value="research")]
    out = R.resolve(rules)
    assert out["limits"]["max_skills"]["hard"]["value"] == 6
    [over] = out["overridden"]
    assert over["rule"]["rule_id"] == "b2" and over["by"]["rule_id"] == "a1"
    assert "tighten" in over["reason"]


def test_a_narrower_hard_rule_may_tighten_a_broader_one():
    rules = [_rule("a1", "limit", {"pages": 2}), _rule("b2", "limit", {"pages": 1}, scope="job", value="j")]
    out = R.resolve(rules)
    assert out["limits"]["pages"]["hard"]["value"] == 1 and not out["overridden"]


def test_a_narrower_soft_rule_overrides_a_broader_soft_one_either_way():
    rules = [_rule("a1", "limit", {"max_skills": 6}, strength=3),
             _rule("b2", "limit", {"max_skills": 14}, strength=2, scope="role_family", value="r")]
    out = R.resolve(rules)
    assert out["limits"]["max_skills"]["soft"]["value"] == 14
    assert out["limits"]["max_skills"]["hard"] is None


def test_a_soft_rule_never_loosens_a_hard_one():
    rules = [_rule("a1", "limit", {"max_skills": 6}),
             _rule("b2", "limit", {"max_skills": 14}, strength=3, scope="job", value="j")]
    out = R.resolve(rules)
    assert out["limits"]["max_skills"]["soft"] is None
    assert out["overridden"][0]["reason"] == "a soft rule never loosens a hard one"


def test_a_narrower_hard_order_must_extend_the_broader_one():
    rules = [_rule("a1", "section_order", {"order": ["skills"]}),
             _rule("b2", "section_order", {"order": ["skills", "experience"]}, scope="role_family", value="r"),
             _rule("c3", "section_order", {"order": ["education"]}, scope="job", value="j")]
    out = R.resolve(rules)
    assert out["order"]["hard"]["value"] == ["skills", "experience"]
    assert [o["rule"]["rule_id"] for o in out["overridden"]] == ["c3"]


def test_scope_filter_is_the_preferences_filter():
    rules = [_rule("g", "limit", {"pages": 1}), _rule("f", "limit", {"pages": 2}, scope="role_family", value="Research"),
             _rule("j", "limit", {"pages": 2}, scope="job", value="job-1"),
             {**_rule("x", "limit", {"pages": 3}), "status": "retracted"}]
    ids = lambda family, job: [r["rule_id"] for r in R.in_scope(rules, job, family)]  # noqa: E731
    assert ids(None, None) == ["g"]
    assert ids("research", None) == ["f", "g"]
    assert ids("research", "job-1") == ["f", "g", "j"]
    assert ids("other", "job-2") == ["g"]


# ── the store: record, supersede, retract, never delete ──────────────────────

def test_record_rule_stores_validates_and_supersedes(env):
    uid, _, _ = env
    first = _record(uid, "Keep it to one page", params={"pages": 1})
    assert first["status"] == "stored" and first["rule"]["hard"] and first["rule"]["params"] == {"pages": 1}
    again = _record(uid, "Keep it to one page", params={"pages": 1})
    assert again["status"] == "already_recorded"
    changed = _record(uid, "Two pages is fine", params={"pages": 2}, strength=3)
    assert changed["status"] == "superseded" and changed["supersedes"] == "Keep it to one page"
    assert not changed["rule"]["hard"] and "soft" in changed["rule"]["enforcement"]

    rows = services.load_resume_rules(uid, include_inactive=True)
    assert [(r["status"], r["params"]) for r in rows] == [("superseded", {"pages": 1}), ("active", {"pages": 2})]
    assert rows[1]["supersedes_id"] == rows[0]["rule_id"]

    assert _record(uid, "x", params={"pages": 0})["status"] == "refused"
    assert _record(uid, "", params={"pages": 1})["status"] == "refused"
    assert _record(uid, "Be concise", kind="limit")["status"] == "refused"      # no plain limit in the words
    assert _record(uid, "Order", kind="section_order")["status"] == "refused"
    assert _record(uid, "Pages", params={"pages": 1}, strength=9)["status"] == "refused"
    assert _record(uid, "Pages", params={"pages": 1}, scope="job")["status"] == "refused"   # needs a job id
    assert _record(uid, "Pages", params={"pages": 1}, scope="role_family")["status"] == "refused"


def test_a_limit_with_no_params_is_read_from_plain_words(env):
    uid, _, _ = env
    out = tools.record_rule(uid, "No projects section please", "limit", None, 5)
    assert out["status"] == "stored" and out["rule"]["params"] == {"projects": 0}
    [row] = services.load_resume_rules(uid)
    assert row["text"] == "No projects section please" and row["strength"] == 5
    assert row["provenance"]["source"] == "host"


def test_retract_rule_keeps_the_history(env):
    uid, _, _ = env
    rule = _record(uid, "Keep it to one page", params={"pages": 1})["rule"]
    out = tools.retract_rule(uid, rule["rule_id"])
    assert out["status"] == "retracted" and out["rule"]["status"] == "retracted"
    assert services.load_resume_rules(uid) == []
    kept = services.load_resume_rules(uid, include_inactive=True)
    assert [(r["rule_id"], r["status"], r["text"]) for r in kept] == [
        (rule["rule_id"], "retracted", "Keep it to one page")]
    assert tools.retract_rule(uid, "nope")["status"] == "not_found"
    # a retracted rule is no longer enforced, and recording it again is a new row
    again = _record(uid, "Keep it to one page", params={"pages": 1})
    assert again["status"] == "stored"
    assert len(services.load_resume_rules(uid, include_inactive=True)) == 2


def test_record_rule_refuses_to_loosen_a_hard_rule_and_names_it(env):
    uid, job_id, _ = env
    hard = _record(uid, "One page, always", params={"pages": 1})["rule"]
    out = _record(uid, "Two pages for research roles", params={"pages": 2}, scope="role_family",
                  value="research")
    assert out["status"] == "refused"
    assert "One page, always" in out["reason"] and hard["rule_id"][:8] in out["reason"]
    assert out["blocked_by"]["rule_id"] == hard["rule_id"]
    soft = _record(uid, "Two pages here", params={"pages": 2}, strength=2, scope="job", value=job_id)
    assert soft["status"] == "refused"                      # a soft rule cannot loosen a hard one either
    tighter = _record(uid, "Two bullets a role", params={"bullets_per_item": 2}, scope="job", value=job_id)
    assert tighter["status"] == "stored"                    # another thing: nothing to loosen
    # soft over soft is allowed
    _record(uid, "Ten skills", params={"max_skills": 10}, strength=3)
    over = _record(uid, "Fifteen for this job", params={"max_skills": 15}, strength=3, scope="job", value=job_id)
    assert over["status"] == "stored"
    assert services.load_resume_rules(uid)[-1]["scope_type"] == "job"


# ── the briefing and the pins ────────────────────────────────────────────────

def test_the_briefing_and_pins_carry_hard_rules_verbatim_and_style_notes(env):
    uid, job_id, _ = env
    _record(uid, "Keep it to one page", params={"pages": 1})
    _record(uid, "Never more than 12 skills", params={"max_skills": 12})
    _record(uid, "Two bullets per project", params={"bullets_per_item": 2}, strength=2)
    _record(uid, "Plain, direct verbs; no buzzwords", kind="style_note", params={}, strength=4)
    _record(uid, "Only for this job: no projects section", params={"projects": 0}, scope="job", value=job_id)

    brief = tools.art_briefing(uid)
    assert [r["text"] for r in brief["rules"]["hard"]] == ["Keep it to one page", "Never more than 12 skills"]
    assert [r["text"] for r in brief["rules"]["soft"]] == ["Two bullets per project"]
    assert [n["text"] for n in brief["rules"]["notes"]] == ["Plain, direct verbs; no buzzwords"]
    assert brief["counts"]["page_rules"] == 4                     # the job's own is not in this scope

    with_job = tools.art_briefing(uid, job_id=job_id)
    assert "Only for this job: no projects section" in [r["text"] for r in with_job["rules"]["hard"]]

    pins = tools.art_pins(uid, job_id=job_id)
    assert {r["text"] for r in pins["rules"]} == {"Keep it to one page", "Never more than 12 skills",
                                                  "Only for this job: no projects section"}
    assert all(r["strength"] == 5 for r in pins["rules"])


# ── enforcement at finalize ──────────────────────────────────────────────────

def test_a_skills_limit_is_the_default_and_a_plan_may_not_loosen_it(isolated_engine, env):
    uid, job_id, _ = env
    rule = _record(uid, "Never more than 5 skills", params={"max_skills": 5})["rule"]

    loose = _plan(uid, job_id, dry_run=True, finalize={"max_skills": 9})
    assert not loose["committed"]
    [v] = [v for v in loose["violations"] if v["check"] == "rules"]
    assert v["rule_id"] == rule["rule_id"] and "Never more than 5 skills" in v["detail"]
    assert loose["rules"]["applied"][0]["param"] == "max_skills"

    out = _plan(uid, job_id)
    assert out["committed"] and out["violations"] == []
    assert len(_head_content(uid, job_id)["skills_ranked"]) == 5
    assert {"param": "max_skills", "value": 5}.items() <= out["rules"]["applied"][0].items()

    tighter = _plan(uid, job_id, dry_run=True, finalize={"max_skills": 4, "min_skills": 0},
                    skills=["Python", "SQL", "Docker"])
    assert not [v for v in tighter["violations"] if v["check"] == "rules"]


def test_a_pages_limit_sets_the_line_budget_and_names_the_rule_when_it_is_over(isolated_engine, env, monkeypatch):
    from harness import executor

    uid, job_id, _ = env
    monkeypatch.setattr(executor, "MEASURER", lambda texts: [3 for _ in texts])   # tall: overflows
    rule = _record(uid, "One page. No exceptions.", params={"pages": 1})["rule"]
    plan = _plan(uid, job_id, dry_run=True, finalize={"min_skills": 0})
    [budget] = [v for v in plan["violations"] if v["check"] == "line_budget"]
    assert plan["line_budget"]["budget"] == 60
    assert rule["rule_id"][:8] in budget["hint"] and "One page. No exceptions." in budget["hint"]

    # a looser line_budget than a hard rule allows is a rules violation, not a silent pass
    loose = _plan(uid, job_id, dry_run=True, finalize={"line_budget": 90})
    assert [v["rule_id"] for v in loose["violations"] if v["check"] == "rules"] == [rule["rule_id"]]

    # two pages as a hard rule widens the default
    tools.retract_rule(uid, rule["rule_id"])
    _record(uid, "Two pages at most", params={"pages": 2})
    wide = _plan(uid, job_id, dry_run=True, finalize={"min_skills": 0})
    assert wide["line_budget"]["budget"] == 120
    still = _plan(uid, job_id, dry_run=True, finalize={"line_budget": 150})
    assert still["violations"] and still["violations"][0]["check"] == "rules"


def test_a_section_order_is_the_default_and_a_different_one_is_refused(env):
    from agents.checks import expected_sections

    uid, job_id, _ = env
    rule = _record(uid, "Skills first, then experience", kind="section_order",
                   params={"order": ["skills", "experience"]})["rule"]

    out = _run(uid, {"job_id": job_id})
    assert out["committed"], out["violations"]
    order = _head_content(uid, job_id)["_section_order"]
    assert order[:2] == ["skills", "experience"] and sorted(order) == sorted(expected_sections(
        _head_content(uid, job_id)))

    expected = expected_sections(_head_content(uid, job_id))
    other = [s for s in expected if s != "skills"] + ["skills"]
    bad = _plan(uid, job_id, dry_run=True, section_order=other)
    [v] = [v for v in bad["violations"] if v["check"] == "rules"]
    assert v["rule_id"] == rule["rule_id"] and "Skills first, then experience" in v["detail"]
    assert "skills" in v["hint"]

    same = _plan(uid, job_id, dry_run=True, section_order=["skills", "experience"]
                 + [s for s in expected if s not in ("skills", "experience")])
    assert not [v for v in same["violations"] if v["check"] == "rules"]


def test_the_editor_layout_outranks_a_rule_order(isolated_engine, env):
    """The user's own drag (#277) is laid over the plan's order and the rules' default."""
    from database.models import UserJobResult
    from harness import tree

    uid, job_id, _ = env
    _record(uid, "Skills first", kind="section_order", params={"order": ["skills"]})
    first = _run(uid, {"job_id": job_id})
    assert first["committed"] and _head_content(uid, job_id)["_section_order"][0] == "skills"

    dragged = ["experience", "projects", "skills", "education"]
    with Session(isolated_engine) as s:
        row = s.exec(select(UserJobResult)).one()
        row.layout_overrides = {"section_order": dragged}
        s.add(row)
        s.commit()
    drag = tree.record_result(uid, job_id, source="editor", note="layout")
    out = _run(uid, {"job_id": job_id, "parent": drag["node_id"], "nodes": [
        {"id": "d", "op": "delete", "item_key": PROJ, "because": "user:less games"}]})
    assert out["committed"], out["violations"]
    assert not [v for v in out["violations"] if v["check"] == "rules"]
    assert _head_content(uid, job_id)["_section_order"][:3] == ["experience", "projects", "skills"]


def test_no_projects_section_removes_it_and_refuses_nodes_in_it(env):
    uid, job_id, _ = env
    rule = _record(uid, "No projects section", params={"projects": 0})["rule"]
    out = _run(uid, {"job_id": job_id, "nodes": [
        {"id": "p", "op": "keep", "item_key": PROJ},
        {"id": "e", "op": "keep", "item_key": EXP}]})
    status = {n["id"]: n for n in out["nodes"]}
    assert status["p"]["status"] == "refused" and status["p"]["reason"].startswith("page_rule:")
    assert "No projects section" in status["p"]["reason"]
    assert status["e"]["status"] == "kept"
    assert out["committed"] and out["violations"] == []
    content = _head_content(uid, job_id)
    assert content["projects"] == [] and content["_excluded_sections"] == ["projects"]
    assert content["experiences"]
    assert out["rules"]["excluded_sections"] == ["projects"]
    assert {a["rule_id"] for a in out["rules"]["applied"]} == {rule["rule_id"]}

    # a plan cannot bring it back
    back = _plan(uid, job_id, dry_run=True, finalize={"keep_sections": ["projects"]})
    [v] = [v for v in back["violations"] if v["check"] == "rules"]
    assert v["rule_id"] == rule["rule_id"] and "keep_sections" in v["detail"]


def test_retracting_an_exclusion_brings_the_section_back(env):
    uid, job_id, _ = env
    rule = _record(uid, "No projects section", params={"projects": 0})["rule"]
    assert _run(uid, {"job_id": job_id})["committed"]
    assert _head_content(uid, job_id)["projects"] == []
    tools.retract_rule(uid, rule["rule_id"])
    out = _plan(uid, job_id)
    assert out["committed"], out
    content = _head_content(uid, job_id)
    assert content["projects"] and "_excluded_sections" not in content


def test_a_soft_exclusion_applies_by_default_and_a_plan_may_keep_the_section(env):
    uid, job_id, _ = env
    rule = _record(uid, "I'd rather not show projects", params={"projects": 0}, strength=3)["rule"]
    default = _plan(uid, job_id, dry_run=True)
    assert default["violations"] == [] and default["rules"]["excluded_sections"] == ["projects"]
    assert {"rule_id": rule["rule_id"], "status": "applied"}.items() <= [
        s for s in default["soft_preferences"] if s["source"] == "rule"][0].items()

    kept = _plan(uid, job_id, finalize={"keep_sections": ["projects"]})
    assert kept["committed"] and kept["violations"] == []
    soft = [s for s in kept["soft_preferences"] if s["source"] == "rule"][0]
    assert soft["status"] == "not_applied" and "keep_sections" in soft["reason"]
    assert _head_content(uid, job_id)["projects"]


def test_an_excluded_education_section_is_left_off_the_rendered_page(env):
    from harness import render

    uid, job_id, _ = env
    from harness.executor import kg_default_content

    heading = "\\section{Education}"
    assert heading in render.generated_tex(uid, kg_default_content(uid, ""))
    _record(uid, "Leave education off", params={"education": 0})
    assert _run(uid, {"job_id": job_id})["committed"]
    content = _head_content(uid, job_id)
    assert content["_excluded_sections"] == ["education"] and "education" not in content
    assert heading not in render.generated_tex(uid, content)


def test_bullets_per_item_is_a_hard_check_with_a_cut_hint(env):
    uid, job_id, _ = env
    rule = _record(uid, "Three bullets per role at most", params={"bullets_per_item": 1})["rule"]
    out = _run(uid, {"job_id": job_id}, dry_run=True)
    mine = [v for v in out["violations"] if v["check"] == "rules"]
    assert mine and all(v["rule_id"] == rule["rule_id"] for v in mine)
    assert "delete_bullet" in mine[0]["hint"] and "bullets" in mine[0]["detail"]
    assert not out["committed"]


# ── style notes never block ──────────────────────────────────────────────────

def test_a_style_note_never_blocks_and_is_shown_verbatim(env):
    uid, job_id, _ = env
    note = _record(uid, "Never use first person. Keep a formal tone.", kind="style_note", params={}, strength=5)
    assert note["status"] == "stored" and not note["rule"]["hard"]
    assert "never blocks" in note["rule"]["enforcement"]
    out = _run(uid, {"job_id": job_id})
    assert out["committed"] and out["violations"] == []
    assert [n["text"] for n in out["rules"]["style_notes"]] == ["Never use first person. Keep a formal tone."]
    assert [n["text"] for n in tools.art_briefing(uid)["rules"]["notes"]] == [
        "Never use first person. Keep a formal tone."]


# ── hard pins reach every field on the page ──────────────────────────────────

def _page(**kw):
    return {"experiences": [{"title": "Engineer", "company": "Acme", "bullets": ["Built things"]}], **kw}


@pytest.mark.parametrize("content,where", [
    (_page(education=[{"institution": "Kafka Institute", "degree": "B.S."}]), "education"),
    (_page(education=[{"institution": "State U", "degree": "B.S. Kafka Studies"}]), "education"),
    (_page(achievements=[{"title": "Kafka Hackathon Winner", "issuer": "Acme"}]), "achievements"),
    (_page(achievements=[{"title": "Winner", "description": "Built on Kafka"}]), "achievements"),
    (_page(skills_ranked=[{"name": "Apache Kafka", "category": "Data"}]), "skills"),
    (_page(summary="Engineer with Kafka experience"), "summary"),
])
def test_a_negative_pin_term_is_caught_outside_experiences_and_projects(content, where):
    from harness.acceptance import negative_pin_violations

    found = negative_pin_violations(content, ["kafka"])
    assert len(found) == 1 and found[0].startswith(f"negative_pin:kafka@{where} ::"), found


def test_nothing_is_flagged_when_the_term_is_absent():
    from harness.acceptance import negative_pin_violations

    content = _page(education=[{"institution": "State U", "degree": "B.S."}],
                    achievements=[{"title": "Winner"}], skills_ranked=[{"name": "Python"}], summary="Builds.")
    assert negative_pin_violations(content, ["kafka"]) == []
    assert negative_pin_violations(content, []) == []


def test_a_hard_pin_in_education_text_blocks_the_commit_with_a_useful_hint(isolated_engine, env):
    uid, job_id, _ = env
    with Session(isolated_engine) as s:
        s.add(UserPreference(user_id=uid, text="Never mention City University", polarity="suppress",
                             target_type="topic", target_term="city university", scope_type="global",
                             strength=5, status="active"))
        s.commit()
    out = _run(uid, {"job_id": job_id}, dry_run=True)
    hits = [v for v in out["violations"] if "@education" in str(v["detail"])]
    assert hits and hits[0]["check"] == "preferences"
    assert "knowledge graph" in hits[0]["hint"] and "plan node" in hits[0]["hint"]
    assert not out["committed"]


def test_a_hard_pin_in_achievement_text_blocks_the_commit(isolated_engine, env):
    from harness import ingest

    uid, job_id, _ = env
    ingest.upsert_items(uid, [{"kind": "achievement",
                               "data": {"title": "Hackathon Grand Prize", "issuer": "Acme Labs"}}])
    with Session(isolated_engine) as s:
        s.add(UserPreference(user_id=uid, text="Never mention hackathons", polarity="suppress",
                             target_type="topic", target_term="hackathon", scope_type="global",
                             strength=5, status="active"))
        s.commit()
    out = _run(uid, {"job_id": job_id}, dry_run=True)
    assert any("@achievements" in str(v["detail"]) for v in out["violations"])
    # excluding the section is how the user can honour the pin without editing their graph
    _record(uid, "Leave achievements off", params={"achievements": 0})
    fixed = _run(uid, {"job_id": job_id}, dry_run=True)
    assert not any("@achievements" in str(v["detail"]) for v in fixed["violations"])


# ── soft preferences are reported, never blocking ────────────────────────────

def test_soft_preferences_are_reported_applied_or_not(isolated_engine, env):
    uid, job_id, _ = env
    with Session(isolated_engine) as s:
        p = UserPreference(user_id=uid, text="Prefer not to show Pixel Adventure", polarity="suppress",
                           target_type="project", target_key=PROJ, scope_type="global", strength=3,
                           status="active")
        s.add(p)
        s.commit()
        pid = str(p.preference_id)
    kept = _run(uid, {"job_id": job_id}, dry_run=True)
    [soft] = [s for s in kept["soft_preferences"] if s["source"] == "preference"]
    assert soft["preference_id"] == pid and soft["status"] == "not_applied" and kept["violations"] == []
    dropped = _run(uid, {"job_id": job_id, "nodes": [
        {"id": "d", "op": "delete", "item_key": PROJ, "because": f"pref:{pid}"}]})
    [soft] = [s for s in dropped["soft_preferences"] if s["source"] == "preference"]
    assert soft["status"] == "applied" and dropped["committed"]


# ── record_preference routes a page subject ──────────────────────────────────

def test_record_preference_routes_a_plain_page_limit_to_a_rule(env):
    from harness import memory

    uid, _, _ = env
    out = memory.record_preference(uid, "Keep it to one page", "suppress", "page length", 5)
    assert out["status"] == "stored" and out["anchor"]["status"] == "document"
    assert out["rule"]["kind"] == "limit" and out["rule"]["params"] == {"pages": 1} and out["rule"]["hard"]
    assert out["pinned"] and not out.get("preference_id")
    assert [r["params"] for r in services.load_resume_rules(uid)] == [{"pages": 1}]
    assert services.load_preferences(uid) == []

    section = memory.record_preference(uid, "No projects section", "suppress", "section: projects", 4)
    assert section["rule"]["params"] == {"projects": 0} and section["status"] == "stored"


def test_record_preference_sends_a_loose_page_subject_back_with_nothing_stored(env):
    from harness import memory

    uid, _, _ = env
    out = memory.record_preference(uid, "Keep a formal tone", "reframe", "formal tone", 4)
    assert out["status"] == "refused" and out["anchor"]["status"] == "document"
    assert out["route"]["tool"] == "record_rule" and "style_note" in out["route"]["kinds"]
    assert "limit" in out["route"]["shapes"] and "record_rule" in out["reason"]
    assert services.load_resume_rules(uid) == [] and services.load_preferences(uid) == []

    emphasize = memory.record_preference(uid, "Lead with projects", "emphasize", "section: projects", 3)
    assert emphasize["status"] == "refused" and emphasize["route"]


def test_a_routed_rule_that_would_loosen_a_hard_one_is_refused(env):
    from harness import memory

    uid, _, _ = env
    _record(uid, "One page", params={"pages": 1})
    out = memory.record_preference(uid, "two pages for research roles", "suppress", "page length", 5,
                                   scope="role_family", scope_value="research")
    assert out["status"] == "refused" and "One page" in out["reason"]
    assert [r["params"] for r in services.load_resume_rules(uid)] == [{"pages": 1}]


# ── rule_violations, on content alone ────────────────────────────────────────

def test_rule_violations_reads_hard_rules_only():
    from harness.acceptance import rule_violations

    hard = R.resolve([_rule("h1", "limit", {"bullets_per_item": 1, "achievements": 0})])
    soft = R.resolve([_rule("s1", "limit", {"bullets_per_item": 1, "achievements": 0}, strength=3)])
    content = {"experiences": [{"title": "Eng", "company": "Acme", "bullets": ["a", "b", "c"]}],
               "achievements": [{"title": "Prize"}]}
    found = rule_violations(content, hard)
    assert {v["rule_id"] for v in found} == {"h1"} and len(found) == 2
    assert any("achievements section" in v["detail"] for v in found)
    assert any("exp:eng|acme has 3 bullets" in v["detail"] for v in found)
    assert rule_violations(content, soft) == []
    assert rule_violations(content, R.resolve([])) == []
    assert rule_violations({"experiences": [{"title": "Eng", "company": "Acme", "bullets": ["a"]}]}, hard) == []


# ── the table is its own, and old data loads ─────────────────────────────────

def test_with_no_rules_the_defaults_and_the_result_are_what_they_were(env):
    from harness.acceptance import rule_violations

    uid, job_id, _ = env
    out = _run(uid, {"job_id": job_id}, dry_run=True)
    assert "rules" not in out and out["violations"] == [] and out["line_budget"]["budget"] == 60
    assert not out.get("soft_preferences")
    assert rule_violations({"experiences": []}, R.resolve([])) == []
    eff = R.effective_finalize({"line_budget": None, "max_skills": None, "min_skills": None,
                                "max_bullet_lines": None, "bullets_per_item": None,
                                "keep_sections": []}, R.resolve([]))
    assert eff["values"] == {"line_budget": 60.0, "max_skills": 18, "min_skills": 8,
                             "max_bullet_lines": 2, "bullets_per_item": None}
    assert eff["excluded"] == {} and eff["violations"] == [] and eff["errors"] == []


def test_a_finalize_past_the_built_in_default_is_still_an_invalid_program(env):
    uid, job_id, _ = env
    out = _run(uid, {"job_id": job_id, "finalize": {"line_budget": 90}}, dry_run=True)
    assert out["error"]["code"] == "invalid_program" and "line_budget" in out["error"]["message"]


def test_resume_rule_rows_are_a_new_table_not_a_preference(isolated_engine, env):
    uid, _, _ = env
    _record(uid, "One page", params={"pages": 1})
    with Session(isolated_engine) as s:
        assert len(s.exec(select(ResumeRule)).all()) == 1
        assert s.exec(select(UserPreference)).all() == []


# ── soft limits are never a gate ─────────────────────────────────────────────

def test_soft_limits_apply_where_they_can_and_are_otherwise_only_measured(env, monkeypatch):
    from harness import executor

    uid, job_id, _ = env
    monkeypatch.setattr(executor, "MEASURER",
                        lambda texts: [2 if i == 0 else 1 for i, _ in enumerate(texts)])
    _record(uid, "About six skills", params={"max_skills": 6}, strength=3)
    _record(uid, "Bullets of one line", params={"max_bullet_lines": 1}, strength=2)
    _record(uid, "One bullet per role", params={"bullets_per_item": 1}, strength=2)

    out = _plan(uid, job_id, dry_run=True)
    assert out["violations"] == [], out["violations"]              # nothing blocks
    soft = {s["param"]: s for s in out["soft_preferences"] if s["source"] == "rule"}
    assert soft["max_skills"]["status"] == "applied"               # the default list was cut to it
    assert soft["max_bullet_lines"]["status"] == "not_applied" and "above" in soft["max_bullet_lines"]["reason"]
    assert soft["bullets_per_item"]["status"] == "not_applied"
    assert out["line_budget"]["budget"] == 60

    # a plan that lists its own, longer skills is judged on them and still not blocked
    own = _plan(uid, job_id, dry_run=True, skills=["Python", "SQL", "Docker", "Airflow", "Kafka",
                                                   "AWS", "Redis", "Postgres", "Flask"])
    assert own["violations"] == []
    assert {s["param"]: s["status"] for s in own["soft_preferences"]
            if s["source"] == "rule"}["max_skills"] == "not_applied"


def test_a_soft_page_limit_that_widens_the_built_in_default_moves_it(env):
    uid, job_id, _ = env
    _record(uid, "Two pages are fine", params={"pages": 2}, strength=3)
    out = _plan(uid, job_id, dry_run=True)
    assert out["line_budget"]["budget"] == 120 and out["violations"] == []
    [soft] = [s for s in out["soft_preferences"] if s["param"] == "pages"]
    assert soft["status"] == "applied"
    assert [a["param"] for a in out["rules"]["applied"]] == ["pages"]
