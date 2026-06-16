"""Tests for Bright Data LinkedIn ingestion (issue #13).

Covers:
  1. ingest_brightdata happy path (mocked synchronous /scrape call)
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


def _install_fake_http(monkeypatch, record=_RECORD):
    """Patch requests.post on the linkedin module to simulate Bright Data /scrape."""
    def fake_post(url, **kwargs):
        assert "/scrape" in url, f"unexpected URL: {url}"
        return _FakeResp([record])

    monkeypatch.setattr(linkedin_module.requests, "post", fake_post)
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


def test_ingest_brightdata_error_record(monkeypatch):
    _install_fake_http(monkeypatch, record={"error": "profile is private"})
    with pytest.raises(LinkedInIngestionError):
        LinkedInIngestor().ingest_brightdata("https://www.linkedin.com/in/x")


def test_ingest_brightdata_request_failure(monkeypatch):
    def boom(url, **kwargs):
        raise requests.RequestException("connection refused")
    monkeypatch.setattr(linkedin_module.requests, "post", boom)
    monkeypatch.setattr(linkedin_module, "BRIGHTDATA_API_KEY", "test-key")
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


# ── sign-in auto-refresh ────────────────────────────────────────────────────────

def _login_user(isolated_engine, monkeypatch, **fields):
    """Build a user and a BackgroundTasks, with auth_router pointed at the test DB."""
    from fastapi import BackgroundTasks
    import web.routers.auth_router as ar
    import web.routers.dependencies as deps

    monkeypatch.setattr(ar, "BRIGHTDATA_API_KEY", "test-key")
    monkeypatch.setattr(ar, "engine", isolated_engine)
    monkeypatch.setattr(deps, "LINKEDIN_DAILY_LIMIT", 2)
    monkeypatch.setattr(deps, "OWNER_EMAIL", "")

    with Session(isolated_engine) as s:
        user = User(name="U", **fields)
        s.add(user); s.commit(); s.refresh(user)
    return ar, BackgroundTasks(), user


def test_login_refresh_when_stale(isolated_engine, monkeypatch):
    ar, bg, user = _login_user(
        isolated_engine, monkeypatch,
        email="login1@example.com",
        linkedin_url="https://www.linkedin.com/in/janedev",
    )
    ar._maybe_refresh_linkedin_on_login(bg, user)
    assert len(bg.tasks) == 1


def test_login_no_refresh_when_fresh_today(isolated_engine, monkeypatch):
    from datetime import datetime, timezone
    ar, bg, user = _login_user(
        isolated_engine, monkeypatch,
        email="login2@example.com",
        linkedin_url="https://www.linkedin.com/in/janedev",
        linkedin_ingested_at=datetime.now(timezone.utc),
    )
    ar._maybe_refresh_linkedin_on_login(bg, user)
    assert len(bg.tasks) == 0


def test_login_no_refresh_without_url(isolated_engine, monkeypatch):
    ar, bg, user = _login_user(
        isolated_engine, monkeypatch, email="login3@example.com",
    )
    ar._maybe_refresh_linkedin_on_login(bg, user)
    assert len(bg.tasks) == 0


def test_login_no_refresh_without_key(isolated_engine, monkeypatch):
    ar, bg, user = _login_user(
        isolated_engine, monkeypatch,
        email="login4@example.com",
        linkedin_url="https://www.linkedin.com/in/janedev",
    )
    monkeypatch.setattr(ar, "BRIGHTDATA_API_KEY", None)
    ar._maybe_refresh_linkedin_on_login(bg, user)
    assert len(bg.tasks) == 0


def test_login_no_refresh_when_quota_exhausted(isolated_engine, monkeypatch):
    import web.routers.dependencies as deps
    ar, bg, user = _login_user(
        isolated_engine, monkeypatch,
        email="login5@example.com",
        linkedin_url="https://www.linkedin.com/in/janedev",
    )
    monkeypatch.setattr(deps, "LINKEDIN_DAILY_LIMIT", 2)
    for _ in range(2):
        with Session(isolated_engine) as s:
            deps.increment_linkedin_usage(user.user_id, s)
    ar._maybe_refresh_linkedin_on_login(bg, user)
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
    with Session(isolated_engine) as s:
        out = asyncio.run(ir.ingest_linkedin(body, user=user, _=None, session=s))
    assert out == {"result": "LinkedIn profile ingested."}
    # The attempt was counted against the LinkedIn quota.
    with Session(isolated_engine) as s:
        from database.models import AIUsage
        rows = s.exec(
            __import__("sqlmodel").select(AIUsage).where(AIUsage.kind == "linkedin")
        ).all()
    assert len(rows) == 1 and rows[0].call_count == 1


def test_ingest_linkedin_endpoint_blocks_over_limit(isolated_engine, monkeypatch):
    """The manual endpoint's quota dependency raises 429 once the cap is hit."""
    from fastapi import HTTPException
    import web.routers.dependencies as deps

    monkeypatch.setattr(deps, "LINKEDIN_DAILY_LIMIT", 2)
    monkeypatch.setattr(deps, "OWNER_EMAIL", "")
    with Session(isolated_engine) as s:
        user = User(name="U", email="u7@example.com")
        s.add(user); s.commit(); s.refresh(user)

    for _ in range(2):
        with Session(isolated_engine) as s:
            deps.increment_linkedin_usage(user.user_id, s)

    async def _run():
        with Session(isolated_engine) as s:
            with pytest.raises(HTTPException) as exc:
                await deps.check_linkedin_quota(user=user, session=s)
        assert exc.value.status_code == 429
    asyncio.run(_run())


def test_linkedin_quota_separate_from_ai(isolated_engine, monkeypatch):
    """LinkedIn and AI usage are counted independently (kind discriminator)."""
    import web.routers.dependencies as deps
    monkeypatch.setattr(deps, "OWNER_EMAIL", "")
    with Session(isolated_engine) as s:
        user = User(name="U", email="u8@example.com")
        s.add(user); s.commit(); s.refresh(user)

    with Session(isolated_engine) as s:
        deps.increment_ai_usage(user.user_id, s)
        deps.increment_ai_usage(user.user_id, s)
        deps.increment_linkedin_usage(user.user_id, s)

    with Session(isolated_engine) as s:
        assert deps._usage_row(s, user.user_id, "ai").call_count == 2
        assert deps._usage_row(s, user.user_id, "linkedin").call_count == 1


def test_linkedin_owner_exempt(isolated_engine, monkeypatch):
    import web.routers.dependencies as deps
    monkeypatch.setattr(deps, "LINKEDIN_DAILY_LIMIT", 1)
    monkeypatch.setattr(deps, "OWNER_EMAIL", "owner@example.com")
    with Session(isolated_engine) as s:
        owner = User(name="O", email="owner@example.com")
        s.add(owner); s.commit(); s.refresh(owner)

    for _ in range(5):
        with Session(isolated_engine) as s:
            deps.increment_linkedin_usage(owner.user_id, s)

    with Session(isolated_engine) as s:
        assert deps.linkedin_quota_remaining(s, owner.user_id, owner.email) is True


def test_profile_auto_trigger_skips_when_quota_exhausted(isolated_engine, monkeypatch):
    """The background auto-trigger self-guards against the LinkedIn cap."""
    import web.routers.profile_router as pr
    import web.routers.dependencies as deps

    monkeypatch.setattr(deps, "LINKEDIN_DAILY_LIMIT", 1)
    monkeypatch.setattr(deps, "OWNER_EMAIL", "")
    monkeypatch.setattr(pr, "engine", isolated_engine)
    with Session(isolated_engine) as s:
        user = User(name="U", email="u9@example.com")
        s.add(user); s.commit(); s.refresh(user)
        uid = user.user_id

    with Session(isolated_engine) as s:
        deps.increment_linkedin_usage(uid, s)  # exhaust the cap

    called = {"n": 0}
    monkeypatch.setattr(pr.services, "ingest_linkedin",
                        lambda url, user_id: called.__setitem__("n", called["n"] + 1))

    pr._linkedin_ingest_task(uid, "https://www.linkedin.com/in/x", "u9@example.com")
    assert called["n"] == 0  # scrape skipped — over quota
