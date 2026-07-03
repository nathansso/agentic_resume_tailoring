"""Tests for GitHub OAuth endpoints and token pass-through (issue #4)."""
from unittest.mock import patch, MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from database.models import User
import web.routers.auth_router as auth_router_module
import database.db as db_module


def _make_test_user(engine, token: str | None = None) -> User:
    with Session(engine) as session:
        user = User(
            name="Test User",
            email="oauth_test@example.com",
            username="oauth_test",
            github_access_token=token,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        uid = user.user_id
    with Session(engine) as session:
        return session.get(User, uid)


@pytest.fixture()
def client(isolated_engine, monkeypatch):
    """TestClient with isolated DB and auth bypassed."""
    monkeypatch.setattr(db_module, "engine", isolated_engine)
    monkeypatch.setattr(auth_router_module, "engine", isolated_engine)

    from web.app import create_app
    import web.auth as web_auth_module

    app = create_app()
    user = _make_test_user(isolated_engine)

    app.dependency_overrides[web_auth_module.get_current_user] = lambda: user
    return TestClient(app, raise_server_exceptions=True), user, isolated_engine


# ── /api/auth/github/status ──────────────────────────────────

def test_github_status_not_connected(client, monkeypatch):
    tc, user, engine = client
    monkeypatch.setattr(auth_router_module, "GITHUB_CLIENT_ID", None)
    resp = tc.get("/api/auth/github/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["connected"] is False
    assert data["oauth_configured"] is False
    assert data["github_username"] is None


def test_github_status_connected(client, monkeypatch):
    tc, user, engine = client
    monkeypatch.setattr(auth_router_module, "GITHUB_CLIENT_ID", "test-client-id")
    # Give the user a token
    with Session(engine) as session:
        u = session.get(User, user.user_id)
        u.github_access_token = "gho_testtoken"
        session.add(u)
        session.commit()
    # Refresh the override so the dependency returns the updated user
    import web.auth as web_auth_module
    from web.app import create_app
    app = create_app()
    with Session(engine) as session:
        fresh_user = session.get(User, user.user_id)
    app.dependency_overrides[web_auth_module.get_current_user] = lambda: fresh_user
    fresh_client = TestClient(app, raise_server_exceptions=True)
    resp = fresh_client.get("/api/auth/github/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["connected"] is True
    assert data["oauth_configured"] is True
    assert "github_username" in data


# ── DELETE /api/auth/github ───────────────────────────────────

def test_github_disconnect_clears_token(client):
    tc, user, engine = client
    # Set a token first
    with Session(engine) as session:
        u = session.get(User, user.user_id)
        u.github_access_token = "gho_existing"
        session.add(u)
        session.commit()

    resp = tc.delete("/api/auth/github")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    with Session(engine) as session:
        u = session.get(User, user.user_id)
        assert u.github_access_token is None


# ── GET /api/auth/github (start) — no client ID configured ───

def test_github_oauth_start_503_without_client_id(client, monkeypatch):
    tc, _user, _engine = client
    monkeypatch.setattr(auth_router_module, "GITHUB_CLIENT_ID", None)
    resp = tc.get("/api/auth/github", follow_redirects=False)
    assert resp.status_code == 503


def test_github_oauth_start_redirects_to_github(client, monkeypatch):
    tc, _user, _engine = client
    monkeypatch.setattr(auth_router_module, "GITHUB_CLIENT_ID", "my-client-id")
    resp = tc.get("/api/auth/github", follow_redirects=False)
    assert resp.status_code in (302, 307)
    assert "github.com/login/oauth/authorize" in resp.headers["location"]
    assert "my-client-id" in resp.headers["location"]


# ── disconnect clears username ────────────────────────────────

def test_github_disconnect_clears_username(client):
    tc, user, engine = client
    with Session(engine) as session:
        u = session.get(User, user.user_id)
        u.github_access_token = "gho_existing"
        u.github_username = "octocat"
        session.add(u)
        session.commit()

    resp = tc.delete("/api/auth/github")
    assert resp.status_code == 200

    with Session(engine) as session:
        u = session.get(User, user.user_id)
        assert u.github_access_token is None
        assert u.github_username is None


# ── ingest_github token pass-through ─────────────────────────

def test_ingest_github_uses_provided_token(monkeypatch):
    """ingest_github passes the supplied token to GitHubIngestor."""
    captured = {}

    class FakeIngestor:
        def __init__(self, username, token=None):
            captured["token"] = token

        def ingest(self):
            return []

    # ingest_github imports locally from these modules, so patch the source
    with patch("database.db.init_db"), \
         patch("database.user_utils.get_active_profile", return_value=None), \
         patch("ingestion.github.GitHubIngestor", FakeIngestor):
        from services import ingest_github
        ingest_github("testuser", token="gho_mytoken")

    assert captured.get("token") == "gho_mytoken"


def test_ingest_github_falls_back_to_env_token(monkeypatch):
    """ingest_github falls back to GITHUB_TOKEN env var when token not provided."""
    captured = {}

    class FakeIngestor:
        def __init__(self, username, token=None):
            captured["token"] = token

        def ingest(self):
            return []

    import config as cfg_module
    monkeypatch.setattr(cfg_module, "GITHUB_TOKEN", "env_pat")

    with patch("database.db.init_db"), \
         patch("database.user_utils.get_active_profile", return_value=None), \
         patch("ingestion.github.GitHubIngestor", FakeIngestor):
        from services import ingest_github
        ingest_github("testuser")

    assert captured.get("token") == "env_pat"
