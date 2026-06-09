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
OWNER_EMAIL = os.getenv("OWNER_EMAIL", "")


def _today_utc() -> str:
    return datetime.now(timezone.utc).date().isoformat()


async def check_ai_quota(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> None:
    if OWNER_EMAIL and user.email == OWNER_EMAIL:
        return
    today = _today_utc()
    usage = session.exec(
        select(AIUsage).where(AIUsage.user_id == user.user_id, AIUsage.date == today)
    ).first()
    if usage and usage.call_count >= AI_DAILY_LIMIT:
        raise HTTPException(
            status_code=429,
            detail=f"Daily AI call limit ({AI_DAILY_LIMIT}) reached. Resets at midnight UTC.",
        )


def increment_ai_usage(user_id: UUID, session: Session) -> None:
    today = _today_utc()
    usage = session.exec(
        select(AIUsage).where(AIUsage.user_id == user_id, AIUsage.date == today)
    ).first()
    if usage:
        usage.call_count += 1
        session.add(usage)
    else:
        session.add(AIUsage(user_id=user_id, date=today, call_count=1))
    session.commit()
