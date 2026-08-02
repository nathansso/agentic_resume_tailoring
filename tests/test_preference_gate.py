"""The preference gate on the planner and the pipeline (issue #129).

Where a preference stops being a suggestion. `render_constraints` puts the same
information in the planning prompt, but ImplexConv finding 2 is that a model can
retrieve the invalidating fact and still reason past it, so these tests are
about the deterministic override — not about whether the prompt persuades.
"""
from uuid import uuid4

from sqlmodel import Session

import services
from agents.arbitration import compile_constraints
from agents.preferences import compile_preferences
from agents.tailor import ResumeTailorAgent
from agents.tailor_planner import TailorPlanner, apply_constraints, decision_log_entry
from database.models import User


ITEMS = [
    {"key": "exp:barista|coffee co", "section": "experience", "label": "Barista",
     "suggested_keywords": ["service"]},
    {"key": "exp:intern|acme", "section": "experience", "label": "Intern",
     "suggested_keywords": ["python"]},
    {"key": "proj:recipe app", "section": "project", "label": "Recipe App",
     "suggested_keywords": ["react"]},
    {"key": "proj:diginetica", "section": "project", "label": "Diginetica",
     "suggested_keywords": ["pytorch"]},
]


def _constraint(**kwargs):
    base = {
        "preference_id": "p1",
        "text": "Do not include the Recipe App.",
        "polarity": "suppress",
        "target_key": "proj:recipe app",
        "target_term": "Recipe App",
        "strength": 3,
    }
    base.update(kwargs)
    return base


def _set(*applied):
    return {"applied": list(applied), "conflicts": [], "refused": []}


def _plan(items=None):
    return TailorPlanner.default_plan(items or ITEMS)


def _op(actions, key):
    return next(a["op"] for a in actions if a["item_key"] == key)


# ── the gate ─────────────────────────────────────────────────────────────────

def test_an_empty_constraint_set_leaves_the_plan_untouched():
    """Backward compatibility, stated precisely: with no preferences the plan is
    identical, which is what "absent profile reproduces today's behavior" means
    for a pipeline whose generator is an LLM."""
    plan = _plan()
    gated, enforcement = apply_constraints(plan, {"applied": [], "conflicts": [],
                                                  "refused": []}, ITEMS)
    assert gated == _plan()
    assert enforcement == {}


def test_a_suppression_forces_a_delete():
    gated, enforcement = apply_constraints(_plan(), _set(_constraint()), ITEMS)
    assert _op(gated, "proj:recipe app") == "delete"
    assert _op(gated, "proj:diginetica") == "revise"
    assert enforcement["changed"][0]["to_op"] == "delete"


def test_a_suppression_overrides_an_llm_plan_that_kept_the_item():
    """The planner proposing `keep` is exactly the case the gate exists for."""
    raw = [{"item_key": i["key"], "op": "keep", "rationale": "looks good"} for i in ITEMS]
    validated = TailorPlanner.validate_plan(raw, ITEMS, [])
    gated, _ = apply_constraints(validated, _set(_constraint()), ITEMS)
    assert _op(gated, "proj:recipe app") == "delete"


def test_an_emphasize_forbids_deleting_the_item():
    raw = [{"item_key": "proj:diginetica", "op": "delete", "rationale": "off-topic"}]
    validated = TailorPlanner.validate_plan(raw, ITEMS, [])
    gated, _ = apply_constraints(
        validated,
        _set(_constraint(polarity="emphasize", target_key="proj:diginetica",
                         text="Lead with Diginetica.")),
        ITEMS,
    )
    assert _op(gated, "proj:diginetica") == "revise"


def test_a_reframe_pins_the_strategy_without_touching_a_keep():
    """`keep` carries a re-tailor's prior bullets forward verbatim (#115), and a
    reframe is not a mandate to discard the user's existing tailoring."""
    raw = [
        {"item_key": "proj:diginetica", "op": "keep"},
        {"item_key": "proj:recipe app", "op": "revise", "strategy": "tighten"},
    ]
    validated = TailorPlanner.validate_plan(raw, ITEMS, [])
    gated, _ = apply_constraints(
        validated,
        _set(
            _constraint(preference_id="a", polarity="reframe", target_key="proj:diginetica"),
            _constraint(preference_id="b", polarity="reframe", target_key="proj:recipe app"),
        ),
        ITEMS,
    )
    assert _op(gated, "proj:diginetica") == "keep"
    recipe = next(a for a in gated if a["item_key"] == "proj:recipe app")
    assert recipe["strategy"] == "reframe"


def test_a_suppression_cannot_empty_a_section():
    """"Drop the retail job" when it is the only experience loses to the
    structural invariant: an empty experience section is not a resume."""
    items = [ITEMS[0], ITEMS[2]]
    gated, _ = apply_constraints(
        _plan(items),
        _set(_constraint(target_key="exp:barista|coffee co", target_term="Barista")),
        items,
    )
    assert _op(gated, "exp:barista|coffee co") == "keep"
    assert "refusing to remove every experience" in next(
        a["rationale"] for a in gated if a["item_key"] == "exp:barista|coffee co")


def test_a_preference_binding_to_nothing_is_reported_not_swallowed():
    """A skill-targeted or since-removed target has no action to gate. An
    applied preference that quietly did nothing is the silent-symptom failure
    this tier exists to avoid."""
    _, enforcement = apply_constraints(
        _plan(), _set(_constraint(target_key="skill:machine learning",
                                  target_term="machine learning")), ITEMS)
    assert enforcement["unenforced"][0]["target_key"] == "skill:machine learning"
    assert "changed" not in enforcement


def test_the_gate_is_applied_to_the_deterministic_fallback_too():
    """The fallback runs when the model failed, which is no reason for a user's
    standing preferences to stop binding."""
    class _BrokenLLM:
        def invoke(self, _messages):
            raise RuntimeError("provider down")

    plan = TailorPlanner(llm=_BrokenLLM()).plan(
        items=ITEMS, pool=[], jd_text="", missing_skills=[],
        constraints=_set(_constraint()),
    )
    assert plan["planner"] == "default"
    assert _op(plan["actions"], "proj:recipe app") == "delete"


# ── the prompt block ─────────────────────────────────────────────────────────

def test_the_prompt_is_byte_identical_without_preferences():
    """Conditional inclusion, the same discipline graph_evidence and job_cards
    use: a user with no preferences pays nothing, not even a blank heading."""
    seen = []

    class _Capture:
        def invoke(self, messages):
            seen.append(messages[0]["content"])
            raise RuntimeError("stop here")

    for constraints in (None, {"applied": [], "conflicts": [], "refused": []}):
        TailorPlanner(llm=_Capture()).plan(
            items=ITEMS, pool=[], jd_text="jd", missing_skills=[],
            constraints=constraints,
        )
    assert seen[0] == seen[1]
    assert "STANDING PREFERENCES" not in seen[0]


def test_the_prompt_carries_the_constraints_when_present():
    seen = []

    class _Capture:
        def invoke(self, messages):
            seen.append(messages[0]["content"])
            raise RuntimeError("stop here")

    TailorPlanner(llm=_Capture()).plan(
        items=ITEMS, pool=[], jd_text="jd", missing_skills=[],
        constraints=_set(_constraint()),
    )
    assert "STANDING PREFERENCES" in seen[0]
    assert "LEAVE OUT [Recipe App]" in seen[0]


# ── the decision log ─────────────────────────────────────────────────────────

def test_the_log_entry_is_unchanged_without_preferences():
    entry = decision_log_entry({"actions": [], "knobs": {}}, {}, {})
    assert "constraints" not in entry


def test_conflicts_and_refusals_are_recorded_in_the_log():
    """Acceptance criterion: a preference that lost to a requirement, or was
    refused for want of evidence, is something the user can see and argue with."""
    constraints = compile_constraints(
        [
            {"preference_id": "p1", "text": "No ML.", "polarity": "suppress",
             "target_term": "machine learning", "strength": 1},
            {"preference_id": "p2", "text": "Lead with leadership.",
             "polarity": "emphasize", "target_term": "leadership", "strength": 3},
        ],
        {"requirements": [{"text": "The candidate knows machine learning.",
                           "type": "required", "criticality": 5,
                           "terms": ["machine learning"], "ordinal": 0}]},
        [],
    )
    entry = decision_log_entry({"actions": [], "knobs": {}}, {}, {},
                               constraints=constraints)
    assert entry["constraints"]["conflicts"][0]["winner"] == "jd"
    assert entry["constraints"]["refused"][0]["preference_id"] == "p2"


# ── skills ───────────────────────────────────────────────────────────────────

def test_a_suppressed_skill_is_dropped_from_the_ranked_list():
    ranked = [{"name": "Python"}, {"name": "Machine Learning"}, {"name": "SQL"}]
    kept = ResumeTailorAgent._suppress_skills(
        ranked, _set(_constraint(target_key="skill:machine learning",
                                 target_term="Machine Learning")))
    assert [s["name"] for s in kept] == ["Python", "SQL"]


def test_suppressing_every_skill_is_refused():
    ranked = [{"name": "Python"}]
    kept = ResumeTailorAgent._suppress_skills(
        ranked, _set(_constraint(target_key="skill:python", target_term="Python")))
    assert kept == ranked


def test_skills_are_untouched_without_preferences():
    ranked = [{"name": "Python"}]
    assert ResumeTailorAgent._suppress_skills(ranked, None) is ranked


# ── the mandatory node + the write barrier ───────────────────────────────────

def _user(engine) -> User:
    with Session(engine) as session:
        user = User(name="Gate", email=f"gate-{uuid4()}@example.com")
        session.add(user)
        session.commit()
        session.refresh(user)
        return user


def test_the_node_runs_and_returns_an_empty_set_for_a_user_with_no_preferences(
        isolated_engine):
    """The amendment's hard requirement: the persona step is *always* called,
    even when the profile is empty. A tier only consulted when something
    upstream decides it is relevant is a tier that silently stops binding."""
    user = _user(isolated_engine)
    constraints = ResumeTailorAgent._compile_preference_constraints(
        user.user_id, uuid4(), None, [], [], [], [])
    assert constraints == {"applied": [], "conflicts": [], "refused": []}


def test_the_node_binds_a_stored_preference(isolated_engine):
    user = _user(isolated_engine)
    proposal = compile_preferences([{
        "text": "Do not include the Recipe App.",
        "polarity": "suppress",
        "target_type": "project",
        "target_label": "project: Recipe App",
        "scope_type": "global",
        "strength": 4,
        "evidence": "skip the recipe app",
    }], [{"key": "proj:recipe app", "label": "project: Recipe App",
          "target_type": "project"}])[0]
    services.apply_preference_decision(user.user_id, proposal)

    constraints = ResumeTailorAgent._compile_preference_constraints(
        user.user_id, uuid4(), None,
        [], [{"name": "Recipe App"}], [], [],
    )
    assert [a["target_key"] for a in constraints["applied"]] == ["proj:recipe app"]


def test_the_pipeline_never_writes_a_preference(isolated_engine):
    """#118's write barrier, which binds harder here because these preferences
    are *inferred*: a pipeline allowed to write this table would suppress an
    item, observe the suppression, infer a standing preference from it, and cite
    that back as the user's own instruction."""
    user = _user(isolated_engine)
    ResumeTailorAgent._compile_preference_constraints(
        user.user_id, uuid4(), None, [], [], [], [])
    assert services.load_preferences(user.user_id, include_inactive=True) == []


# ── the cost of honoring a preference ────────────────────────────────────────

def test_honoring_a_suppression_costs_ats_composite_and_the_cost_is_bounded():
    """Acceptance criterion 9, measured rather than assumed.

    The ATS composite is 0.75 coverage and coverage is monotone non-decreasing
    in text (the defect #127 documents), so *any* suppression scores as a loss
    on it — this metric structurally cannot reward honoring a user. The number
    is still worth pinning: it is the price of the tier, and it re-baselines
    once #125-#127 land.

    Suppressing an item the JD does not ask for costs nothing, which is the
    case the arbitration is designed to produce; the cost only appears when a
    hard preference beats a real requirement.
    """
    from agents.ats_scorer import ATSScoringEngine

    jd = "We need a Python engineer with React and PyTorch experience."
    full = {
        "experiences": [{"title": "Intern", "company": "Acme",
                         "bullets": ["Built Python services"]}],
        "projects": [
            {"name": "Recipe App", "bullets": ["A React recipe app"]},
            {"name": "Diginetica", "bullets": ["PyTorch recommender"]},
        ],
        "skills_ranked": [{"name": "Python"}, {"name": "React"}, {"name": "PyTorch"}],
    }
    matched = {"technical": ["Python", "React", "PyTorch"]}

    def composite(content):
        return ATSScoringEngine.score_tailored(content, jd, matched)["composite"]

    baseline = composite(full)

    off_jd = {**full, "projects": [full["projects"][1]],
              "skills_ranked": full["skills_ranked"]}
    on_jd = {**full, "projects": [full["projects"][1]],
             "skills_ranked": [{"name": "Python"}, {"name": "PyTorch"}]}

    print(f"\nATS composite  baseline={baseline}  "
          f"suppress-off-JD={composite(off_jd)}  suppress-on-JD={composite(on_jd)}")

    # Suppressing content the JD never asked for is free.
    assert composite(off_jd) == baseline
    # Suppressing a term the JD requires costs, and only ever costs.
    assert composite(on_jd) < baseline


def test_arbitration_failure_degrades_to_no_constraints(isolated_engine, monkeypatch):
    """A preference tier that can fail a tailoring run is worse than one that
    occasionally does not bind."""
    user = _user(isolated_engine)
    monkeypatch.setattr(
        services, "load_preferences",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down")))
    constraints = ResumeTailorAgent._compile_preference_constraints(
        user.user_id, uuid4(), None, [], [], [], [])
    assert constraints == {"applied": [], "conflicts": [], "refused": []}
