from contextvars import ContextVar
from pathlib import Path
from typing import Optional
from uuid import UUID

from sqlmodel import Session, select

from database.db import engine
from database.models import User

ART_DIR = Path.home() / ".art"
ACTIVE_PROFILE_FILE = ART_DIR / "active_profile_id"

# Request-scoped acting user for the multi-user web server (issue #73).
# The pointer file below is a single-user CLI concept: on a shared server it is
# one global slot that concurrent requests race over. Web requests bind the
# authenticated user here instead (set in web/auth.py::get_current_user);
# ContextVars propagate through asyncio.to_thread and task spawns, so service
# and agent code deep in a request keeps resolving the right user.
_REQUEST_USER_ID: ContextVar[Optional[UUID]] = ContextVar(
    "art_request_user_id", default=None
)


def set_request_user(user_id: Optional[UUID]) -> None:
    """Bind the acting user for the current request/task context.

    Pass None to clear the binding (get_active_profile falls back to the
    CLI pointer file).
    """
    _REQUEST_USER_ID.set(user_id)


def get_active_profile() -> Optional[User]:
    """Return the acting User, or None if no profile has been set up.

    Web requests resolve from the request-scoped binding; the CLI falls back
    to the ~/.art/active_profile_id pointer file.
    """
    request_uid = _REQUEST_USER_ID.get()
    if request_uid is not None:
        with Session(engine) as session:
            return session.get(User, request_uid)
    if not ACTIVE_PROFILE_FILE.exists():
        return None
    try:
        user_id = UUID(ACTIVE_PROFILE_FILE.read_text().strip())
    except (ValueError, OSError):
        return None
    with Session(engine) as session:
        return session.get(User, user_id)


def get_user_by_username(username: str) -> Optional[User]:
    """Return the User with the given username, or None if not found."""
    with Session(engine) as session:
        return session.exec(select(User).where(User.username == username)).first()


def get_user_by_email(email: str) -> Optional[User]:
    """Return the User with the given email, or None if not found."""
    with Session(engine) as session:
        return session.exec(select(User).where(User.email == email)).first()


def get_user_by_supabase_uid(supabase_uid: str) -> Optional[User]:
    """Return the User mapped to the given Supabase Auth UID, or None."""
    with Session(engine) as session:
        return session.exec(select(User).where(User.supabase_uid == supabase_uid)).first()


def authenticate_local(username: str, password: str) -> Optional[User]:
    """Verify username + password against the local DB. Returns the User or None."""
    from database.auth import verify_password
    user = get_user_by_username(username)
    if user and user.password_hash and verify_password(password, user.password_hash):
        return user
    return None


def authenticate_local_email(email: str, password: str) -> Optional[User]:
    """Verify email + password against the local DB. Returns the User or None.

    Used by the dev/offline auth fallback (when Supabase is not configured).
    In production, auth goes through Supabase and this is never called.
    """
    from database.auth import verify_password
    user = get_user_by_email(email)
    if user and user.password_hash and verify_password(password, user.password_hash):
        return user
    return None


def set_local_password(user_id: UUID, password_hash: str) -> None:
    """Update a user's local password hash (dev/offline fallback only)."""
    with Session(engine) as session:
        user = session.get(User, user_id)
        if user:
            user.password_hash = password_hash
            session.add(user)
            session.commit()


def set_supabase_uid(user_id: UUID, supabase_uid: str) -> None:
    """Link a local profile to its Supabase Auth UID.

    Backfilled on login/reset so users created before Supabase auth (or without
    the mapping) resolve correctly from their Supabase JWT's ``sub`` claim.
    """
    with Session(engine) as session:
        user = session.get(User, user_id)
        if user and user.supabase_uid != supabase_uid:
            user.supabase_uid = supabase_uid
            session.add(user)
            session.commit()


def create_profile(
    name: str,
    email: str,
    username: Optional[str] = None,
    password_hash: Optional[str] = None,
    github_username: str = "",
    linkedin_url: str = "",
    supabase_uid: Optional[str] = None,
) -> User:
    """Create a new User row and persist its UUID to ~/.art/active_profile_id."""
    ART_DIR.mkdir(parents=True, exist_ok=True)
    with Session(engine) as session:
        user = User(
            name=name,
            email=email,
            username=username or None,
            password_hash=password_hash or None,
            supabase_uid=supabase_uid or None,
            github_username=github_username or None,
            linkedin_url=linkedin_url or None,
            onboarding_complete=False,
            onboarding_steps={},
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        uid = user.user_id

    ACTIVE_PROFILE_FILE.write_text(str(uid))

    with Session(engine) as session:
        return session.get(User, uid)


class NoActiveUserError(RuntimeError):
    """Raised when code that acts on user data has no acting user bound.

    Failing here is deliberate. The previous behavior — resolving to an
    arbitrary ``select(User).limit(1)`` row — silently attributed one user's
    data to another on a shared server, which is how #73 happened.
    """


def require_active_user() -> User:
    """Return the acting User, or raise if none is bound (issue #131).

    Use this from library code (parsers, pipeline nodes, agents). Web requests
    bind the acting user via ``set_request_user``; the CLI binds it via
    ``get_or_create_cli_user``. If neither ran, that is a bug in the caller and
    must surface as an error rather than a wrong user.
    """
    user = get_active_profile()
    if user is None:
        raise NoActiveUserError(
            "No acting user is bound. Web callers must call set_request_user() "
            "before touching user data; CLI callers must bind via "
            "get_or_create_cli_user()."
        )
    return user


def get_or_create_cli_user() -> User:
    """Resolve the CLI's active profile, creating a default one on first run.

    CLI-only. This is the single remaining place allowed to adopt an existing
    row or write the global ``ACTIVE_PROFILE_FILE`` pointer, because the CLI is
    genuinely single-user: one process, one shell, one operator. Never call it
    from server code — see ``require_active_user``.
    """
    user = get_active_profile()
    if user:
        return user
    with Session(engine) as session:
        existing = session.exec(select(User).limit(1)).first()
        if existing:
            ART_DIR.mkdir(parents=True, exist_ok=True)
            ACTIVE_PROFILE_FILE.write_text(str(existing.user_id))
            return existing
    return create_profile("Default User", "user@example.com")
