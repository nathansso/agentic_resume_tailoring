"""Auth migration + password recovery tests (issue #61).

Supabase is never available in the test environment, so the Supabase helpers
are patched on ``web.routers.auth_router``. These tests exercise both the
Supabase-only production path and the offline dev fallback.
"""
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from database.models import User
import web.routers.auth_router as auth_router_module
import database.db as db_module


@pytest.fixture()
def client(isolated_engine, monkeypatch):
    """TestClient with an isolated DB. Public auth endpoints — no auth override."""
    monkeypatch.setattr(db_module, "engine", isolated_engine)
    monkeypatch.setattr(auth_router_module, "engine", isolated_engine)
    from web.app import create_app
    app = create_app()
    return TestClient(app, raise_server_exceptions=True), isolated_engine


def _make_user(engine, *, email, username, supabase_uid=None, password_hash=None):
    with Session(engine) as session:
        user = User(
            name="Test User", email=email, username=username,
            supabase_uid=supabase_uid, password_hash=password_hash,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        return user.user_id


def _force_supabase(monkeypatch, enabled: bool):
    monkeypatch.setattr(auth_router_module, "supabase_configured", lambda: enabled)


# ── capabilities ──────────────────────────────────────────────

def test_capabilities_local(client, monkeypatch):
    tc, _ = client
    _force_supabase(monkeypatch, False)
    data = tc.get("/api/auth/capabilities").json()
    assert data == {"password_reset_enabled": False, "auth_mode": "local"}


def test_capabilities_supabase(client, monkeypatch):
    tc, _ = client
    _force_supabase(monkeypatch, True)
    data = tc.get("/api/auth/capabilities").json()
    assert data == {"password_reset_enabled": True, "auth_mode": "supabase"}


# ── dev-fallback login (email + local hash) ───────────────────

def test_local_register_then_login_by_email(client, monkeypatch):
    tc, engine = client
    _force_supabase(monkeypatch, False)
    r = tc.post("/api/auth/register", json={
        "name": "Ada", "email": "Ada@Example.com", "username": "ada", "password": "hunter2pass",
    })
    assert r.status_code == 200, r.text
    # Email is normalised to lowercase.
    assert r.json()["user"]["email"] == "ada@example.com"

    ok = tc.post("/api/auth/login", json={"email": "ada@example.com", "password": "hunter2pass"})
    assert ok.status_code == 200
    assert ok.cookies.get("access_token")


def test_local_login_wrong_password_generic_401(client, monkeypatch):
    tc, engine = client
    _force_supabase(monkeypatch, False)
    from database.auth import hash_password
    _make_user(engine, email="bob@example.com", username="bob",
               password_hash=hash_password("correctpass"))
    r = tc.post("/api/auth/login", json={"email": "bob@example.com", "password": "wrongpass"})
    assert r.status_code == 401
    assert r.json()["detail"] == "Invalid email or password"


def test_local_login_unknown_email_is_indistinguishable(client, monkeypatch):
    """Unknown email returns the same generic error as a wrong password (no enumeration)."""
    tc, _ = client
    _force_supabase(monkeypatch, False)
    r = tc.post("/api/auth/login", json={"email": "nobody@example.com", "password": "whatever1"})
    assert r.status_code == 401
    assert r.json()["detail"] == "Invalid email or password"


# ── Supabase-mode login ───────────────────────────────────────

def test_supabase_login_sets_jwt_and_backfills_uid(client, monkeypatch):
    tc, engine = client
    _force_supabase(monkeypatch, True)
    # User exists locally by email but has no supabase_uid yet (pre-migration account).
    uid = _make_user(engine, email="carol@example.com", username="carol")
    monkeypatch.setattr(auth_router_module, "supabase_sign_in", lambda e, p: {
        "supabase_uid": "sb-carol", "access_token": "jwt-abc",
        "refresh_token": "ref-abc", "expires_at": 9999999999,
    })
    r = tc.post("/api/auth/login", json={"email": "carol@example.com", "password": "pw12345678"})
    assert r.status_code == 200, r.text
    assert r.cookies.get("access_token") == "jwt-abc"
    with Session(engine) as s:
        assert s.get(User, uid).supabase_uid == "sb-carol"


def test_supabase_login_bad_credentials_generic_401(client, monkeypatch):
    tc, _ = client
    _force_supabase(monkeypatch, True)
    monkeypatch.setattr(auth_router_module, "supabase_sign_in", lambda e, p: None)
    r = tc.post("/api/auth/login", json={"email": "carol@example.com", "password": "bad"})
    assert r.status_code == 401
    assert r.json()["detail"] == "Invalid email or password"


# ── Supabase-mode register stores no local password ───────────

def test_supabase_register_stores_no_local_hash(client, monkeypatch):
    tc, engine = client
    _force_supabase(monkeypatch, True)
    monkeypatch.setattr(auth_router_module, "supabase_sign_up", lambda e, p, email_redirect_to=None: {
        "supabase_uid": "sb-dave", "access_token": "jwt-d",
        "refresh_token": "ref-d", "expires_at": 9999999999,
    })
    r = tc.post("/api/auth/register", json={
        "name": "Dave", "email": "dave@example.com", "username": "dave", "password": "pw12345678",
    })
    assert r.status_code == 200, r.text
    assert r.cookies.get("access_token") == "jwt-d"
    from database.user_utils import get_user_by_email
    user = get_user_by_email("dave@example.com")
    assert user.supabase_uid == "sb-dave"
    assert user.password_hash is None  # Supabase owns the credential


def test_supabase_register_confirmation_link_targets_login(client, monkeypatch):
    """The confirmation email must redirect back to /login, not the app root (issue #62)."""
    tc, _ = client
    _force_supabase(monkeypatch, True)
    signup = MagicMock(return_value={
        "supabase_uid": "sb-grace", "access_token": "j", "refresh_token": "r", "expires_at": 9999999999,
    })
    monkeypatch.setattr(auth_router_module, "supabase_sign_up", signup)
    tc.post("/api/auth/register", json={
        "name": "Grace", "email": "grace@example.com", "username": "grace", "password": "pw12345678",
    })
    assert signup.call_args.kwargs["email_redirect_to"].endswith("/login")


def test_supabase_register_rejected_returns_400(client, monkeypatch):
    tc, _ = client
    _force_supabase(monkeypatch, True)
    monkeypatch.setattr(auth_router_module, "supabase_sign_up", lambda e, p, email_redirect_to=None: None)
    r = tc.post("/api/auth/register", json={
        "name": "Eve", "email": "eve@example.com", "username": "eve", "password": "weak",
    })
    assert r.status_code == 400


def test_supabase_register_email_confirmation_required(client, monkeypatch):
    tc, _ = client
    _force_supabase(monkeypatch, True)
    # sign_up succeeds but returns no session (confirmation email pending).
    monkeypatch.setattr(auth_router_module, "supabase_sign_up", lambda e, p, email_redirect_to=None: {"supabase_uid": "sb-frank"})
    r = tc.post("/api/auth/register", json={
        "name": "Frank", "email": "frank@example.com", "username": "frank", "password": "pw12345678",
    })
    assert r.status_code == 200
    assert r.json()["email_confirmation_required"] is True
    assert r.cookies.get("access_token") is None


# ── forgot-password ───────────────────────────────────────────

def test_forgot_password_no_enumeration(client, monkeypatch):
    tc, engine = client
    _force_supabase(monkeypatch, True)
    _make_user(engine, email="known@example.com", username="known")
    send = MagicMock(return_value=True)
    monkeypatch.setattr(auth_router_module, "supabase_send_password_reset", send)

    known = tc.post("/api/auth/forgot-password", json={"email": "known@example.com"})
    unknown = tc.post("/api/auth/forgot-password", json={"email": "ghost@example.com"})
    # Identical status and body regardless of whether the account exists.
    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json()
    # Email lowercased; redirect points at the reset page.
    (email_arg, redirect_arg) = send.call_args_list[0].args
    assert email_arg == "known@example.com"
    assert redirect_arg.endswith("/reset-password")


def test_forgot_password_lowercases_email(client, monkeypatch):
    tc, _ = client
    _force_supabase(monkeypatch, True)
    send = MagicMock(return_value=True)
    monkeypatch.setattr(auth_router_module, "supabase_send_password_reset", send)
    tc.post("/api/auth/forgot-password", json={"email": "MixedCase@Example.com"})
    assert send.call_args_list[0].args[0] == "mixedcase@example.com"


def test_forgot_password_unavailable_without_supabase(client, monkeypatch):
    tc, _ = client
    _force_supabase(monkeypatch, False)
    r = tc.post("/api/auth/forgot-password", json={"email": "x@example.com"})
    assert r.status_code == 503


# ── reset-password ────────────────────────────────────────────

def test_reset_password_success_and_uid_backfill(client, monkeypatch):
    tc, engine = client
    _force_supabase(monkeypatch, True)
    uid = _make_user(engine, email="reset@example.com", username="reset")
    monkeypatch.setattr(auth_router_module, "supabase_update_password",
                        lambda a, r, p: {"supabase_uid": "sb-reset", "email": "reset@example.com"})
    r = tc.post("/api/auth/reset-password", json={
        "access_token": "recovery-jwt", "refresh_token": "recovery-ref", "new_password": "brandnew123",
    })
    assert r.status_code == 200, r.text
    with Session(engine) as s:
        assert s.get(User, uid).supabase_uid == "sb-reset"


def test_reset_password_invalid_token_400(client, monkeypatch):
    tc, _ = client
    _force_supabase(monkeypatch, True)
    monkeypatch.setattr(auth_router_module, "supabase_update_password", lambda a, r, p: None)
    r = tc.post("/api/auth/reset-password", json={
        "access_token": "bad", "refresh_token": "bad", "new_password": "brandnew123",
    })
    assert r.status_code == 400


def test_reset_password_weak_password_422_before_supabase_call(client, monkeypatch):
    tc, _ = client
    _force_supabase(monkeypatch, True)
    update = MagicMock()
    monkeypatch.setattr(auth_router_module, "supabase_update_password", update)
    r = tc.post("/api/auth/reset-password", json={
        "access_token": "recovery-jwt", "refresh_token": "recovery-ref", "new_password": "short",
    })
    assert r.status_code == 422
    update.assert_not_called()  # never hand a weak password to Supabase


def test_reset_password_unavailable_without_supabase(client, monkeypatch):
    tc, _ = client
    _force_supabase(monkeypatch, False)
    r = tc.post("/api/auth/reset-password", json={
        "access_token": "x", "refresh_token": "y", "new_password": "brandnew123",
    })
    assert r.status_code == 503
