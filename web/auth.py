"""FastAPI auth dependency — resolves the current user from the session cookie."""
import os
import time
from typing import Optional
from uuid import UUID

from fastapi import Request, HTTPException
from sqlmodel import Session

from database.db import engine
from database.models import User
from database.user_utils import get_user_by_supabase_uid

_SESSION_SECRET = os.getenv("SESSION_SECRET_KEY", "dev-secret-change-in-production")

# Supabase projects migrated to "JWT signing keys" sign access tokens with a
# rotating asymmetric key (ES256/RS256) published at
# /auth/v1/.well-known/jwks.json; legacy projects sign with the shared HS256
# secret. Verify against the JWKS first, then fall back to the secret so both
# project generations (and unexpired legacy tokens during a migration) work.
_JWKS_TTL_SECONDS = 3600
_jwks_cache: dict = {"jwks": None, "fetched_at": 0.0}


def _fetch_jwks(supabase_url: str) -> Optional[dict]:
    import requests
    resp = requests.get(
        f"{supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json", timeout=5
    )
    resp.raise_for_status()
    return resp.json()


def _get_jwks() -> Optional[dict]:
    supabase_url = os.getenv("SUPABASE_URL")
    if not supabase_url:
        return None
    now = time.time()
    if _jwks_cache["jwks"] is not None and now - _jwks_cache["fetched_at"] < _JWKS_TTL_SECONDS:
        return _jwks_cache["jwks"]
    try:
        jwks = _fetch_jwks(supabase_url)
        if jwks and jwks.get("keys"):
            _jwks_cache["jwks"] = jwks
            _jwks_cache["fetched_at"] = now
            return jwks
    except Exception:
        pass
    # Serve the stale copy on fetch failure so live sessions survive a
    # transient Supabase outage.
    return _jwks_cache["jwks"]


def _decode_supabase_claims(token: str) -> Optional[dict]:
    from jose import jwt
    jwks = _get_jwks()
    if jwks:
        try:
            return jwt.decode(token, jwks, algorithms=["ES256", "RS256"], audience="authenticated")
        except Exception:
            pass
    jwt_secret = os.getenv("SUPABASE_JWT_SECRET")
    if jwt_secret:
        try:
            return jwt.decode(token, jwt_secret, algorithms=["HS256"], audience="authenticated")
        except Exception:
            pass
    return None


def _user_from_supabase_jwt(token: str) -> Optional[User]:
    payload = _decode_supabase_claims(token)
    if not payload:
        return None
    supabase_uid: Optional[str] = payload.get("sub")
    if not supabase_uid:
        return None
    try:
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
    # Fail-closed: once Supabase is configured (production), only Supabase-issued
    # JWTs are accepted. The local signed-cookie path is reachable only in
    # offline dev/tests where Supabase is absent.
    from database.auth import supabase_configured
    if supabase_configured():
        user = _user_from_supabase_jwt(token)
    else:
        user = _user_from_local_cookie(token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    return user


def make_session_token(user_id: UUID) -> str:
    from itsdangerous import URLSafeTimedSerializer
    return URLSafeTimedSerializer(_SESSION_SECRET).dumps(str(user_id))
