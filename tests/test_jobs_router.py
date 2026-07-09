"""Issue #70 — jobs router: JD at creation, per-job tailor budget, revision notes."""
from uuid import uuid4

import pytest
from sqlmodel import Session

from database.models import JobDescription, User, UserJobResult


def _seed_user(engine, name: str) -> User:
    with Session(engine) as s:
        user = User(name=name, email=f"{name.lower()}_{uuid4().hex[:8]}@example.com")
        s.add(user)
        s.commit()
        s.refresh(user)
        return user


def _make_job(engine, user_id, status="created", description="", retailor_count=0):
    with Session(engine) as s:
        job = JobDescription(
            title="Eng", company="Acme", description=description,
            status=status, user_id=user_id, retailor_count=retailor_count,
        )
        s.add(job)
        s.commit()
        s.refresh(job)
        return job


def _make_result(engine, user_id, job_id) -> UserJobResult:
    with Session(engine) as s:
        result = UserJobResult(user_id=user_id, job_id=job_id)
        s.add(result)
        s.commit()
        s.refresh(result)
        return result


@pytest.fixture()
def jobs_client(isolated_engine, monkeypatch):
    """TestClient factory with isolated DB and auth overridden per user."""
    import database.db as db_module
    monkeypatch.setattr(db_module, "engine", isolated_engine)
    import web.routers.jobs_router as jobs_router_module
    monkeypatch.setattr(jobs_router_module, "engine", isolated_engine)
    import agents.formatter as fmt_module
    monkeypatch.setattr(fmt_module, "engine", isolated_engine)

    from fastapi.testclient import TestClient
    from web.app import create_app
    import web.auth as web_auth_module

    def _make(user: User) -> TestClient:
        app = create_app()
        app.dependency_overrides[web_auth_module.get_current_user] = lambda: user
        return TestClient(app, raise_server_exceptions=True)

    return _make


# ── Create with description ────────────────────────────────────────────────────

def test_create_job_with_description_persists(isolated_engine, jobs_client):
    alice = _seed_user(isolated_engine, "Alice")
    resp = jobs_client(alice).post(
        "/api/jobs/",
        json={"title": "SWE", "company": "Acme", "description": "  We need Python.  "},
    )
    assert resp.status_code == 200
    job_id = resp.json()["job_id"]
    with Session(isolated_engine) as s:
        from uuid import UUID
        job = s.get(JobDescription, UUID(job_id))
        assert job.description == "We need Python."
        assert job.status == "created"


def test_create_job_without_description_backcompat(isolated_engine, jobs_client):
    alice = _seed_user(isolated_engine, "Alice")
    resp = jobs_client(alice).post("/api/jobs/", json={"title": "SWE", "company": "Acme"})
    assert resp.status_code == 200
    with Session(isolated_engine) as s:
        from uuid import UUID
        job = s.get(JobDescription, UUID(resp.json()["job_id"]))
        assert job.description == ""


# ── Retailor budget surfaced in detail ─────────────────────────────────────────

def test_job_detail_includes_retailor_budget(isolated_engine, jobs_client, monkeypatch):
    monkeypatch.setenv("JOB_TAILOR_LIMIT", "3")
    alice = _seed_user(isolated_engine, "Alice")
    job = _make_job(isolated_engine, alice.user_id)
    resp = jobs_client(alice).get(f"/api/jobs/{job.job_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["retailor_count"] == 0
    assert body["retailor_limit"] == 3


# ── Explainability surfaced, internal keys filtered ────────────────────────────

def test_job_detail_surfaces_explainability_and_filters_underscore_keys(
    isolated_engine, jobs_client,
):
    alice = _seed_user(isolated_engine, "Alice")
    job = _make_job(isolated_engine, alice.user_id, status="tailored", description="JD")
    explainability = {
        "matched": ["python"], "emphasized": ["pandas (≈numpy)"],
        "inferred": ["docker"], "missing": ["kubernetes"], "ats_score": 72.5,
    }
    with Session(isolated_engine) as s:
        result = UserJobResult(
            user_id=alice.user_id, job_id=job.job_id,
            matched_skills={"python": {"match_type": "direct"}, "_explainability": explainability},
            missing_skills=["kubernetes"],
        )
        s.add(result)
        s.commit()

    body = jobs_client(alice).get(f"/api/jobs/{job.job_id}").json()
    assert body["explainability"] == explainability
    assert body["matched_skills"] == ["python"]
    assert not any(k.startswith("_") for k in body["matched_skills"])


def test_job_detail_explainability_null_when_absent(isolated_engine, jobs_client):
    alice = _seed_user(isolated_engine, "Alice")
    job = _make_job(isolated_engine, alice.user_id)
    _make_result(isolated_engine, alice.user_id, job.job_id)
    body = jobs_client(alice).get(f"/api/jobs/{job.job_id}").json()
    assert body["explainability"] is None


# ── Tailor: cap, revision notes, increment ─────────────────────────────────────

class _FakeTailorAgent:
    captured: dict = {}

    def __init__(self):
        pass

    def tailor(self, user_id, job_id, result_id, resume_text="", revision_notes=""):
        _FakeTailorAgent.captured = {
            "result_id": result_id,
            "revision_notes": revision_notes,
        }
        return {"experiences": []}


def test_tailor_at_cap_returns_409(isolated_engine, jobs_client, monkeypatch):
    monkeypatch.setenv("JOB_TAILOR_LIMIT", "2")
    alice = _seed_user(isolated_engine, "Alice")
    job = _make_job(isolated_engine, alice.user_id, status="analyzed",
                    description="JD", retailor_count=2)
    _make_result(isolated_engine, alice.user_id, job.job_id)

    resp = jobs_client(alice).post(f"/api/jobs/{job.job_id}/tailor")
    assert resp.status_code == 409
    assert "Re-tailor limit reached (2/2)" in resp.json()["detail"]


def test_tailor_passes_revision_notes_and_increments(isolated_engine, jobs_client, monkeypatch):
    import agents.tailor as tailor_module
    monkeypatch.setattr(tailor_module, "ResumeTailorAgent", _FakeTailorAgent)
    _FakeTailorAgent.captured = {}

    alice = _seed_user(isolated_engine, "Alice")
    job = _make_job(isolated_engine, alice.user_id, status="analyzed", description="JD")
    _make_result(isolated_engine, alice.user_id, job.job_id)

    resp = jobs_client(alice).post(
        f"/api/jobs/{job.job_id}/tailor",
        json={"revision_notes": "emphasize Python"},
    )
    assert resp.status_code == 200
    assert _FakeTailorAgent.captured["revision_notes"] == "emphasize Python"
    body = resp.json()
    assert body["retailor_count"] == 1
    assert body["retailor_limit"] >= 1
    with Session(isolated_engine) as s:
        assert s.get(JobDescription, job.job_id).retailor_count == 1


def test_tailor_without_body_backcompat(isolated_engine, jobs_client, monkeypatch):
    """Legacy POST with no JSON body still tailors (empty revision notes)."""
    import agents.tailor as tailor_module
    monkeypatch.setattr(tailor_module, "ResumeTailorAgent", _FakeTailorAgent)
    _FakeTailorAgent.captured = {}

    alice = _seed_user(isolated_engine, "Alice")
    job = _make_job(isolated_engine, alice.user_id, status="analyzed", description="JD")
    _make_result(isolated_engine, alice.user_id, job.job_id)

    resp = jobs_client(alice).post(f"/api/jobs/{job.job_id}/tailor")
    assert resp.status_code == 200
    assert _FakeTailorAgent.captured["revision_notes"] == ""


def test_tailor_other_users_job_403(isolated_engine, jobs_client):
    alice = _seed_user(isolated_engine, "Alice")
    bob = _seed_user(isolated_engine, "Bob")
    job = _make_job(isolated_engine, alice.user_id, status="analyzed", description="JD")
    _make_result(isolated_engine, alice.user_id, job.job_id)

    resp = jobs_client(bob).post(f"/api/jobs/{job.job_id}/tailor")
    assert resp.status_code == 403


# ── Issue #71: .tex editing endpoints ──────────────────────────────────────────

_TAILORED = {
    "experiences": [{
        "title": "Dev", "company": "Acme", "start_date": "2020", "end_date": "Now",
        "bullets": ["Built the thing", "Shipped the thing"],
    }],
    "projects": [],
    "skills_emphasized": [],
}


def _make_tailored_result(engine, user_id, job_id, edited_tex=None) -> UserJobResult:
    with Session(engine) as s:
        result = UserJobResult(
            user_id=user_id, job_id=job_id,
            tailored_resume_content=dict(_TAILORED), edited_tex=edited_tex,
        )
        s.add(result)
        s.commit()
        s.refresh(result)
        return result


def test_get_tex_seeds_from_tailored_content(isolated_engine, jobs_client):
    alice = _seed_user(isolated_engine, "Alice")
    job = _make_job(isolated_engine, alice.user_id, status="tailored", description="JD")
    _make_tailored_result(isolated_engine, alice.user_id, job.job_id)

    resp = jobs_client(alice).get(f"/api/jobs/{job.job_id}/tex")
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "generated"
    assert body["updated_at"] is None
    assert "%% ART-SECTION:" in body["tex"]
    assert r"\resumeItem{Built the thing}" in body["tex"]


def test_get_tex_without_tailoring_422(isolated_engine, jobs_client):
    alice = _seed_user(isolated_engine, "Alice")
    job = _make_job(isolated_engine, alice.user_id, status="created")
    assert jobs_client(alice).get(f"/api/jobs/{job.job_id}/tex").status_code == 422


def test_put_then_get_tex_roundtrip(isolated_engine, jobs_client):
    alice = _seed_user(isolated_engine, "Alice")
    job = _make_job(isolated_engine, alice.user_id, status="tailored", description="JD")
    _make_tailored_result(isolated_engine, alice.user_id, job.job_id)
    client = jobs_client(alice)

    edited = "\\documentclass{article}\\begin{document}My edits\\end{document}"
    put = client.put(f"/api/jobs/{job.job_id}/tex", json={"tex": edited})
    assert put.status_code == 200
    assert put.json()["saved"] is True

    got = client.get(f"/api/jobs/{job.job_id}/tex").json()
    assert got["source"] == "edited"
    assert got["tex"] == edited
    assert got["updated_at"] is not None

    # Job detail reflects the manual-edit state
    assert client.get(f"/api/jobs/{job.job_id}").json()["has_manual_edits"] is True


def test_put_tex_empty_422(isolated_engine, jobs_client):
    alice = _seed_user(isolated_engine, "Alice")
    job = _make_job(isolated_engine, alice.user_id, status="tailored", description="JD")
    _make_tailored_result(isolated_engine, alice.user_id, job.job_id)
    resp = jobs_client(alice).put(f"/api/jobs/{job.job_id}/tex", json={"tex": "   "})
    assert resp.status_code == 422


def test_delete_tex_discards_manual_edits(isolated_engine, jobs_client):
    alice = _seed_user(isolated_engine, "Alice")
    job = _make_job(isolated_engine, alice.user_id, status="tailored", description="JD")
    _make_tailored_result(isolated_engine, alice.user_id, job.job_id, edited_tex="old edits")
    client = jobs_client(alice)

    assert client.delete(f"/api/jobs/{job.job_id}/tex").status_code == 200
    got = client.get(f"/api/jobs/{job.job_id}/tex").json()
    assert got["source"] == "generated"


def test_tex_endpoints_reject_other_user(isolated_engine, jobs_client):
    alice = _seed_user(isolated_engine, "Alice")
    bob = _seed_user(isolated_engine, "Bob")
    job = _make_job(isolated_engine, alice.user_id, status="tailored", description="JD")
    _make_tailored_result(isolated_engine, alice.user_id, job.job_id)
    client = jobs_client(bob)

    assert client.get(f"/api/jobs/{job.job_id}/tex").status_code == 403
    assert client.put(f"/api/jobs/{job.job_id}/tex", json={"tex": "x"}).status_code == 403
    assert client.post(f"/api/jobs/{job.job_id}/preview", json={"tex": "x"}).status_code == 403


def test_preview_returns_pdf(isolated_engine, jobs_client, monkeypatch):
    import agents.formatter as fmt_module
    monkeypatch.setattr(fmt_module, "_compile_tex_to_pdf", lambda tex: b"%PDF-fake")

    alice = _seed_user(isolated_engine, "Alice")
    job = _make_job(isolated_engine, alice.user_id, status="tailored", description="JD")

    resp = jobs_client(alice).post(f"/api/jobs/{job.job_id}/preview", json={"tex": "\\documentclass{article}"})
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content == b"%PDF-fake"


def test_preview_compile_error_returns_422_with_log(isolated_engine, jobs_client, monkeypatch):
    import agents.formatter as fmt_module

    def _boom(tex):
        raise RuntimeError("pdflatex failed (exit 1):\n! Undefined control sequence.")

    monkeypatch.setattr(fmt_module, "_compile_tex_to_pdf", _boom)

    alice = _seed_user(isolated_engine, "Alice")
    job = _make_job(isolated_engine, alice.user_id, status="tailored", description="JD")

    resp = jobs_client(alice).post(f"/api/jobs/{job.job_id}/preview", json={"tex": "\\broken"})
    assert resp.status_code == 422
    assert "Undefined control sequence" in resp.json()["detail"]


def test_export_tex_prefers_edited(isolated_engine, jobs_client):
    alice = _seed_user(isolated_engine, "Alice")
    job = _make_job(isolated_engine, alice.user_id, status="tailored", description="JD")
    _make_tailored_result(isolated_engine, alice.user_id, job.job_id, edited_tex="MY EDITED TEX")

    resp = jobs_client(alice).get(f"/api/jobs/{job.job_id}/export?format=tex")
    assert resp.status_code == 200
    assert resp.text == "MY EDITED TEX"


def test_export_pdf_compiles_edited_tex(isolated_engine, jobs_client, monkeypatch):
    import agents.formatter as fmt_module
    compiled = {}

    def _fake_compile(tex):
        compiled["tex"] = tex
        return b"%PDF-edited"

    monkeypatch.setattr(fmt_module, "_compile_tex_to_pdf", _fake_compile)

    alice = _seed_user(isolated_engine, "Alice")
    job = _make_job(isolated_engine, alice.user_id, status="tailored", description="JD")
    _make_tailored_result(isolated_engine, alice.user_id, job.job_id, edited_tex="MY EDITED TEX")

    resp = jobs_client(alice).get(f"/api/jobs/{job.job_id}/export?format=pdf")
    assert resp.status_code == 200
    assert resp.content == b"%PDF-edited"
    assert compiled["tex"] == "MY EDITED TEX"


def test_export_tex_without_edits_still_generates(isolated_engine, jobs_client):
    alice = _seed_user(isolated_engine, "Alice")
    job = _make_job(isolated_engine, alice.user_id, status="tailored", description="JD")
    _make_tailored_result(isolated_engine, alice.user_id, job.job_id)

    resp = jobs_client(alice).get(f"/api/jobs/{job.job_id}/export?format=tex")
    assert resp.status_code == 200
    assert r"\resumeItem{Built the thing}" in resp.text


# ── Revision notes persisted by the real agent ─────────────────────────────────

def test_tailor_agent_persists_revision_notes(isolated_engine, monkeypatch):
    """ResumeTailorAgent.tailor() writes revision_notes and discards manual
    .tex edits (issue #71 — re-tailoring supersedes them)."""
    import agents.formatter as fmt_module
    import agents.tailor as tailor_module
    from conftest import _seed_user_and_skill

    monkeypatch.setattr(tailor_module, "engine", isolated_engine)
    monkeypatch.setattr(tailor_module, "get_llm", lambda *a, **kw: object())
    # Skip the one-page fit (a real LaTeX compile) — not under test here.
    monkeypatch.setattr(
        fmt_module.ResumeFormatterAgent,
        "fit_content_to_one_page",
        lambda self, content, section_order=None: content,
    )

    user = _seed_user_and_skill(isolated_engine)
    job = _make_job(isolated_engine, user.user_id, status="analyzed", description="Python JD")
    result = _make_result(isolated_engine, user.user_id, job.job_id)
    with Session(isolated_engine) as s:
        stored = s.get(UserJobResult, result.result_id)
        stored.edited_tex = "manual edits about to be superseded"
        s.add(stored)
        s.commit()

    agent = tailor_module.ResumeTailorAgent()

    class FakeGraph:
        def invoke(self, state):
            assert state["revision_notes"] == "make it punchier"
            return {**state,
                    "tailored_content": {"experiences": [], "projects": [],
                                         "skills_emphasized": []},
                    "evaluation": {}, "attempt": 1, "done": True}

    agent.graph = FakeGraph()
    agent.tailor(user.user_id, job.job_id, result.result_id,
                 revision_notes="make it punchier")

    with Session(isolated_engine) as s:
        stored = s.get(UserJobResult, result.result_id)
        assert stored.revision_notes == "make it punchier"
        assert stored.edited_tex is None
        assert stored.edited_tex_updated_at is None
