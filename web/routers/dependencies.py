"""Shared FastAPI dependencies for AI route protection."""
import os
from datetime import datetime, timezone
from uuid import UUID

from fastapi import Depends, HTTPException
from sqlmodel import Session, select

from database.db import get_session
from database.models import AIUsage, User
from web.auth import get_current_user

AI_DAILY_LIMIT = int(os.getenv("AI_DAILY_LIMIT", "20"))
# LinkedIn scrapes hit Bright Data's paid API, so they get their own, much
# tighter daily cap. Keep this low — a user only needs to (re)import a profile
# rarely. Tune via the LINKEDIN_DAILY_LIMIT env var.
LINKEDIN_DAILY_LIMIT = int(os.getenv("LINKEDIN_DAILY_LIMIT", "2"))
OWNER_EMAIL = os.getenv("OWNER_EMAIL", "")


def _today_utc() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _usage_row(session: Session, user_id: UUID, kind: str):
    return session.exec(
        select(AIUsage).where(
            AIUsage.user_id == user_id,
            AIUsage.date == _today_utc(),
            AIUsage.kind == kind,
        )
    ).first()


def _increment(user_id: UUID, session: Session, kind: str) -> None:
    usage = _usage_row(session, user_id, kind)
    if usage:
        usage.call_count += 1
        session.add(usage)
    else:
        session.add(
            AIUsage(user_id=user_id, date=_today_utc(), kind=kind, call_count=1)
        )
    session.commit()


def _has_quota(session: Session, user_id: UUID, email: str, kind: str, limit: int) -> bool:
    if OWNER_EMAIL and email == OWNER_EMAIL:
        return True
    usage = _usage_row(session, user_id, kind)
    return not (usage and usage.call_count >= limit)


# ── AI (LLM) quota ─────────────────────────────────────────────────────────────

async def check_ai_quota(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> None:
    if not _has_quota(session, user.user_id, user.email, "ai", AI_DAILY_LIMIT):
        raise HTTPException(
            status_code=429,
            detail=f"Daily AI call limit ({AI_DAILY_LIMIT}) reached. Resets at midnight UTC.",
        )


def increment_ai_usage(user_id: UUID, session: Session) -> None:
    _increment(user_id, session, "ai")


# ── LaTeX preview-compile quota (issue #71) ────────────────────────────────────
# pdflatex is CPU-bound, not billed, so the cap is generous — it exists only to
# stop a runaway client from hammering the 512MB VM. Sized for the editor's
# debounced auto-compile (~50-150 compiles per active editing hour).
COMPILE_DAILY_LIMIT = int(os.getenv("COMPILE_DAILY_LIMIT", "500"))


async def check_compile_quota(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> None:
    if not _has_quota(session, user.user_id, user.email, "compile", COMPILE_DAILY_LIMIT):
        raise HTTPException(
            status_code=429,
            detail=f"Daily preview-compile limit ({COMPILE_DAILY_LIMIT}) reached. Resets at midnight UTC.",
        )


def increment_compile_usage(user_id: UUID, session: Session) -> None:
    _increment(user_id, session, "compile")


# ── LinkedIn (Bright Data) quota ────────────────────────────────────────────────

def linkedin_quota_remaining(session: Session, user_id: UUID, email: str = "") -> bool:
    """Whether the user may make another LinkedIn scrape today (non-raising)."""
    return _has_quota(session, user_id, email, "linkedin", LINKEDIN_DAILY_LIMIT)


async def check_linkedin_quota(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> None:
    if not linkedin_quota_remaining(session, user.user_id, user.email):
        raise HTTPException(
            status_code=429,
            detail=(
                f"Daily LinkedIn import limit ({LINKEDIN_DAILY_LIMIT}) reached. "
                "Resets at midnight UTC."
            ),
        )


def increment_linkedin_usage(user_id: UUID, session: Session) -> None:
    _increment(user_id, session, "linkedin")
