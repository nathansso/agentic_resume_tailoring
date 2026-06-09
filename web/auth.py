"""FastAPI auth dependency — resolves the current user from the session cookie."""
import os
from typing import Optional
from uuid import UUID

from fastapi import Request, HTTPException
from sqlmodel import Session

from database.db import engine
from database.models import User
from database.user_utils import get_user_by_supabase_uid

_SESSION_SECRET = os.getenv("SESSION_SECRET_KEY", "dev-secret-change-in-production")


def _user_from_supabase_jwt(token: str) -> Optional[User]:
    jwt_secret = os.getenv("SUPABASE_JWT_SECRET")
    if not jwt_secret:
        return None
    try:
        from jose import jwt
        payload = jwt.decode(token, jwt_secret, algorithms=["HS256"], audience="authenticated")
        supabase_uid: Optional[str] = payload.get("sub")
        if not supabase_uid:
            return None
        return get_user_by_supabase_uid(supabase_uid)
    except Exception:
        return None


def _user_from_local_cookie(token: str) -> Optional[User]:
    try:
        from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
        s = URLSafeTimedSerializer(_SESSION_SECRET)
        user_id_str: str = s.loads(token, max_age=86400 * 30)
        with Session(engine) as db:
            return db.get(User, UUID(user_id_str))
    except Exception:
        return None


def get_current_user(request: Request) -> User:
    token = request.cookies.get("access_token")
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user = _user_from_supabase_jwt(token) or _user_from_local_cookie(token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    return user


def make_session_token(user_id: UUID) -> str:
    from itsdangerous import URLSafeTimedSerializer
    return URLSafeTimedSerializer(_SESSION_SECRET).dumps(str(user_id))
