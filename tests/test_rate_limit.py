"""Tests for per-user AI rate limiting (issue #45)."""
import asyncio
import pytest
from sqlmodel import Session, select

import web.routers.dependencies as deps_module
from database.models import AIUsage, User
from web.routers.dependencies import check_ai_quota, increment_ai_usage


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_user(engine, email="user@example.com") -> User:
    with Session(engine) as s:
        user = User(name="Test", email=email)
        s.add(user)
        s.commit()
        s.refresh(user)
        return user


def _usage_count(engine, user_id, date) -> int:
    with Session(engine) as s:
        row = s.exec(
            select(AIUsage).where(AIUsage.user_id == user_id, AIUsage.date == date)
        ).first()
        return row.call_count if row else 0


# ── increment_ai_usage ────────────────────────────────────────────────────────

def test_increment_creates_row(isolated_engine):
    user = _make_user(isolated_engine)
    with Session(isolated_engine) as s:
        increment_ai_usage(user.user_id, s)
    assert _usage_count(isolated_engine, user.user_id, "2099-01-01") == 0  # different date
    with Session(isolated_engine) as s:
        rows = s.exec(select(AIUsage).where(AIUsage.user_id == user.user_id)).all()
    assert len(rows) == 1
    assert rows[0].call_count == 1


def test_increment_accumulates(isolated_engine):
    user = _make_user(isolated_engine)
    for _ in range(3):
        with Session(isolated_engine) as s:
            increment_ai_usage(user.user_id, s)
    with Session(isolated_engine) as s:
        rows = s.exec(select(AIUsage).where(AIUsage.user_id == user.user_id)).all()
    assert len(rows) == 1
    assert rows[0].call_count == 3


def test_increment_separate_dates(isolated_engine, monkeypatch):
    user = _make_user(isolated_engine)
    monkeypatch.setattr(deps_module, "_today_utc", lambda: "2025-01-01")
    with Session(isolated_engine) as s:
        increment_ai_usage(user.user_id, s)

    monkeypatch.setattr(deps_module, "_today_utc", lambda: "2025-01-02")
    with Session(isolated_engine) as s:
        increment_ai_usage(user.user_id, s)

    with Session(isolated_engine) as s:
        rows = s.exec(select(AIUsage).where(AIUsage.user_id == user.user_id)).all()
    assert len(rows) == 2
    assert {r.date for r in rows} == {"2025-01-01", "2025-01-02"}
    assert all(r.call_count == 1 for r in rows)


# ── check_ai_quota ────────────────────────────────────────────────────────────

def test_quota_allows_under_limit(isolated_engine, monkeypatch):
    monkeypatch.setattr(deps_module, "AI_DAILY_LIMIT", 5)
    monkeypatch.setattr(deps_module, "OWNER_EMAIL", "")
    user = _make_user(isolated_engine)

    for _ in range(4):
        with Session(isolated_engine) as s:
            increment_ai_usage(user.user_id, s)

    async def _run():
        with Session(isolated_engine) as s:
            await check_ai_quota(user=user, session=s)  # should not raise

    asyncio.run(_run())


def test_quota_raises_429_at_limit(isolated_engine, monkeypatch):
    from fastapi import HTTPException
    monkeypatch.setattr(deps_module, "AI_DAILY_LIMIT", 3)
    monkeypatch.setattr(deps_module, "OWNER_EMAIL", "")
    user = _make_user(isolated_engine)

    for _ in range(3):
        with Session(isolated_engine) as s:
            increment_ai_usage(user.user_id, s)

    async def _run():
        with Session(isolated_engine) as s:
            with pytest.raises(HTTPException) as exc_info:
                await check_ai_quota(user=user, session=s)
        assert exc_info.value.status_code == 429

    asyncio.run(_run())


def test_owner_email_exempt(isolated_engine, monkeypatch):
    monkeypatch.setattr(deps_module, "AI_DAILY_LIMIT", 1)
    monkeypatch.setattr(deps_module, "OWNER_EMAIL", "owner@example.com")
    owner = _make_user(isolated_engine, email="owner@example.com")

    for _ in range(5):
        with Session(isolated_engine) as s:
            increment_ai_usage(owner.user_id, s)

    async def _run():
        with Session(isolated_engine) as s:
            await check_ai_quota(user=owner, session=s)  # should not raise

    asyncio.run(_run())


def test_owner_exempt_nonowner_blocked(isolated_engine, monkeypatch):
    from fastapi import HTTPException
    monkeypatch.setattr(deps_module, "AI_DAILY_LIMIT", 2)
    monkeypatch.setattr(deps_module, "OWNER_EMAIL", "owner@example.com")
    regular = _make_user(isolated_engine, email="regular@example.com")

    for _ in range(2):
        with Session(isolated_engine) as s:
            increment_ai_usage(regular.user_id, s)

    async def _run():
        with Session(isolated_engine) as s:
            with pytest.raises(HTTPException) as exc_info:
                await check_ai_quota(user=regular, session=s)
        assert exc_info.value.status_code == 429

    asyncio.run(_run())


def test_daily_reset(isolated_engine, monkeypatch):
    monkeypatch.setattr(deps_module, "AI_DAILY_LIMIT", 2)
    monkeypatch.setattr(deps_module, "OWNER_EMAIL", "")
    user = _make_user(isolated_engine)

    monkeypatch.setattr(deps_module, "_today_utc", lambda: "2025-06-07")
    for _ in range(2):
        with Session(isolated_engine) as s:
            increment_ai_usage(user.user_id, s)

    # New day — quota resets
    monkeypatch.setattr(deps_module, "_today_utc", lambda: "2025-06-08")

    async def _run():
        with Session(isolated_engine) as s:
            await check_ai_quota(user=user, session=s)  # should not raise

    asyncio.run(_run())
