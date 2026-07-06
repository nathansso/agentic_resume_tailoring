import os
from datetime import datetime, timezone
from urllib.parse import urlencode
from uuid import UUID

import requests as _requests
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response, Depends
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from config import BRIGHTDATA_API_KEY, GITHUB_CLIENT_ID, GITHUB_CLIENT_SECRET
from database.auth import (
    hash_password,
    supabase_configured,
    supabase_send_password_reset,
    supabase_sign_in,
    supabase_sign_up,
    supabase_update_password,
)
from database.db import engine
from database.user_utils import (
    authenticate_local_email,
    create_profile,
    get_user_by_email,
    get_user_by_supabase_uid,
    get_user_by_username,
    set_supabase_uid,
)
from ingestion.linkedin import LinkedInIngestor
from web.auth import get_current_user, make_session_token, _SESSION_SECRET
from web.routers.dependencies import linkedin_quota_remaining
from web.routers.profile_router import _linkedin_ingest_task
from database.models import User

router = APIRouter(prefix="/api/auth", tags=["auth"])

_COOKIE_OPTS = dict(httponly=True, samesite="strict", max_age=86400 * 30)

_MIN_PASSWORD_LENGTH = 8


def _validate_password_strength(password: str) -> None:
    """Reject weak passwords with HTTP 422. Supabase enforces its own policy too."""
    if len(password or "") < _MIN_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=422,
            detail=f"Password must be at least {_MIN_PASSWORD_LENGTH} characters",
        )


def _app_url(request: Request, path: str) -> str:
    """Absolute URL to *path* on the public app origin.

    Prefers the APP_BASE_URL env var (set in production) and otherwise derives
    the origin from the incoming request so local dev works without config.
    """
    base = os.getenv("APP_BASE_URL") or str(request.base_url)
    return f"{base.rstrip('/')}{path}"


def _reset_redirect_url(request: Request) -> str:
    """Absolute URL of the reset-password page for the Supabase recovery link."""
    return _app_url(request, "/reset-password")


def _maybe_refresh_linkedin_on_login(background: BackgroundTasks, user: User) -> None:
    """On sign-in, refresh LinkedIn data — at most once per day, quota permitting.

    Only fires when the user has a LinkedIn URL on file and we don't already
    have a scrape from today. The daily cap is the hard backstop; this staleness
    gate keeps rapid re-logins from each spending a paid Bright Data call.
    """
    if not BRIGHTDATA_API_KEY or not (user.linkedin_url or "").strip():
        return
    ingested_at = user.linkedin_ingested_at
    if ingested_at and ingested_at.date() == datetime.now(timezone.utc).date():
        return  # already refreshed today
    with Session(engine) as session:
        if not linkedin_quota_remaining(session, user.user_id, user.email):
            return
    try:
        normalized = LinkedInIngestor._normalize_url(user.linkedin_url)
    except Exception:
        return
    background.add_task(_linkedin_ingest_task, user.user_id, normalized, user.email)


def _user_dict(user: User) -> dict:
    return {"id": str(user.user_id), "name": user.name, "email": user.email, "username": user.username}


class LoginRequest(BaseModel):
    email: str
    password: str


class RegisterRequest(BaseModel):
    name: str
    email: str
    username: str
    password: str


@router.post("/login")
def login(body: LoginRequest, response: Response, background: BackgroundTasks):
    # Generic credential error — never reveals whether the email exists.
    invalid = HTTPException(status_code=401, detail="Invalid email or password")
    email = body.email.strip().lower()

    if supabase_configured():
        session = supabase_sign_in(email, body.password)
        if not session or not session.get("access_token"):
            raise invalid
        user = get_user_by_supabase_uid(session["supabase_uid"]) or get_user_by_email(email)
        if not user:
            raise invalid
        # Backfill the Supabase UID so JWT resolution (sub → user) works for
        # accounts created before this mapping existed.
        set_supabase_uid(user.user_id, session["supabase_uid"])
        response.set_cookie("access_token", session["access_token"], **_COOKIE_OPTS)
    else:
        # Offline dev/tests only — production always has Supabase configured.
        user = authenticate_local_email(email, body.password)
        if not user:
            raise invalid
        response.set_cookie("access_token", make_session_token(user.user_id), **_COOKIE_OPTS)

    _maybe_refresh_linkedin_on_login(background, user)
    return {"user": _user_dict(user)}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie("access_token")
    return {"ok": True}


@router.post("/register")
def register(body: RegisterRequest, response: Response, request: Request):
    if not body.name.strip():
        raise HTTPException(status_code=422, detail="Name is required")
    if not body.username.strip():
        raise HTTPException(status_code=422, detail="Username is required")
    email = body.email.strip().lower()
    if get_user_by_username(body.username):
        raise HTTPException(status_code=409, detail="Username already taken")
    if get_user_by_email(email):
        raise HTTPException(status_code=409, detail="An account with that email already exists")

    if supabase_configured():
        # Supabase owns the credential — no local password hash is stored.
        # Land the confirmation-email link on /login (not the app root).
        supabase_session = supabase_sign_up(
            email, body.password, email_redirect_to=_app_url(request, "/login")
        )
        supabase_uid = supabase_session.get("supabase_uid") if supabase_session else None
        if not supabase_uid:
            raise HTTPException(status_code=400, detail="Could not create account. Try a different email or a stronger password.")
        password_hash = None
    else:
        # Offline dev/tests only.
        supabase_session = None
        supabase_uid = None
        password_hash = hash_password(body.password)

    try:
        user = create_profile(
            name=body.name,
            email=email,
            username=body.username,
            password_hash=password_hash,
            supabase_uid=supabase_uid,
        )
    except IntegrityError:
        raise HTTPException(status_code=409, detail="An account with that email or username already exists")

    if supabase_configured():
        if supabase_session and supabase_session.get("access_token"):
            response.set_cookie("access_token", supabase_session["access_token"], **_COOKIE_OPTS)
        else:
            # Email confirmation is enabled on the Supabase project — the user must
            # confirm before a session exists. Don't set a cookie; signal the client.
            return {"user": _user_dict(user), "email_confirmation_required": True}
    else:
        response.set_cookie("access_token", make_session_token(user.user_id), **_COOKIE_OPTS)

    return {"user": _user_dict(user)}


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return _user_dict(user)


# ── Password recovery ─────────────────────────────────────────

class ForgotPasswordRequest(BaseModel):
    email: str


class ResetPasswordRequest(BaseModel):
    access_token: str
    refresh_token: str
    new_password: str


@router.get("/capabilities")
def auth_capabilities():
    """Public: tells the frontend which auth features are available."""
    enabled = supabase_configured()
    return {
        "password_reset_enabled": enabled,
        "auth_mode": "supabase" if enabled else "local",
    }


@router.post("/forgot-password")
def forgot_password(body: ForgotPasswordRequest, request: Request):
    """Trigger a Supabase password-reset email.

    Always returns the same generic response so callers cannot tell whether the
    address maps to a real account (no enumeration). Requires Supabase — the
    offline dev fallback has no email transport, so it returns 503.
    """
    if not supabase_configured():
        raise HTTPException(status_code=503, detail="Password reset is not available in this environment")
    # Fire-and-forget; ignore the result to avoid leaking account existence.
    supabase_send_password_reset(body.email.strip().lower(), _reset_redirect_url(request))
    return {"ok": True, "message": "If an account exists for that email, a reset link has been sent."}


@router.post("/reset-password")
def reset_password(body: ResetPasswordRequest):
    """Consume a Supabase recovery token and set a new password.

    The recovery tokens come from the emailed link's URL fragment. Supabase
    validates that they are unexpired and single-use; we additionally enforce a
    minimum password strength before applying the change.
    """
    if not supabase_configured():
        raise HTTPException(status_code=503, detail="Password reset is not available in this environment")
    _validate_password_strength(body.new_password)
    result = supabase_update_password(body.access_token, body.refresh_token, body.new_password)
    if not result:
        raise HTTPException(status_code=400, detail="This reset link is invalid or has expired. Request a new one.")
    # Keep the local profile mapping consistent for post-reset JWT resolution.
    uid = result.get("supabase_uid")
    email = result.get("email")
    user = (get_user_by_supabase_uid(uid) if uid else None) or (get_user_by_email(email) if email else None)
    if user and uid:
        set_supabase_uid(user.user_id, uid)
    return {"ok": True}


# ── GitHub OAuth ──────────────────────────────────────────────

@router.get("/github")
def github_oauth_start(user: User = Depends(get_current_user)):
    """Redirect the browser to GitHub's OAuth authorization page."""
    if not GITHUB_CLIENT_ID:
        raise HTTPException(status_code=503, detail="GitHub OAuth not configured (GITHUB_CLIENT_ID missing)")
    from itsdangerous import URLSafeSerializer
    state = URLSafeSerializer(_SESSION_SECRET).dumps(str(user.user_id))
    qs = urlencode({"client_id": GITHUB_CLIENT_ID, "scope": "repo read:user", "state": state})
    return RedirectResponse(f"https://github.com/login/oauth/authorize?{qs}")


@router.get("/github/callback")
def github_oauth_callback(code: str, state: str):
    """Receive GitHub's OAuth callback, exchange code for token, store it."""
    if not GITHUB_CLIENT_ID or not GITHUB_CLIENT_SECRET:
        raise HTTPException(status_code=503, detail="GitHub OAuth not configured")

    from itsdangerous import URLSafeSerializer, BadSignature
    try:
        user_id_str = URLSafeSerializer(_SESSION_SECRET).loads(state)
        user_id = UUID(user_id_str)
    except (BadSignature, ValueError):
        raise HTTPException(status_code=400, detail="Invalid OAuth state parameter")

    resp = _requests.post(
        "https://github.com/login/oauth/access_token",
        headers={"Accept": "application/json"},
        json={"client_id": GITHUB_CLIENT_ID, "client_secret": GITHUB_CLIENT_SECRET, "code": code},
        timeout=10,
    )
    token_data = resp.json()
    access_token = token_data.get("access_token")
    if not access_token:
        err = token_data.get("error_description", "Unknown error")
        raise HTTPException(status_code=400, detail=f"GitHub OAuth failed: {err}")

    github_username = None
    try:
        user_resp = _requests.get(
            "https://api.github.com/user",
            headers={"Authorization": f"token {access_token}", "Accept": "application/vnd.github+json"},
            timeout=10,
        )
        if user_resp.ok:
            github_username = user_resp.json().get("login")
    except Exception:
        pass

    with Session(engine) as db:
        u = db.get(User, user_id)
        if not u:
            raise HTTPException(status_code=404, detail="User not found")
        u.github_access_token = access_token
        if github_username:
            u.github_username = github_username
        db.add(u)
        db.commit()

    return RedirectResponse("/?github_connected=1")


@router.get("/github/status")
def github_status(user: User = Depends(get_current_user)):
    """Return whether the current user has a connected GitHub OAuth token."""
    return {
        "connected": bool(user.github_access_token),
        "oauth_configured": bool(GITHUB_CLIENT_ID),
        "github_username": user.github_username,
    }


@router.delete("/github")
def github_disconnect(user: User = Depends(get_current_user)):
    """Remove the stored GitHub OAuth token for the current user."""
    with Session(engine) as db:
        u = db.get(User, user.user_id)
        if u:
            u.github_access_token = None
            u.github_username = None
            db.add(u)
            db.commit()
    return {"ok": True}
