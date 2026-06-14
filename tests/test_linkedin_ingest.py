"""Tests for Bright Data LinkedIn ingestion (issue #13).

Covers:
  1. ingest_brightdata happy path (mocked HTTP: trigger -> progress -> snapshot)
  2. ingest_brightdata requires the platform API key
  3. _brightdata_to_text flattening of a structured record
  4. services.ingest_linkedin records done/failed lifecycle on the user row
  5. profile update auto-schedules a background ingest when the URL changes
  6. POST /api/ingest/linkedin endpoint returns the service result
"""
import asyncio

import pytest
import requests
from sqlmodel import Session

import ingestion.linkedin as linkedin_module
from ingestion.linkedin import LinkedInIngestor, LinkedInIngestionError
from database.models import User


_RECORD = {
    "name": "Jane Dev",
    "city": "Seattle",
    "country_code": "US",
    "position": "ML Engineer",
    "about": "I build models.",
    "current_company": {"name": "Acme"},
    "experience": [
        {"title": "ML Engineer", "company": "Acme",
         "start_date": "2020", "end_date": "2024", "description": "Built stuff"},
    ],
    "education": [{"title": "UCSD", "degree": "BS", "field": "CS"}],
    "skills": ["Python", "PyTorch"],
}


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _install_fake_http(monkeypatch, record=_RECORD, status_sequence=("ready",)):
    """Patch requests.post/get on the linkedin module to simulate Bright Data."""
    calls = {"status_idx": 0}

    def fake_post(url, **kwargs):
        return _FakeResp({"snapshot_id": "snap1"})

    def fake_get(url, **kwargs):
        if "/progress/" in url:
            idx = min(calls["status_idx"], len(status_sequence) - 1)
            calls["status_idx"] += 1
            return _FakeResp({"status": status_sequence[idx]})
        if "/snapshot/" in url:
            return _FakeResp([record])
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(linkedin_module.requests, "post", fake_post)
    monkeypatch.setattr(linkedin_module.requests, "get", fake_get)
    monkeypatch.setattr(linkedin_module, "BRIGHTDATA_API_KEY", "test-key")


# ── ingest_brightdata ──────────────────────────────────────────────────────────

def test_ingest_brightdata_happy_path(monkeypatch):
    _install_fake_http(monkeypatch)
    result = LinkedInIngestor().ingest_brightdata("https://www.linkedin.com/in/janedev")

    assert result["source_type"] == "linkedin"
    assert result["source_file"] == "linkedin:https://www.linkedin.com/in/janedev"
    text = result["full_text"]
    assert "Jane Dev" in text
    assert "ML Engineer" in text
    assert "Experience" in text
    assert "Python" in text


def test_ingest_brightdata_normalizes_username(monkeypatch):
    _install_fake_http(monkeypatch)
    result = LinkedInIngestor().ingest_brightdata("janedev")
    assert result["source_file"] == "linkedin:https://www.linkedin.com/in/janedev"


def test_ingest_brightdata_requires_key(monkeypatch):
    monkeypatch.setattr(linkedin_module, "BRIGHTDATA_API_KEY", None)
    with pytest.raises(LinkedInIngestionError) as exc:
        LinkedInIngestor().ingest_brightdata("https://www.linkedin.com/in/x")
    assert "not configured" in str(exc.value)


def test_ingest_brightdata_failed_snapshot(monkeypatch):
    _install_fake_http(monkeypatch, status_sequence=("running", "failed"))
    with pytest.raises(LinkedInIngestionError):
        LinkedInIngestor().ingest_brightdata("https://www.linkedin.com/in/x")


# ── _brightdata_to_text ─────────────────────────────────────────────────────────

def test_brightdata_to_text_flattening():
    text = LinkedInIngestor()._brightdata_to_text(_RECORD, "https://x")
    assert "Name: Jane Dev" in text
    assert "Headline: ML Engineer" in text
    assert "Current company: Acme" in text
    assert "Education:" in text
    assert "UCSD" in text


# ── services.ingest_linkedin lifecycle ──────────────────────────────────────────

def test_service_ingest_linkedin_marks_done(isolated_engine, monkeypatch):
    import tui.services as services
    with Session(isolated_engine) as s:
        user = User(name="U", email="u@example.com")
        s.add(user); s.commit(); s.refresh(user)
        uid = user.user_id

    monkeypatch.setattr(
        LinkedInIngestor, "ingest_brightdata",
        lambda self, url: {"source_type": "linkedin",
                           "source_file": f"linkedin:{url}", "full_text": "x"},
    )
    monkeypatch.setattr(
        "agents.parser.ResumeParserAgent.parse_and_save", lambda self, data: None
    )

    services.ingest_linkedin("https://www.linkedin.com/in/janedev", uid)

    with Session(isolated_engine) as s:
        u = s.get(User, uid)
        assert u.linkedin_ingest_status == "done"
        assert u.linkedin_ingested_url == "https://www.linkedin.com/in/janedev"
        assert u.linkedin_ingested_at is not None


def test_service_ingest_linkedin_marks_failed(isolated_engine, monkeypatch):
    import tui.services as services
    with Session(isolated_engine) as s:
        user = User(name="U", email="u2@example.com")
        s.add(user); s.commit(); s.refresh(user)
        uid = user.user_id

    def _boom(self, url):
        raise LinkedInIngestionError("nope")
    monkeypatch.setattr(LinkedInIngestor, "ingest_brightdata", _boom)

    result = services.ingest_linkedin("https://www.linkedin.com/in/x", uid)
    assert "failed" in result.lower()
    with Session(isolated_engine) as s:
        u = s.get(User, uid)
        assert u.linkedin_ingest_status == "failed"
        assert u.linkedin_ingest_error == "nope"


# ── profile update auto-trigger ─────────────────────────────────────────────────

def test_profile_update_schedules_ingest(isolated_engine, monkeypatch):
    from fastapi import BackgroundTasks
    import web.routers.profile_router as pr

    with Session(isolated_engine) as s:
        user = User(name="U", email="u3@example.com")
        s.add(user); s.commit(); s.refresh(user)

    monkeypatch.setattr(pr, "BRIGHTDATA_API_KEY", "test-key")
    bg = BackgroundTasks()
    body = pr.UpdateProfileBody(linkedin_url="https://www.linkedin.com/in/janedev")
    pr.update_profile(body, bg, user=user)

    assert len(bg.tasks) == 1


def test_profile_update_no_trigger_when_unchanged(isolated_engine, monkeypatch):
    from fastapi import BackgroundTasks
    import web.routers.profile_router as pr

    with Session(isolated_engine) as s:
        user = User(name="U", email="u4@example.com",
                    linkedin_ingested_url="https://www.linkedin.com/in/janedev")
        s.add(user); s.commit(); s.refresh(user)

    monkeypatch.setattr(pr, "BRIGHTDATA_API_KEY", "test-key")
    bg = BackgroundTasks()
    body = pr.UpdateProfileBody(linkedin_url="https://www.linkedin.com/in/janedev/")
    pr.update_profile(body, bg, user=user)

    assert len(bg.tasks) == 0


def test_profile_update_no_trigger_without_key(isolated_engine, monkeypatch):
    from fastapi import BackgroundTasks
    import web.routers.profile_router as pr

    with Session(isolated_engine) as s:
        user = User(name="U", email="u5@example.com")
        s.add(user); s.commit(); s.refresh(user)

    monkeypatch.setattr(pr, "BRIGHTDATA_API_KEY", None)
    bg = BackgroundTasks()
    body = pr.UpdateProfileBody(linkedin_url="https://www.linkedin.com/in/janedev")
    pr.update_profile(body, bg, user=user)

    assert len(bg.tasks) == 0


# ── ingest endpoint ─────────────────────────────────────────────────────────────

def test_ingest_linkedin_endpoint(isolated_engine, monkeypatch):
    import web.routers.ingest_router as ir

    with Session(isolated_engine) as s:
        user = User(name="U", email="u6@example.com")
        s.add(user); s.commit(); s.refresh(user)

    monkeypatch.setattr(ir.services, "ingest_linkedin",
                        lambda url, user_id: "LinkedIn profile ingested.")
    monkeypatch.setattr(ir, "_write_active_profile", lambda uid: None)

    body = ir.LinkedInBody(url="https://www.linkedin.com/in/janedev")
    out = asyncio.run(ir.ingest_linkedin(body, user=user))
    assert out == {"result": "LinkedIn profile ingested."}
