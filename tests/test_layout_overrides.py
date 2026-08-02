"""Explicit user arrangement overrides (issue #118).

Two layers, tested separately because they fail differently:

* `agents/layout.py` — the pure reconciler. Every rule that decides what a
  stale or partial override does lives here and needs no database.
* `agents/tailor.py` — the precedence chain. An override must outrank both the
  carried-forward arrangement (#115) and a fresh ranking, survive a re-tailor,
  and never be written by the pipeline itself.
"""

from sqlmodel import Session

from agents.layout import (
    apply_overrides,
    resolve_bullets,
    resolve_section_order,
    resolve_skills,
)
from database.models import (
    Education, Experience, JobDescription, Project, UserJobResult,
)


# ── the pure reconciler ──────────────────────────────────────────────────────


_PRESENT = ["education", "experience", "projects", "skills"]
_FALLBACK = ["education", "experience", "projects", "skills"]


def test_no_override_is_the_identity():
    """The backward-compatibility guarantee, at the level it is decided."""
    assert resolve_section_order(None, _FALLBACK, _PRESENT) == _FALLBACK
    ranked = [{"name": "Python", "score": 1.0}]
    assert resolve_skills(None, ranked) == ranked
    content = {"experiences": [{"title": "SWE", "bullets": ["a", "b"]}]}
    assert resolve_bullets(None, content) == content


def test_section_override_wins_and_is_order_preserving():
    order = resolve_section_order(
        ["skills", "projects"], _FALLBACK, _PRESENT)
    assert order[:2] == ["skills", "projects"]


def test_a_section_the_override_does_not_name_is_appended_never_dropped():
    """Rule 2 — the safety property. A stale override must not silently remove
    a section from the resume."""
    order = resolve_section_order(["skills"], _FALLBACK, _PRESENT)
    assert order[0] == "skills"
    assert set(order) == set(_PRESENT)


def test_a_section_that_no_longer_exists_is_dropped_from_the_override():
    order = resolve_section_order(
        ["achievements", "skills"], _FALLBACK, _PRESENT)
    assert "achievements" not in order
    assert order[0] == "skills"


def test_skills_are_permuted_not_rebuilt():
    """The override carries names; score and category stay the ranker's, so a
    reorder cannot falsify what produced the ranking."""
    ranked = [
        {"name": "Python", "category": "language", "score": 0.9},
        {"name": "PyTorch", "category": "ml", "score": 0.4},
    ]
    out = resolve_skills(["pytorch"], ranked)
    assert [s["name"] for s in out] == ["PyTorch", "Python"]
    assert out[0]["score"] == 0.4 and out[0]["category"] == "ml"


def test_an_unknown_skill_name_in_the_override_is_ignored():
    ranked = [{"name": "Python", "score": 1.0}]
    assert resolve_skills(["Cobol", "Python"], ranked) == ranked


def test_bullets_are_reordered_within_their_item():
    content = {"experiences": [
        {"title": "SWE", "bullets": ["first", "second", "third"]},
    ]}
    out = resolve_bullets(
        [{"section": "experience", "item": "SWE", "order": ["third", "first"]}],
        content,
    )
    assert out["experiences"][0]["bullets"] == ["third", "first", "second"]


def test_resolve_bullets_does_not_mutate_its_input():
    content = {"experiences": [{"title": "SWE", "bullets": ["a", "b"]}]}
    resolve_bullets(
        [{"section": "experience", "item": "SWE", "order": ["b"]}], content)
    assert content["experiences"][0]["bullets"] == ["a", "b"]


def test_a_bullet_override_for_a_deleted_item_is_dropped():
    content = {"projects": [{"name": "Kept", "bullets": ["x"]}]}
    out = resolve_bullets(
        [{"section": "projects", "item": "Deleted", "order": ["y"]}], content)
    assert out["projects"][0]["bullets"] == ["x"]


def test_a_bullet_rewritten_by_the_planner_keeps_its_position():
    """The case the whole fallback exists for: a re-tailor revises bullet text,
    and the arrangement the user chose must survive it."""
    content = {"experiences": [{
        "title": "SWE",
        "bullets": ["Built a Redis cache for sessions",
                    "Led the migration to Kubernetes"],
    }]}
    out = resolve_bullets([{
        "section": "experience", "item": "SWE",
        # As the user positioned it, before the planner rewrote the wording.
        "order": ["Led migration to Kubernetes clusters"],
    }], content)
    assert out["experiences"][0]["bullets"][0] == "Led the migration to Kubernetes"


def test_two_unrelated_bullets_are_never_bound_together():
    """The fallback threshold is high on purpose — mis-binding reorders content
    the user never touched, which is worse than degrading to pipeline order."""
    content = {"experiences": [{
        "title": "SWE",
        "bullets": ["Built a Redis cache for sessions",
                    "Wrote the quarterly compliance report"],
    }]}
    out = resolve_bullets([{
        "section": "experience", "item": "SWE",
        "order": ["Trained a convolutional network on satellite imagery"],
    }], content)
    assert out["experiences"][0]["bullets"] == content["experiences"][0]["bullets"]


def test_latex_escaping_does_not_break_bullet_identity():
    """A bullet round-trips content -> escaped .tex -> editor display text, and
    identity has to survive that."""
    content = {"experiences": [
        {"title": "SWE", "bullets": ["Cut costs 40%", "Shipped the API"]},
    ]}
    out = resolve_bullets([{
        "section": "experience", "item": "SWE",
        "order": ["Shipped the API", r"Cut costs 40\%"],
    }], content)
    assert out["experiences"][0]["bullets"] == ["Shipped the API", "Cut costs 40%"]


def test_resolution_is_deterministic():
    """Two resolutions of the same inputs are identical — the same guarantee
    #121 holds for JD extraction, for the same reason."""
    content = {"experiences": [{"title": "SWE", "bullets": ["a", "b", "c"]}]}
    overrides = {
        "section_order": ["skills", "experience"],
        "bullets": [{"section": "experience", "item": "SWE", "order": ["c"]}],
    }
    first = apply_overrides(overrides, content, _FALLBACK, _PRESENT)
    second = apply_overrides(overrides, content, _FALLBACK, _PRESENT)
    assert first == second


# ── the precedence chain in tailor() ─────────────────────────────────────────


_JD = "machine learning research pytorch publications"

_CONTENT = {
    "experiences": [{
        "title": "Software Engineer",
        "company": "BigCo",
        "bullets": ["Led kubernetes deployments", "Managed terraform infra"],
    }],
    "projects": [{
        "name": "Research Pipeline",
        "bullets": ["Published machine learning research using pytorch"],
    }],
    "skills_emphasized": ["Python"],
}


def _run_tailor(isolated_engine, monkeypatch, *, overrides=None,
                prior_content=None, plan_actions=None, education=None,
                edited_tex=None):
    """One tailor() run against a seeded result, returning the stored row."""
    import agents.tailor as tailor_module
    import services as services_module
    from conftest import _seed_user_and_skill

    monkeypatch.setattr(tailor_module, "engine", isolated_engine)
    monkeypatch.setattr(tailor_module, "get_llm", lambda *a, **kw: object())
    monkeypatch.setattr(services_module, "rebuild_job_card", lambda *a, **kw: None)
    monkeypatch.setattr(
        "agents.formatter.ResumeFormatterAgent.fit_content_to_one_page",
        lambda self, content, section_order=None: content,
    )

    user = _seed_user_and_skill(isolated_engine)
    with Session(isolated_engine) as session:
        job = JobDescription(title="MLE", company="Lab", description=_JD,
                             status="tailored")
        session.add(job)
        session.add(Experience(user_id=user.user_id, title="Software Engineer",
                               company="BigCo", bullets=["source bullet"]))
        session.add(Project(user_id=user.user_id, name="Research Pipeline",
                            description="ml research"))
        session.add(Project(user_id=user.user_id, name="Side Tool",
                            description="cli utility"))
        for edu in education or []:
            session.add(Education(user_id=user.user_id, **edu))
        session.commit()
        session.refresh(job)
        result = UserJobResult(
            user_id=user.user_id, job_id=job.job_id,
            tailored_resume_content=prior_content or {},
            layout_overrides=overrides,
            edited_tex=edited_tex,
        )
        session.add(result)
        session.commit()
        result_id, job_id = result.result_id, job.job_id

    agent = tailor_module.ResumeTailorAgent()

    class FakeGraph:
        def invoke(self, state):
            return {**state, "tailored_content": dict(_CONTENT),
                    "evaluation": {}, "attempt": 1, "done": True}

    agent.graph = FakeGraph()
    agent.tailor(user.user_id, job_id, result_id,
                 plan_override={"actions": plan_actions or []})

    with Session(isolated_engine) as session:
        row = session.get(UserJobResult, result_id)
        session.refresh(row)
        return row


def test_absent_override_reproduces_current_behaviour(isolated_engine, monkeypatch):
    """The backward-compatibility guarantee, end to end."""
    without = _run_tailor(isolated_engine, monkeypatch, overrides=None)
    assert without.layout_overrides is None
    order = without.tailored_resume_content["_section_order"]
    assert order.index("projects") < order.index("experience")


def test_override_outranks_a_fresh_ranking(isolated_engine, monkeypatch):
    """Tier 1 beats tier 3. The research JD ranks projects first; the user says
    otherwise and the user wins."""
    row = _run_tailor(isolated_engine, monkeypatch,
                      overrides={"section_order": ["experience", "projects"]})
    order = row.tailored_resume_content["_section_order"]
    assert order.index("experience") < order.index("projects")


def test_override_outranks_carry_forward(isolated_engine, monkeypatch):
    """Tier 1 beats tier 2 (#115). The prior run froze skills-first; the user
    pinned experience-first afterwards, so carry-forward must yield."""
    row = _run_tailor(
        isolated_engine, monkeypatch,
        prior_content={**_CONTENT,
                       "_section_order": ["skills", "projects", "experience"]},
        overrides={"section_order": ["experience"]},
    )
    assert row.tailored_resume_content["_section_order"][0] == "experience"


def test_override_survives_a_retailor_that_clears_edited_tex(
    isolated_engine, monkeypatch
):
    """The distinction that motivates the whole issue: `edited_tex` encodes
    content and is invalidated by a re-tailor, an override encodes arrangement
    and is not."""
    row = _run_tailor(
        isolated_engine, monkeypatch,
        overrides={"section_order": ["skills"]},
        edited_tex=r"\documentclass{article}",
        prior_content={**_CONTENT, "_section_order": ["experience", "projects"]},
    )
    assert row.edited_tex is None
    assert row.layout_overrides == {"section_order": ["skills"]}
    assert row.tailored_resume_content["_section_order"][0] == "skills"


def test_a_structural_change_does_not_discard_the_override(
    isolated_engine, monkeypatch
):
    """A delete forces a recompute of the ranking (#115), but the recomputed
    order is still only the *fallback* — the override sits above it."""
    row = _run_tailor(
        isolated_engine, monkeypatch,
        overrides={"section_order": ["experience", "projects"]},
        prior_content={**_CONTENT, "_section_order": ["skills", "projects", "experience"]},
        plan_actions=[{"section": "project", "item_key": "proj:research pipeline",
                       "op": "delete", "rationale": "off topic"}],
    )
    order = row.tailored_resume_content["_section_order"]
    assert order.index("experience") < order.index("projects")


def test_the_pipeline_never_creates_an_override(isolated_engine, monkeypatch):
    """If tailoring could set this column the ranker would eventually launder
    its own output into a counterfeit user override."""
    row = _run_tailor(isolated_engine, monkeypatch, overrides=None)
    assert row.layout_overrides is None


def test_the_pipeline_never_rewrites_an_override(isolated_engine, monkeypatch):
    """A run resolves *against* the override and leaves it exactly as the user
    set it — no normalization, no back-writing of the resolved order."""
    kept = {"section_order": ["skills"]}
    row = _run_tailor(isolated_engine, monkeypatch, overrides=dict(kept))
    assert row.layout_overrides == kept


def test_bullet_override_reaches_the_stored_content(isolated_engine, monkeypatch):
    row = _run_tailor(isolated_engine, monkeypatch, overrides={"bullets": [{
        "section": "experience", "item": "Software Engineer",
        "order": ["Managed terraform infra"],
    }]})
    bullets = row.tailored_resume_content["experiences"][0]["bullets"]
    assert bullets[0] == "Managed terraform infra"
    assert set(bullets) == {"Led kubernetes deployments", "Managed terraform infra"}


# ── education is ranked, not pinned (issue #118) ─────────────────────────────


_EDU = [{"institution": "UCSD", "degree": "B.S. Machine Learning"}]


def test_education_is_ranked_by_jd_relevance(isolated_engine, monkeypatch):
    """It used to be pinned first unconditionally. Now a degree the posting
    names competes for the lead like any other section."""
    from agents.tailor import ResumeTailorAgent

    content = {**_CONTENT, "education": [
        {"institution": "UCSD", "degree": "B.S. Machine Learning"}]}
    ml_first = ResumeTailorAgent._ranked_section_order(
        content, {}, "machine learning degree required")
    infra_first = ResumeTailorAgent._ranked_section_order(
        content, {}, "kubernetes terraform deployments infrastructure")

    assert "education" in ml_first
    # The same content orders education differently for different postings —
    # which is the whole claim. Pinned, these two would be identical.
    assert ml_first != infra_first
    assert ml_first.index("education") < infra_first.index("education")


def test_education_reaches_the_content_for_scoring(isolated_engine, monkeypatch):
    row = _run_tailor(isolated_engine, monkeypatch, education=_EDU)
    assert row.tailored_resume_content["education"][0]["institution"] == "UCSD"
    assert "education" in row.tailored_resume_content["_section_order"]


def test_a_user_without_education_gets_no_education_section(
    isolated_engine, monkeypatch
):
    """Previously the pinned list named education for everyone, including users
    who had none — a section the formatter then rendered empty."""
    row = _run_tailor(isolated_engine, monkeypatch, education=None)
    assert "education" not in row.tailored_resume_content["_section_order"]


def test_a_user_can_move_education(isolated_engine, monkeypatch):
    """The point of un-pinning: the ranker places it, and the user overrules."""
    row = _run_tailor(isolated_engine, monkeypatch, education=_EDU,
                      overrides={"section_order": ["experience", "education"]})
    order = row.tailored_resume_content["_section_order"]
    assert order.index("experience") < order.index("education")


# ── migration ────────────────────────────────────────────────────────────────


def test_a_row_predating_the_column_loads(isolated_engine):
    """Backward compatibility for existing local databases: a row written
    before #118 has NULL here and must load and behave as 'ranker decides'."""
    from sqlalchemy import text
    from sqlmodel import select
    from conftest import _seed_user_and_skill

    user = _seed_user_and_skill(isolated_engine)
    with Session(isolated_engine) as session:
        job = JobDescription(title="J", company="C", status="analyzed")
        session.add(job)
        session.commit()
        session.refresh(job)
        result = UserJobResult(user_id=user.user_id, job_id=job.job_id)
        session.add(result)
        session.commit()
        result_id = result.result_id

    # Explicitly NULL the column, as a database migrated by the ALTER would be.
    with isolated_engine.connect() as conn:
        conn.execute(
            text("UPDATE userjobresult SET layout_overrides = NULL "
                 "WHERE result_id = :r"),
            {"r": str(result_id)},
        )
        conn.commit()

    with Session(isolated_engine) as session:
        row = session.exec(select(UserJobResult)).first()
        assert row.layout_overrides is None


def test_the_migration_is_registered():
    """The ALTER that brings an existing database up to the new schema."""
    import inspect
    import database.db as db_module

    source = inspect.getsource(db_module)
    assert "ALTER TABLE userjobresult ADD COLUMN layout_overrides" in source
