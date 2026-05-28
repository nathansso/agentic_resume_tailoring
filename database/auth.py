"""Password hashing and Supabase Auth helpers."""
import hashlib
import hmac
import os
import secrets
from typing import Optional


def hash_password(password: str) -> str:
    """Return a PBKDF2-HMAC-SHA256 hash of *password* with an embedded salt."""
    salt = secrets.token_hex(16)
    key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 200_000)
    return f"pbkdf2:{salt}:{key.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    """Return True if *password* matches *stored_hash*."""
    try:
        _, salt, key_hex = stored_hash.split(":", 2)
    except ValueError:
        return False
    key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 200_000)
    return hmac.compare_digest(key.hex(), key_hex)


def _supabase_client():
    """Return a Supabase client if SUPABASE_URL and SUPABASE_ANON_KEY are set, else None."""
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_ANON_KEY")
    if not url or not key:
        return None
    try:
        from supabase import create_client
        return create_client(url, key)
    except ImportError:
        return None


def _session_dict(response) -> Optional[dict]:
    """Extract a session dict from a supabase-py AuthResponse."""
    user = response.user
    session = response.session
    if not user:
        return None
    result: dict = {"supabase_uid": str(user.id)}
    if session:
        result.update({
            "access_token": session.access_token,
            "refresh_token": session.refresh_token,
            "expires_at": int(session.expires_at),
        })
    return result


def supabase_sign_up(email: str, password: str) -> Optional[dict]:
    """Sign up via Supabase Auth using the user's real email address.

    Returns ``{supabase_uid, access_token, refresh_token, expires_at}`` on
    success. If email confirmation is enabled the session keys will be absent
    until the user clicks the verification link (supabase_uid is still
    returned). Returns None on any error.
    """
    client = _supabase_client()
    if not client:
        return None
    try:
        return _session_dict(client.auth.sign_up({"email": email, "password": password}))
    except Exception:
        return None


def supabase_sign_in(email: str, password: str) -> Optional[dict]:
    """Sign in via Supabase Auth using the user's email address.

    Returns ``{supabase_uid, access_token, refresh_token, expires_at}`` or None.
    """
    client = _supabase_client()
    if not client:
        return None
    try:
        return _session_dict(
            client.auth.sign_in_with_password({"email": email, "password": password})
        )
    except Exception:
        return None


def supabase_refresh_session(refresh_token: str) -> Optional[dict]:
    """Exchange a refresh token for a new session; returns updated dict or None."""
    client = _supabase_client()
    if not client:
        return None
    try:
        return _session_dict(client.auth.refresh_session(refresh_token))
    except Exception:
        return None


def supabase_restore_session() -> Optional[str]:
    """Load the persisted session; refresh if expired.

    Returns the ``supabase_uid`` of the restored user, or None if the session
    is missing, expired, or cannot be refreshed.
    """
    from database.session_store import load_session, save_session, is_expired
    session = load_session()
    if not session:
        return None
    if not is_expired(session):
        return session.get("supabase_uid") or None
    refreshed = supabase_refresh_session(session.get("refresh_token", ""))
    if not refreshed:
        return None
    save_session(
        access_token=refreshed["access_token"],
        refresh_token=refreshed["refresh_token"],
        expires_at=refreshed["expires_at"],
        supabase_uid=refreshed["supabase_uid"],
    )
    return refreshed["supabase_uid"]
