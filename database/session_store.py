"""Supabase JWT session persistence — save/load/refresh from ~/.art/."""
import json
import time
from pathlib import Path
from typing import Optional


def _session_file() -> Path:
    import database.user_utils as _uu
    return _uu.ART_DIR / "supabase_session.json"


def save_session(
    access_token: str,
    refresh_token: str,
    expires_at: int,
    supabase_uid: str = "",
) -> None:
    f = _session_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({
        "supabase_uid": supabase_uid,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_at": expires_at,
    }))


def load_session() -> Optional[dict]:
    try:
        return json.loads(_session_file().read_text())
    except Exception:
        return None


def clear_session() -> None:
    try:
        _session_file().unlink(missing_ok=True)
    except Exception:
        pass


def is_expired(session: dict) -> bool:
    """Return True if the session expires within the next 60 seconds."""
    return time.time() >= session.get("expires_at", 0) - 60
