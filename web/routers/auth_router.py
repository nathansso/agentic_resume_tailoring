from fastapi import APIRouter, HTTPException, Response, Depends
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError

from database.auth import hash_password, supabase_sign_in, supabase_sign_up
from database.user_utils import authenticate_local, create_profile, get_user_by_email, get_user_by_username
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
