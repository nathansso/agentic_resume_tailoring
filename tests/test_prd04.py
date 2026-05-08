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
        lambda self, content, job_title="": b"%PDF-1.4 stub",
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
        lambda self, content, job_title="": b"%PDF-1.4 stub",
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
