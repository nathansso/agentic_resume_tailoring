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


def create_profile(
    name: str,
    email: str,
    github_username: str = "",
    linkedin_url: str = "",
) -> User:
    """Create a new User row and persist its UUID to ~/.art/active_profile_id."""
    ART_DIR.mkdir(parents=True, exist_ok=True)
    with Session(engine) as session:
        user = User(
            name=name,
            email=email,
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
