from fastapi import APIRouter, HTTPException, Response, Depends
from pydantic import BaseModel

from database.auth import hash_password, supabase_sign_in, supabase_sign_up
from database.user_utils import authenticate_local, create_profile, get_user_by_username
from web.auth import get_current_user, make_session_token
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
        raise HTTPException(status_code=401, detail="Invalid credentials")
    _set_session(response, user, body.password)
    return {"user": _user_dict(user)}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie("access_token")
    return {"ok": True}


@router.post("/register")
def register(body: RegisterRequest, response: Response):
    if get_user_by_username(body.username):
        raise HTTPException(status_code=409, detail="Username already taken")

    supabase_session = supabase_sign_up(body.email, body.password)
    supabase_uid = supabase_session.get("supabase_uid") if supabase_session else None

    user = create_profile(
        name=body.name,
        email=body.email,
        username=body.username,
        password_hash=hash_password(body.password),
        supabase_uid=supabase_uid,
    )

    if supabase_session and supabase_session.get("access_token"):
        token = supabase_session["access_token"]
    else:
        token = make_session_token(user.user_id)
    response.set_cookie("access_token", token, **_COOKIE_OPTS)
    return {"user": _user_dict(user)}


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return _user_dict(user)
