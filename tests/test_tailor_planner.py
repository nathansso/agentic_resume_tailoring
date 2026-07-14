"""
Typed tailoring action plan (issues #91 / #51 Phase 2).

Covers: planner validation and deterministic fallback, structural plan
application to the generator inputs, plan enforcement on the LLM output, and
the persisted decision log on UserJobResult.
"""
import json

from sqlmodel import Session

from agents.tailor_planner import (
    DEFAULT_KNOBS,
    REVISION_STRATEGIES,
    TailorPlanner,
    decision_log_entry,
)
from database.models import Experience, JobDescription, Project, UserJobResult


ITEMS = [
    {"key": "exp:ml engineer|nimbus", "section": "experience",
     "label": "ML Engineer", "source_text": "Built ranking models",
     "suggested_keywords": ["python"]},
    {"key": "exp:swe|harbor", "section": "experience",
     "label": "SWE", "source_text": "Dashboards and ETL",
     "suggested_keywords": []},
    {"key": "proj:recipe review", "section": "project",
     "label": "Recipe Review", "source_text": "Random forest pipeline",
     "suggested_keywords": ["sklearn"]},
]
POOL = [
    {"key": "proj:diginetica", "section": "project", "label": "Diginetica",
     "source_text": "GNN vs GBM benchmark", "relevance": 0.9},
]


class _FakeLLM:
    """Returns a canned string from .invoke(), like a chat model."""

    def __init__(self, content: str):
        self.content_str = content
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        from types import SimpleNamespace
        return SimpleNamespace(content=self.content_str)


# ── default plan ──────────────────────────────────────────────────────────────


def test_default_plan_covers_every_item():
    actions = TailorPlanner.default_plan(ITEMS)
    assert [a["item_key"] for a in actions] == [i["key"] for i in ITEMS]
    assert all(a["op"] == "revise" for a in actions)
    # Items with assigned keywords weave them; items without tighten.
    by_key = {a["item_key"]: a for a in actions}
    assert by_key["exp:ml engineer|nimbus"]["strategy"] == "keyword_weave"
    assert by_key["exp:ml engineer|nimbus"]["keywords"] == ["python"]
    assert by_key["exp:swe|harbor"]["strategy"] == "tighten"


def test_default_plan_never_deletes_or_replaces():
    actions = TailorPlanner.default_plan(ITEMS)
    assert all(a["op"] not in ("delete", "replace") for a in actions)


# ── validation ────────────────────────────────────────────────────────────────


def test_validate_plan_drops_unknown_keys_and_fills_missing():
    raw = [
        {"item_key": "proj:not-a-real-item", "op": "delete"},
        {"item_key": "exp:ml engineer|nimbus", "op": "revise",
         "strategy": "quantify", "rationale": "metrics present"},
        "not-a-dict",
    ]
    actions = TailorPlanner.validate_plan(raw, ITEMS, POOL)
    assert [a["item_key"] for a in actions] == [i["key"] for i in ITEMS]
    by_key = {a["item_key"]: a for a in actions}
    assert by_key["exp:ml engineer|nimbus"]["strategy"] == "quantify"
    # Uncovered items got the safe default action.
    assert by_key["exp:swe|harbor"]["op"] == "revise"


def test_validate_plan_coerces_unknown_op_and_strategy():
    raw = [
        {"item_key": "exp:ml engineer|nimbus", "op": "obliterate"},
        {"item_key": "proj:recipe review", "op": "revise", "strategy": "vibes"},
    ]
    by_key = {a["item_key"]: a for a in TailorPlanner.validate_plan(raw, ITEMS, POOL)}
    assert by_key["exp:ml engineer|nimbus"]["op"] == "revise"
    assert by_key["proj:recipe review"]["strategy"] in REVISION_STRATEGIES


def test_validate_plan_replace_rules():
    raw = [
        # replace on an experience is not allowed
        {"item_key": "exp:ml engineer|nimbus", "op": "replace",
         "replacement_key": "proj:diginetica"},
        # replacement_key not in the pool degrades to revise
        {"item_key": "proj:recipe review", "op": "replace",
         "replacement_key": "proj:imaginary"},
    ]
    by_key = {a["item_key"]: a for a in TailorPlanner.validate_plan(raw, ITEMS, POOL)}
    assert by_key["exp:ml engineer|nimbus"]["op"] == "revise"
    assert by_key["proj:recipe review"]["op"] == "revise"

    valid = [{"item_key": "proj:recipe review", "op": "replace",
              "replacement_key": "proj:diginetica", "rationale": "more rigorous"}]
    by_key = {a["item_key"]: a for a in TailorPlanner.validate_plan(valid, ITEMS, POOL)}
    assert by_key["proj:recipe review"]["op"] == "replace"
    assert by_key["proj:recipe review"]["replacement_key"] == "proj:diginetica"


def test_validate_plan_refuses_to_empty_a_section():
    raw = [
        {"item_key": "exp:ml engineer|nimbus", "op": "delete"},
        {"item_key": "exp:swe|harbor", "op": "delete"},
    ]
    actions = TailorPlanner.validate_plan(raw, ITEMS, POOL)
    exp_ops = [a["op"] for a in actions if a["section"] == "experience"]
    assert "keep" in exp_ops  # one delete was coerced back
    assert exp_ops.count("delete") == 1


def test_validate_plan_knobs_disable_ops():
    raw = [
        {"item_key": "exp:ml engineer|nimbus", "op": "delete"},
        {"item_key": "proj:recipe review", "op": "replace",
         "replacement_key": "proj:diginetica"},
    ]
    knobs = {**DEFAULT_KNOBS, "allow_delete": False, "allow_replace": False}
    by_key = {a["item_key"]: a for a in TailorPlanner.validate_plan(raw, ITEMS, POOL, knobs)}
    assert by_key["exp:ml engineer|nimbus"]["op"] == "keep"
    assert by_key["proj:recipe review"]["op"] == "revise"


# ── plan(): LLM path and fallback ────────────────────────────────────────────


def test_plan_falls_back_when_llm_fails():
    planner = TailorPlanner(llm=object())  # no .invoke → raises → fallback
    plan = planner.plan(ITEMS, POOL, "jd text", ["Excel"])
    assert plan["planner"] == "default"
    assert len(plan["actions"]) == len(ITEMS)


def test_plan_uses_valid_llm_output():
    payload = json.dumps([
        {"item_key": "proj:recipe review", "op": "replace",
         "replacement_key": "proj:diginetica", "rationale": "stronger fit"},
    ])
    planner = TailorPlanner(llm=_FakeLLM(f"```json\n{payload}\n```"))
    plan = planner.plan(ITEMS, POOL, "jd text", [], revision_notes="swap the recipe project")
    assert plan["planner"] == "llm"
    by_key = {a["item_key"]: a for a in plan["actions"]}
    assert by_key["proj:recipe review"]["op"] == "replace"
    # every other item still got exactly one action
    assert len(plan["actions"]) == len(ITEMS)


def test_plan_empty_items_short_circuits():
    llm = _FakeLLM("[]")
    plan = TailorPlanner(llm=llm).plan([], POOL, "jd", [])
    assert plan["actions"] == []
    assert llm.calls == 0


# ── structural application to generator inputs ───────────────────────────────


_EXPS = [
    {"title": "ML Engineer", "company": "Nimbus", "bullets": ["built models", "shipped it"],
     "bullet_budget": 2, "suggested_keywords": ["python"]},
    {"title": "SWE", "company": "Harbor", "bullets": ["dashboards"],
     "bullet_budget": 2, "suggested_keywords": []},
]
_PROJS = [
    {"name": "Recipe Review", "description": "rf pipeline", "blurbs": {},
     "suggested_keywords": ["sklearn"], "selection_score": 0.4},
]
_PROJ_POOL = [
    {"name": "Diginetica", "description": "gnn benchmark", "blurbs": {},
     "selection_score": 0.9, "repo_url": "https://github.com/x/diginetica"},
]


def _plan_with(actions):
    return {"actions": actions, "knobs": dict(DEFAULT_KNOBS), "planner": "llm"}


def test_apply_plan_delete_and_replace():
    from agents.tailor import ResumeTailorAgent

    plan = _plan_with([
        {"section": "experience", "item_key": "exp:swe|harbor", "op": "delete",
         "rationale": "irrelevant to this JD"},
        {"section": "project", "item_key": "proj:recipe review", "op": "replace",
         "replacement_key": "proj:diginetica", "rationale": "more rigorous"},
    ])
    assignments = {"exp:swe|harbor": ["excel"], "exp:ml engineer|nimbus": ["python"]}

    exps, projs, kept_assignments = ResumeTailorAgent._apply_plan_to_inputs(
        plan, list(_EXPS), list(_PROJS), list(_PROJ_POOL), assignments, {}
    )

    assert [e["title"] for e in exps] == ["ML Engineer"]
    assert [p["name"] for p in projs] == ["Diginetica"]
    assert projs[0]["plan_op"] == "revise"          # replacement arrives as a revise
    assert projs[0]["plan_rationale"] == "more rigorous"
    # assignments for removed items are dropped; survivors keep theirs
    assert "exp:swe|harbor" not in kept_assignments
    assert kept_assignments["exp:ml engineer|nimbus"] == ["python"]


def test_apply_plan_keep_project_carries_prior_bullets():
    from agents.tailor import ResumeTailorAgent

    plan = _plan_with([
        {"section": "project", "item_key": "proj:recipe review", "op": "keep",
         "rationale": "user liked it"},
    ])
    prior = {"projects": [{"name": "Recipe Review", "bullets": ["prior bullet"]}]}

    _, projs, _ = ResumeTailorAgent._apply_plan_to_inputs(
        plan, [], list(_PROJS), [], {}, prior
    )
    assert projs[0]["plan_op"] == "keep"
    assert projs[0]["prior_bullets"] == ["prior bullet"]


# ── enforcement on generated output ──────────────────────────────────────────


def test_enforce_plan_drops_deleted_and_restores_kept():
    from agents.tailor import ResumeTailorAgent

    state = {
        "plan": _plan_with([
            {"section": "experience", "item_key": "exp:swe|harbor", "op": "delete"},
            {"section": "experience", "item_key": "exp:ml engineer|nimbus", "op": "keep"},
            {"section": "project", "item_key": "proj:recipe review", "op": "keep"},
        ]),
        # generator inputs after plan application: SWE was removed
        "experiences": [
            {"title": "ML Engineer", "company": "Nimbus", "plan_op": "keep",
             "bullets": ["built models", "shipped it"], "bullet_budget": 2},
        ],
        "projects": [
            {"name": "Recipe Review", "plan_op": "keep",
             "prior_bullets": ["prior bullet"]},
        ],
    }
    tailored = {
        "experiences": [
            # LLM re-added the deleted experience and rewrote the kept one
            {"title": "SWE", "company": "Harbor", "bullets": ["sneaky comeback"]},
            {"title": "ML Engineer", "company": "Nimbus",
             "bullets": ["fully rewritten bullet"]},
        ],
        "projects": [
            {"name": "Recipe Review", "bullets": ["fully rewritten project"]},
        ],
    }

    out = ResumeTailorAgent._enforce_plan(tailored, state)

    titles = [e["title"] for e in out["experiences"]]
    assert "SWE" not in titles
    kept = next(e for e in out["experiences"] if e["title"] == "ML Engineer")
    assert kept["bullets"] == ["built models", "shipped it"]
    assert out["projects"][0]["bullets"] == ["prior bullet"]


def test_enforce_plan_noop_without_actions():
    from agents.tailor import ResumeTailorAgent

    tailored = {"experiences": [{"title": "X", "company": "Y", "bullets": ["b"]}]}
    out = ResumeTailorAgent._enforce_plan(dict(tailored), {"plan": {}})
    assert out == tailored


# ── decision log ──────────────────────────────────────────────────────────────


def test_decision_log_entry_shape():
    plan = _plan_with([{"section": "project", "item_key": "proj:x", "op": "revise",
                        "strategy": "tighten", "keywords": [], "propensity": 1.0}])
    evaluation = {"ats_breakdown": {
        "composite": 71.0, "baseline_composite": 50.0, "delta": 21.0,
        "skill_coverage": {"score": 80.0}, "keyword_coverage": {"score": 55.0},
    }}
    entry = decision_log_entry(plan, {"n_projects": 1}, evaluation, "emphasize python")

    assert entry["reward"]["delta"] == 21.0
    assert entry["reward"]["skill_coverage"] == 80.0
    assert entry["actions"][0]["propensity"] == 1.0
    assert entry["knobs"] == DEFAULT_KNOBS
    assert entry["context"] == {"n_projects": 1}
    assert entry["revision_notes"] == "emphasize python"
    assert entry["timestamp"]


def test_tailor_appends_decision_log(isolated_engine, monkeypatch):
    """ResumeTailorAgent.tailor() persists a decision-log entry per run, even
    on the offline fallback path (no LLM)."""
    import agents.formatter as fmt_module
    import agents.tailor as tailor_module
    from conftest import _seed_user_and_skill

    monkeypatch.setattr(tailor_module, "engine", isolated_engine)
    monkeypatch.setattr(tailor_module, "get_llm", lambda *a, **kw: object())
    monkeypatch.setattr(
        fmt_module.ResumeFormatterAgent, "fit_content_to_one_page",
        lambda self, content, section_order=None: content,
    )

    user = _seed_user_and_skill(isolated_engine)
    with Session(isolated_engine) as session:
        job = JobDescription(title="MLE", company="Lab", status="analyzed",
                             description="Python machine learning models pipelines")
        session.add(job)
        session.add(Experience(user_id=user.user_id, title="ML Engineer",
                               company="Nimbus", description="ml systems",
                               bullets=["Built Python models", "Deployed pipelines"]))
        session.add(Project(user_id=user.user_id, name="Recipe Review",
                            description="Random forest popularity model"))
        session.commit()
        job_id = job.job_id

        result = UserJobResult(user_id=user.user_id, job_id=job_id)
        session.add(result)
        session.commit()
        result_id = result.result_id

    agent = tailor_module.ResumeTailorAgent()

    generated = {
        "experiences": [{"title": "ML Engineer", "company": "Nimbus",
                         "bullets": ["Built Python models"]}],
        "projects": [{"name": "Recipe Review", "bullets": ["rf model"]}],
        "skills_emphasized": ["Python"],
    }

    class FakeGraph:
        def invoke(self, state):
            return {**state, "tailored_content": generated,
                    "evaluation": {"ats_breakdown": {"composite": 60.0, "delta": 10.0}},
                    "best_content": generated,
                    "best_evaluation": {"ats_breakdown": {"composite": 60.0, "delta": 10.0}},
                    "best_score": 60.0, "attempt": 1, "done": True}

    agent.graph = FakeGraph()
    agent.tailor(user.user_id, job_id, result_id)

    with Session(isolated_engine) as session:
        stored = session.get(UserJobResult, result_id)
        log = stored.tailoring_decisions
        if isinstance(log, str):
            log = json.loads(log)

    assert isinstance(log, list) and len(log) == 1
    entry = log[0]
    assert entry["planner"] == "default"        # object() LLM → fallback plan
    assert entry["reward"]["composite"] == 60.0
    assert entry["context"]["n_experiences"] == 1
    assert entry["context"]["attempts"] == 1
    # every generator input item received an action
    assert {a["op"] for a in entry["actions"]} == {"revise"}


def test_second_tailor_run_appends_not_overwrites(isolated_engine, monkeypatch):
    """Re-tailoring appends a second decision-log entry and marks it a revision."""
    import agents.formatter as fmt_module
    import agents.tailor as tailor_module
    from conftest import _seed_user_and_skill

    monkeypatch.setattr(tailor_module, "engine", isolated_engine)
    monkeypatch.setattr(tailor_module, "get_llm", lambda *a, **kw: object())
    monkeypatch.setattr(
        fmt_module.ResumeFormatterAgent, "fit_content_to_one_page",
        lambda self, content, section_order=None: content,
    )

    user = _seed_user_and_skill(isolated_engine)
    with Session(isolated_engine) as session:
        job = JobDescription(title="MLE", company="Lab", status="analyzed",
                             description="Python machine learning")
        session.add(job)
        session.add(Experience(user_id=user.user_id, title="ML Engineer",
                               company="Nimbus", description="ml",
                               bullets=["Built Python models"]))
        session.commit()
        job_id = job.job_id
        result = UserJobResult(user_id=user.user_id, job_id=job_id)
        session.add(result)
        session.commit()
        result_id = result.result_id

    agent = tailor_module.ResumeTailorAgent()
    generated = {
        "experiences": [{"title": "ML Engineer", "company": "Nimbus",
                         "bullets": ["Built Python models"]}],
        "projects": [], "skills_emphasized": [],
    }

    class FakeGraph:
        def invoke(self, state):
            return {**state, "tailored_content": generated, "evaluation": {},
                    "best_content": generated, "best_evaluation": {},
                    "best_score": 50.0, "attempt": 1, "done": True}

    agent.graph = FakeGraph()
    agent.tailor(user.user_id, job_id, result_id)
    agent.tailor(user.user_id, job_id, result_id,
                 revision_notes="emphasize python more")

    with Session(isolated_engine) as session:
        stored = session.get(UserJobResult, result_id)
        log = stored.tailoring_decisions
        if isinstance(log, str):
            log = json.loads(log)

    assert len(log) == 2
    assert log[0]["context"]["is_revision"] is False
    assert log[1]["context"]["is_revision"] is True
    assert log[1]["revision_notes"] == "emphasize python more"
