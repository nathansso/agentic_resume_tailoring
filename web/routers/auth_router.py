from urllib.parse import urlencode
from uuid import UUID

import requests as _requests
from fastapi import APIRouter, HTTPException, Response, Depends
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from config import GITHUB_CLIENT_ID, GITHUB_CLIENT_SECRET
from database.auth import hash_password, supabase_sign_in, supabase_sign_up
from database.db import engine
from database.user_utils import authenticate_local, create_profile, get_user_by_email, get_user_by_username
from web.auth import get_current_user, make_session_token, _SESSION_SECRET
from database.models import User

router = APIRouter(prefix="/api/auth", tags=["auth"])

_COOKIE_OPTS = dict(httponly=True, samesite="strict", max_age=86400 * 30)


def _set_session(response: Response, user: User, password: str) -> None:
    supabase_session = supabase_sign_in(user.email, password)
    if supabase_session and supabase_session.get("access_token"):
        token = supabase_session["access_token"]
    else:
        token = make_session_token(user.user_id)
    response.set_cookie("access_token", token, **_COOKIE_OPTS)


def _user_dict(user: User) -> dict:
    return {"id": str(user.user_id), "name": user.name, "email": user.email, "username": user.username}


class LoginRequest(BaseModel):
    username: str
    password: str


class RegisterRequest(BaseModel):
    name: str
    email: str
    username: str
    password: str


@router.post("/login")
def login(body: LoginRequest, response: Response):
    user = authenticate_local(body.username, body.password)
    if not user:
        existing = get_user_by_username(body.username)
        if not existing:
            raise HTTPException(status_code=401, detail="No account found with that username")
        raise HTTPException(status_code=401, detail="Incorrect password")
    _set_session(response, user, body.password)
    return {"user": _user_dict(user)}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie("access_token")
    return {"ok": True}


@router.post("/register")
def register(body: RegisterRequest, response: Response):
    if not body.name.strip():
        raise HTTPException(status_code=422, detail="Name is required")
    if not body.username.strip():
        raise HTTPException(status_code=422, detail="Username is required")
    if get_user_by_username(body.username):
        raise HTTPException(status_code=409, detail="Username already taken")
    if get_user_by_email(body.email):
        raise HTTPException(status_code=409, detail="An account with that email already exists")

    supabase_session = supabase_sign_up(body.email, body.password)
    supabase_uid = supabase_session.get("supabase_uid") if supabase_session else None

    try:
        user = create_profile(
            name=body.name,
            email=body.email,
            username=body.username,
            password_hash=hash_password(body.password),
            supabase_uid=supabase_uid,
        )
    except IntegrityError:
        raise HTTPException(status_code=409, detail="An account with that email or username already exists")

    if supabase_session and supabase_session.get("access_token"):
        token = supabase_session["access_token"]
    else:
        token = make_session_token(user.user_id)
    response.set_cookie("access_token", token, **_COOKIE_OPTS)
    return {"user": _user_dict(user)}


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return _user_dict(user)


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
