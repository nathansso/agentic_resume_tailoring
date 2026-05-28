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


def supabase_sign_up(username: str, password: str) -> Optional[str]:
    """Create a Supabase Auth user; returns the supabase_uid on success, None otherwise."""
    client = _supabase_client()
    if not client:
        return None
    email = f"{username}@art.local"
    try:
        response = client.auth.sign_up({"email": email, "password": password})
        user = response.user
        return str(user.id) if user else None
    except Exception:
        return None


def supabase_sign_in(username: str, password: str) -> Optional[str]:
    """Sign in via Supabase Auth; returns the supabase_uid on success, None otherwise."""
    client = _supabase_client()
    if not client:
        return None
    email = f"{username}@art.local"
    try:
        response = client.auth.sign_in_with_password({"email": email, "password": password})
        user = response.user
        return str(user.id) if user else None
    except Exception:
        return None
