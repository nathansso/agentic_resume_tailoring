from pathlib import Path
from typing import Optional
from uuid import UUID

from sqlmodel import Session, select

from database.db import engine
from database.models import User

ART_DIR = Path.home() / ".art"
ACTIVE_PROFILE_FILE = ART_DIR / "active_profile_id"


def get_active_profile() -> Optional[User]:
    """Return the active User, or None if no profile has been set up."""
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


def get_or_create_default_user() -> User:
    """Backward-compat wrapper used by the pipeline and CLI. Prefers active profile."""
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
