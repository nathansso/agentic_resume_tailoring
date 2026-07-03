"""PRD 04 — job workspace lifecycle tests."""
import uuid
from pathlib import Path

import pytest
from sqlmodel import Session, select

from database.models import JobDescription, JobSkill, Skill, UserJobResult
from agents.chat import ChatAgent


# ── helpers ──────────────────────────────────────────────────


def _make_job(engine, title="Eng", company="Acme", status="created", description="") -> JobDescription:
    from database.db import engine as _default_engine
    with Session(engine) as session:
        job = JobDescription(title=title, company=company, description=description, status=status)
        session.add(job)
        session.commit()
        session.refresh(job)
        return job


# ── tests ─────────────────────────────────────────────────────


def test_job_lifecycle_status_transitions(isolated_engine):
    """Job status advances created→analyzed→tailored→exported via DB writes."""
    from datetime import datetime

    job = _make_job(isolated_engine, status="created")
    job_id = job.job_id

    with Session(isolated_engine) as session:
        j = session.get(JobDescription, job_id)
        assert j.status == "created"

        j.status = "analyzed"
        j.updated_at = datetime.utcnow()
        session.add(j)
        session.commit()

    with Session(isolated_engine) as session:
        j = session.get(JobDescription, job_id)
        assert j.status == "analyzed"

        j.status = "tailored"
        j.updated_at = datetime.utcnow()
        session.add(j)
        session.commit()

    with Session(isolated_engine) as session:
        j = session.get(JobDescription, job_id)
        assert j.status == "tailored"

        j.status = "exported"
        j.updated_at = datetime.utcnow()
        session.add(j)
        session.commit()

    with Session(isolated_engine) as session:
        j = session.get(JobDescription, job_id)
        assert j.status == "exported"


def test_chat_sets_active_job_context(isolated_engine):
    """set_active_job stores the job_id; _get_active_job returns the correct record."""
    job = _make_job(isolated_engine, title="ML Eng", company="OpenAI")
    agent = ChatAgent()
    assert agent.active_job_id is None

    agent.set_active_job(str(job.job_id))
    assert agent.active_job_id == str(job.job_id)

    fetched = agent._get_active_job()
    assert fetched is not None
    assert fetched.title == "ML Eng"
    assert fetched.company == "OpenAI"


def test_tailoring_explainability_sections(isolated_engine, monkeypatch):
    """_tailor_active_job produces all four explainability sections in its response."""
    from database.user_utils import get_active_profile
    import graph.pipeline as _pipeline

    job = _make_job(isolated_engine, title="SWE", company="Corp", description="Build software")
    # Seed a skill link so the guard passes
    with Session(isolated_engine) as session:
        skill = Skill(name="Python")
        session.add(skill)
        session.flush()
        session.add(JobSkill(job_id=job.job_id, skill_id=skill.skill_id, required=True, weight=1.0))
        session.commit()

    from conftest import _seed_user_and_skill
    _seed_user_and_skill(isolated_engine)

    def fake_match(state):
        state["matched_skills"] = {
            "Python": {"match_type": "direct"},
            "FastAPI": {"match_type": "semantic", "similarity": 0.9, "matched_to": "Flask"},
            "Docker": {"match_type": "semantic", "similarity": 0.6},
        }
        state["missing_skills"] = ["Kubernetes"]
        state["ats_score"] = 72.0
        return state

    def fake_tailor(state):
        state["tailored_content"] = {"summary": "Tailored"}
        state["result_id"] = ""
        return state

    def fake_format(state):
        state["formatted_resume"] = "# Resume"
        return state

    monkeypatch.setattr(_pipeline, "match_skills_node", fake_match)
    monkeypatch.setattr(_pipeline, "tailor_resume_node", fake_tailor)
    monkeypatch.setattr(_pipeline, "format_resume_node", fake_format)

    agent = ChatAgent()
    agent.set_active_job(str(job.job_id))
    response = agent._tailor_active_job("")

    assert "Matched (evidence-backed)" in response
    assert "Python" in response
    assert "Emphasized" in response
    assert "FastAPI" in response
    assert "Inferred (low evidence)" in response
    assert "Docker" in response
    assert "Missing" in response
    assert "Kubernetes" in response
    assert "72" in response


def test_export_creates_file(isolated_engine, monkeypatch, tmp_path):
    """_export_active_job writes a PDF file to the exports directory."""
    from database.models import UserJobResult
    from conftest import _seed_user_and_skill

    user = _seed_user_and_skill(isolated_engine)
    job = _make_job(isolated_engine, title="PM", company="Inc", status="tailored")

    fake_content = {"summary": "Great match", "sections": []}
    with Session(isolated_engine) as session:
        result = UserJobResult(
            user_id=user.user_id,
            job_id=job.job_id,
            ats_score=85.0,
            matched_skills={},
            missing_skills=[],
            tailored_resume_content=fake_content,
        )
        session.add(result)
        session.commit()

    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    from agents import formatter as fmt_module
    monkeypatch.setattr(
        fmt_module.ResumeFormatterAgent,
        "format_pdf",
        lambda self, content, job_title="", section_order=None: b"%PDF-1.4 stub",
    )

    agent = ChatAgent()
    agent.set_active_job(str(job.job_id))
    response = agent._export_active_job("")

    assert "exported" in response.lower() or "PDF" in response
    export_path = Path(response.split(":", 1)[-1].strip()) if ":" in response else None
    if export_path:
        assert export_path.suffix == ".pdf"
        assert export_path.exists()


def test_export_produces_pdf_file(isolated_engine, monkeypatch, tmp_path):
    """_export_active_job stores a .pdf path (not .md) in UserJobResult.export_path."""
    from database.models import UserJobResult
    from conftest import _seed_user_and_skill

    user = _seed_user_and_skill(isolated_engine)
    job = _make_job(isolated_engine, title="Engineer", company="Acme", status="tailored")

    with Session(isolated_engine) as session:
        result = UserJobResult(
            user_id=user.user_id,
            job_id=job.job_id,
            ats_score=90.0,
            matched_skills={},
            missing_skills=[],
            tailored_resume_content={"summary": "Great"},
        )
        session.add(result)
        session.commit()

    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    from agents import formatter as fmt_module
    monkeypatch.setattr(
        fmt_module.ResumeFormatterAgent,
        "format_pdf",
        lambda self, content, job_title="", section_order=None: b"%PDF-1.4 stub",
    )

    agent = ChatAgent()
    agent.set_active_job(str(job.job_id))
    response = agent._export_active_job("")

    # Response path must end with .pdf
    assert ".pdf" in response, f"Expected .pdf in response: {response!r}"
    export_path = Path(response.split(":", 1)[-1].strip())
    assert export_path.suffix == ".pdf", f"Expected .pdf suffix, got: {export_path.suffix}"
    assert export_path.exists(), "Exported file should exist on disk"

    # DB record must also store the .pdf path
    with Session(isolated_engine) as session:
        db_result = session.exec(
            select(UserJobResult).where(UserJobResult.user_id == user.user_id)
        ).first()
    assert db_result is not None
    assert db_result.export_path is not None
    assert db_result.export_path.endswith(".pdf"), (
        f"DB export_path should end with .pdf, got: {db_result.export_path!r}"
    )


def test_set_active_job_new_job_has_empty_history(isolated_engine):
    """Selecting a brand-new job yields an empty agent history."""
    job = _make_job(isolated_engine, title="Brand New", company="Fresh Co")
    agent = ChatAgent()
    agent.history = [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}]

    agent.set_active_job(str(job.job_id))

    assert agent.history == []
    assert agent.active_job_id == str(job.job_id)


def test_set_active_job_restores_prior_history(isolated_engine):
    """Returning to a previously visited job restores the cached agent history."""
    job1 = _make_job(isolated_engine, title="Job One", company="Alpha")
    job2 = _make_job(isolated_engine, title="Job Two", company="Beta")
    agent = ChatAgent()

    # First visit to job1, accumulate history
    agent.set_active_job(str(job1.job_id))
    agent.history = [
        {"role": "user", "content": "analyze"},
        {"role": "assistant", "content": "done"},
    ]

    # Switch to job2 — should start with an empty history
    agent.set_active_job(str(job2.job_id))
    assert agent.history == []

    # Return to job1 — history must be restored
    agent.set_active_job(str(job1.job_id))
    assert agent.history == [
        {"role": "user", "content": "analyze"},
        {"role": "assistant", "content": "done"},
    ]


# ── Edge cases: job with no results ──────────────────────────────────────────


def test_get_job_details_no_results_yet(isolated_engine):
    """get_job_details returns a dict without 'ats_score' when no results exist for the job."""
    import services as services_module
    job = _make_job(isolated_engine, title="Fresh Job", company="Startup")

    detail = services_module.get_job_details(str(job.job_id))

    assert detail is not None, "Should return a dict for an existing job"
    assert detail["title"] == "Fresh Job"
    assert detail["company"] == "Startup"
    # No results yet — ats_score should NOT be in the dict (no KeyError in the service)
    assert "ats_score" not in detail


def test_get_jobs_score_display_no_results(isolated_engine):
    """get_jobs returns an empty score string for a job with no UserJobResult rows."""
    import services as services_module
    _make_job(isolated_engine, title="Unseen Job", company="Nobody Inc")

    jobs = services_module.get_jobs()
    assert len(jobs) == 1
    assert jobs[0]["title"] == "Unseen Job"
    assert jobs[0]["score"] == "", (
        f"Expected empty score for job with no results, got: {jobs[0]['score']!r}"
    )


# ── Issue 22: dynamic section reordering by job relevance ────


_REORDER_CONTENT = {
    "experiences": [{
        "title": "Software Engineer",
        "company": "BigCo",
        "bullets": ["Led kubernetes deployments", "Managed terraform infrastructure"],
    }],
    "projects": [{
        "name": "Research Pipeline",
        "bullets": ["Published machine learning research using pytorch"],
    }],
    "skills_emphasized": ["Python"],
}

_RESEARCH_JD = "machine learning research pytorch publications"
_INFRA_JD = "kubernetes terraform deployments infrastructure"


def test_score_section_relevance_differs_by_job():
    """The same content scores sections differently for different JDs."""
    from agents.tailor import ResumeTailorAgent

    proj_research = ResumeTailorAgent._score_section_relevance(
        "projects", _REORDER_CONTENT, {}, _RESEARCH_JD)
    exp_research = ResumeTailorAgent._score_section_relevance(
        "experience", _REORDER_CONTENT, {}, _RESEARCH_JD)
    assert proj_research > exp_research

    proj_infra = ResumeTailorAgent._score_section_relevance(
        "projects", _REORDER_CONTENT, {}, _INFRA_JD)
    exp_infra = ResumeTailorAgent._score_section_relevance(
        "experience", _REORDER_CONTENT, {}, _INFRA_JD)
    assert exp_infra > proj_infra


def test_ranked_section_order_differs_by_job():
    """Different job skill profiles produce different section orderings."""
    from agents.tailor import ResumeTailorAgent

    order_research = ResumeTailorAgent._ranked_section_order(_REORDER_CONTENT, {}, _RESEARCH_JD)
    order_infra = ResumeTailorAgent._ranked_section_order(_REORDER_CONTENT, {}, _INFRA_JD)

    # Education stays pinned first in both
    assert order_research[0] == "education"
    assert order_infra[0] == "education"

    assert order_research.index("projects") < order_research.index("experience")
    assert order_infra.index("experience") < order_infra.index("projects")
    assert order_research != order_infra


def test_tailor_persists_section_order(isolated_engine, monkeypatch):
    """ResumeTailorAgent.tailor() stores _section_order in tailored_resume_content."""
    import agents.tailor as tailor_module
    from conftest import _seed_user_and_skill

    monkeypatch.setattr(tailor_module, "engine", isolated_engine)
    monkeypatch.setattr(tailor_module, "get_llm", lambda *a, **kw: object())

    user = _seed_user_and_skill(isolated_engine)
    job = _make_job(isolated_engine, title="MLE", company="Lab",
                    status="analyzed", description=_RESEARCH_JD)
    with Session(isolated_engine) as session:
        result = UserJobResult(user_id=user.user_id, job_id=job.job_id)
        session.add(result)
        session.commit()
        result_id = result.result_id

    agent = tailor_module.ResumeTailorAgent()

    class FakeGraph:
        def invoke(self, state):
            return {**state, "tailored_content": dict(_REORDER_CONTENT),
                    "evaluation": {}, "attempt": 1, "done": True}

    agent.graph = FakeGraph()
    agent.tailor(user.user_id, job.job_id, result_id)

    with Session(isolated_engine) as session:
        stored = session.get(UserJobResult, result_id)
        order = stored.tailored_resume_content.get("_section_order")

    assert order is not None
    assert order[0] == "education"
    assert set(order) == {"education", "experience", "projects", "skills"}
    # Research JD → projects ahead of experience
    assert order.index("projects") < order.index("experience")


def test_export_passes_section_order(isolated_engine, monkeypatch, tmp_path):
    """_export_active_job forwards the stored _section_order to the formatter."""
    from conftest import _seed_user_and_skill

    user = _seed_user_and_skill(isolated_engine)
    job = _make_job(isolated_engine, title="RS", company="Lab", status="tailored")

    stored_order = ["education", "projects", "experience", "skills"]
    with Session(isolated_engine) as session:
        result = UserJobResult(
            user_id=user.user_id,
            job_id=job.job_id,
            tailored_resume_content={**_REORDER_CONTENT, "_section_order": stored_order},
        )
        session.add(result)
        session.commit()

    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    captured = {}

    def fake_format_pdf(self, content, job_title="", section_order=None):
        captured["section_order"] = section_order
        return b"%PDF-1.4 stub"

    from agents import formatter as fmt_module
    monkeypatch.setattr(fmt_module.ResumeFormatterAgent, "format_pdf", fake_format_pdf)

    agent = ChatAgent()
    agent.set_active_job(str(job.job_id))
    agent._export_active_job("")

    assert captured["section_order"] == stored_order


def test_export_section_order_fallback(isolated_engine, monkeypatch, tmp_path):
    """Without a stored _section_order the formatter gets None (style/default order)."""
    from conftest import _seed_user_and_skill

    user = _seed_user_and_skill(isolated_engine)
    job = _make_job(isolated_engine, title="RS", company="Lab", status="tailored")

    with Session(isolated_engine) as session:
        result = UserJobResult(
            user_id=user.user_id,
            job_id=job.job_id,
            tailored_resume_content=dict(_REORDER_CONTENT),
        )
        session.add(result)
        session.commit()

    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    captured = {}

    def fake_format_pdf(self, content, job_title="", section_order=None):
        captured["section_order"] = section_order
        return b"%PDF-1.4 stub"

    from agents import formatter as fmt_module
    monkeypatch.setattr(fmt_module.ResumeFormatterAgent, "format_pdf", fake_format_pdf)

    agent = ChatAgent()
    agent.set_active_job(str(job.job_id))
    agent._export_active_job("")

    assert captured["section_order"] is None


# ── best-of-N attempt selection (issue #58) ──────────────────────────────────


def _fake_breakdown(score):
    return {
        "composite": score,
        "skill_coverage": {"score": score, "covered": 1, "total": 1, "gaps": []},
        "keyword_coverage": {"missing_keywords": []},
    }


def _eval_state(content, attempt, best_score=-1.0, best_content=None):
    return {
        "user_id": "u", "job_id": "j", "result_id": "r",
        "resume_text": "", "job_text": "jd", "matched_skills": {},
        "missing_skills": [], "priority_keywords": [], "baseline_breakdown": {},
        "experiences": [], "projects": [],
        "tailored_content": content,
        "evaluation": {},
        "best_content": best_content or {},
        "best_evaluation": {},
        "best_score": best_score,
        "attempt": attempt, "done": False,
    }


def _eval_agent(monkeypatch, score_of):
    """A ResumeTailorAgent whose ATS scorer returns score_of(content)."""
    import agents.tailor as tm
    monkeypatch.setattr(tm, "get_llm", lambda *a, **k: object())

    def fake_score(content, job_text, matched_skills=None, baseline_breakdown=None):
        return _fake_breakdown(score_of(content))

    monkeypatch.setattr(tm.ATSScoringEngine, "score_tailored", fake_score)
    return tm.ResumeTailorAgent()


def test_evaluate_keeps_best_across_regression(monkeypatch):
    """A worse later attempt must not overwrite a better earlier one (issue #58)."""
    agent = _eval_agent(monkeypatch, lambda c: c["_score"])

    # Attempt 1 scores 80 — below the great bar (90), so the loop keeps going.
    s = _eval_state({"experiences": [], "projects": [], "_score": 80}, attempt=1)
    agent._evaluate_node(s)
    assert s["best_score"] == 80
    assert s["best_content"]["_score"] == 80
    assert s["done"] is False  # below great bar, budget remains

    # Attempt 2 (the budget cap) regresses to 60 — best stays at 80, loop ends.
    s["attempt"] = 2
    s["tailored_content"] = {"experiences": [], "projects": [], "_score": 60}
    agent._evaluate_node(s)
    assert s["best_score"] == 80
    assert s["best_content"]["_score"] == 80
    assert s["done"] is True  # MAX_RETRIES reached


def test_evaluate_early_exits_at_great_bar(monkeypatch):
    """Clearing the high bar stops the loop early to save an LLM call (issue #58)."""
    agent = _eval_agent(monkeypatch, lambda c: c["_score"])

    s = _eval_state({"experiences": [], "projects": [], "_score": 95}, attempt=1)
    agent._evaluate_node(s)
    assert s["done"] is True  # 95 >= 90 and kw_coverage 1.0 >= 0.80
    assert s["best_score"] == 95


def test_tailor_ships_best_attempt_not_last(isolated_engine, monkeypatch):
    """tailor() persists the best-scoring attempt, not whatever ran last (issue #58)."""
    import agents.tailor as tailor_module
    from conftest import _seed_user_and_skill

    monkeypatch.setattr(tailor_module, "engine", isolated_engine)
    monkeypatch.setattr(tailor_module, "get_llm", lambda *a, **kw: object())

    user = _seed_user_and_skill(isolated_engine)
    job = _make_job(isolated_engine, title="MLE", company="Lab",
                    status="analyzed", description=_RESEARCH_JD)
    with Session(isolated_engine) as session:
        result = UserJobResult(user_id=user.user_id, job_id=job.job_id)
        session.add(result)
        session.commit()
        result_id = result.result_id

    agent = tailor_module.ResumeTailorAgent()

    class FakeGraph:
        def invoke(self, state):
            return {**state,
                    "tailored_content": {"experiences": [], "projects": [], "marker": "last"},
                    "best_content": {"experiences": [], "projects": [], "marker": "best"},
                    "evaluation": {"ats_breakdown": {"composite": 60}},
                    "best_evaluation": {"ats_breakdown": {"composite": 90}},
                    "best_score": 90.0, "attempt": 3, "done": True}

    agent.graph = FakeGraph()
    shipped = agent.tailor(user.user_id, job.job_id, result_id)

    assert shipped["marker"] == "best"
    with Session(isolated_engine) as session:
        stored = session.get(UserJobResult, result_id)
        assert stored.tailored_resume_content["marker"] == "best"
        assert stored.tailored_score_breakdown == {"composite": 90}


def test_tailor_falls_back_to_last_when_no_best(isolated_engine, monkeypatch):
    """When no attempt scored (all errored), ship the last content so the error
    path still surfaces (issue #58)."""
    import agents.tailor as tailor_module
    from conftest import _seed_user_and_skill

    monkeypatch.setattr(tailor_module, "engine", isolated_engine)
    monkeypatch.setattr(tailor_module, "get_llm", lambda *a, **kw: object())

    user = _seed_user_and_skill(isolated_engine)
    job = _make_job(isolated_engine, title="MLE", company="Lab", status="analyzed")
    with Session(isolated_engine) as session:
        result = UserJobResult(user_id=user.user_id, job_id=job.job_id)
        session.add(result)
        session.commit()
        result_id = result.result_id

    agent = tailor_module.ResumeTailorAgent()

    class FakeGraph:
        def invoke(self, state):
            return {**state,
                    "tailored_content": {"error": "boom"},
                    "best_content": {},
                    "evaluation": {"coverage_pct": 0, "gaps": ["generation_failed"]},
                    "best_evaluation": {},
                    "best_score": -1.0, "attempt": 3, "done": True}

    agent.graph = FakeGraph()
    shipped = agent.tailor(user.user_id, job.job_id, result_id)

    assert "error" in shipped


def test_as_obj_coerces_json_string_columns():
    """Regression: SQLite can round-trip JSON columns as strings, which crashed
    the tailor read-path with `'str' object has no attribute 'get'`. `_as_obj`
    normalises them so downstream .get()/iteration stays safe."""
    from agents.tailor import _as_obj

    # JSON-string round-trip (the SQLite failure mode) is parsed back to dict/list
    assert _as_obj('{"composite": 84.8}', {}) == {"composite": 84.8}
    assert _as_obj('["Python", "SQL"]', []) == ["Python", "SQL"]
    # Already-decoded values pass through untouched
    assert _as_obj({"a": 1}, {}) == {"a": 1}
    # None and unparseable strings fall back to the supplied default
    assert _as_obj(None, {}) == {}
    assert _as_obj("not json", {}) == {}

