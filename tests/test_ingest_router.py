"""Tests for /api/ingest router behavior added in issue #68.

Covers:
  1. POST /api/ingest/github defaults the username from the connected OAuth
     account (user.github_username) when the body omits it.
  2. An explicit username in the body still wins over the connected account.
  3. 400 when neither a body username nor a connected account is available.
  4. POST /api/ingest/resume passes the original upload filename through to
     services.ingest_resume_file so results don't show the server temp name.
"""
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from database.models import User
import database.db as db_module
import web.routers.ingest_router as ingest_router_module


def _make_user(engine, github_username: str | None = None,
               github_token: str | None = None) -> User:
    with Session(engine) as session:
        user = User(
            name="Ingest Test",
            email=f"ingest_{uuid4().hex[:8]}@example.com",
            username=f"ingest_{uuid4().hex[:8]}",
            github_username=github_username,
            github_access_token=github_token,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        uid = user.user_id
    with Session(engine) as session:
        return session.get(User, uid)


@pytest.fixture()
def make_client(isolated_engine, monkeypatch, tmp_path):
    """Factory: TestClient with isolated DB and auth bypassed for a given user.
    (The router binds the acting user per request context — issue #73 — so no
    active-profile file redirection is needed.)"""
    monkeypatch.setattr(db_module, "engine", isolated_engine)

    from web.app import create_app
    import web.auth as web_auth_module

    def _make(user: User) -> TestClient:
        app = create_app()
        app.dependency_overrides[web_auth_module.get_current_user] = lambda: user
        return TestClient(app, raise_server_exceptions=True)

    return _make


def test_github_ingest_defaults_to_connected_username(make_client, isolated_engine, monkeypatch):
    user = _make_user(isolated_engine, github_username="octocat", github_token="gho_x")
    captured = {}

    def fake_ingest(username, token=None):
        captured["username"] = username
        captured["token"] = token
        return "ok"

    monkeypatch.setattr(ingest_router_module.services, "ingest_github", fake_ingest)

    resp = make_client(user).post("/api/ingest/github", json={})
    assert resp.status_code == 200
    assert captured["username"] == "octocat"
    assert captured["token"] == "gho_x"


def test_github_ingest_explicit_username_wins(make_client, isolated_engine, monkeypatch):
    user = _make_user(isolated_engine, github_username="octocat")
    captured = {}

    def fake_ingest(username, token=None):
        captured["username"] = username
        return "ok"

    monkeypatch.setattr(ingest_router_module.services, "ingest_github", fake_ingest)

    resp = make_client(user).post("/api/ingest/github", json={"username": "someone-else"})
    assert resp.status_code == 200
    assert captured["username"] == "someone-else"


def test_github_ingest_400_without_any_username(make_client, isolated_engine, monkeypatch):
    user = _make_user(isolated_engine, github_username=None)

    def fake_ingest(username, token=None):  # must never be called
        raise AssertionError("ingest_github should not run without a username")

    monkeypatch.setattr(ingest_router_module.services, "ingest_github", fake_ingest)

    resp = make_client(user).post("/api/ingest/github", json={})
    assert resp.status_code == 400
    assert "username" in resp.json()["detail"].lower()


def test_resume_ingest_passes_original_filename(make_client, isolated_engine, monkeypatch):
    user = _make_user(isolated_engine)
    captured = {}

    def fake_ingest(file_path, display_name=None):
        captured["display_name"] = display_name
        return f"Ingested: {display_name}"

    monkeypatch.setattr(ingest_router_module.services, "ingest_resume_file", fake_ingest)

    resp = make_client(user).post(
        "/api/ingest/resume",
        files={"file": ("My Resume.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )
    assert resp.status_code == 200
    assert captured["display_name"] == "My Resume.pdf"
    assert "My Resume.pdf" in resp.json()["result"]


def test_linkedin_pdf_ingest_passes_original_filename(make_client, isolated_engine, monkeypatch):
    user = _make_user(isolated_engine)
    captured = {}

    def fake_ingest(file_path, display_name=None):
        captured["display_name"] = display_name
        return f"Ingested: {display_name}"

    monkeypatch.setattr(ingest_router_module.services, "ingest_linkedin_pdf", fake_ingest)

    resp = make_client(user).post(
        "/api/ingest/linkedin/pdf",
        files={"file": ("Profile.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )
    assert resp.status_code == 200
    assert captured["display_name"] == "Profile.pdf"
