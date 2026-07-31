"""
Shared fixtures and helpers for the ART test suite.

All test files in this directory have access to these fixtures automatically.
The _seed_user_and_skill helper can be imported: from conftest import _seed_user_and_skill

Database engine (issue #149)
----------------------------
`isolated_engine` runs on SQLite by default and on PostgreSQL when
``ART_TEST_DATABASE_URL`` is set. Production is Supabase Postgres, so the
Postgres leg is the one that matches what we ship; SQLite stays supported
because `cli.py` and the no-Docker contributor path depend on it.

Opt-in on purpose. Auto-detecting a reachable server would make `python
run_tests.py` mean different things on different machines, which is the exact
ambiguity #149 exists to remove. It is also a **separate variable from
`DATABASE_URL`** by design: `DATABASE_URL` may legitimately point at production
Supabase in a developer's `.env`, and the suite must never be able to reach it.

    docker compose up -d postgres
    export ART_TEST_DATABASE_URL=postgresql://art:art@localhost:5432/art
    python run_tests.py
"""
import os
import sys
from pathlib import Path
from uuid import uuid4

# Ensure project root is importable when pytest is invoked from any directory.
# pytest.ini pythonpath=. also handles this; this is a belt-and-suspenders guard.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest
from sqlalchemy import text
from sqlmodel import SQLModel, Session, create_engine

import agents.chat as chat_module
import database.db as db_module
import database.user_utils as user_utils_module
import knowledge_graph.builder as kg_builder_module
import services as services_module
from database.models import User, Skill, UserSkill

# Set → Postgres leg. Unset → SQLite leg (historical default).
ART_TEST_DATABASE_URL = os.getenv("ART_TEST_DATABASE_URL") or None


def requires_postgres(func):
    """Skip a test unless the suite is running its Postgres leg.

    For tests that assert Postgres-specific behavior (the migration chain, the
    pgvector search path) and cannot express anything meaningful on SQLite.
    """
    return pytest.mark.skipif(
        ART_TEST_DATABASE_URL is None,
        reason="requires ART_TEST_DATABASE_URL (Postgres leg)",
    )(func)


@pytest.fixture(scope="session")
def _pg_ready():
    """Once per session: prove the server is reachable and pgvector is present.

    Fails loudly rather than degrading. A Postgres run that silently skipped the
    vector work would reintroduce exactly the blind spot #149 is closing.
    """
    if ART_TEST_DATABASE_URL is None:
        return None
    engine = create_engine(ART_TEST_DATABASE_URL, isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    return engine


@pytest.fixture(autouse=True)
def _isolate_institution_cache(monkeypatch):
    """Keep ROR (issue #95) out of the suite: disable network lookups by default
    and clear the in-process canonicalization memo between tests, so institution
    dedup falls back to normalized-string matching unless a test opts in."""
    import institution as institution_module

    institution_module._MEMO.clear()
    monkeypatch.setenv("ROR_LOOKUP_ENABLED", "0")
    yield
    institution_module._MEMO.clear()


def _sqlite_engine(tmp_path):
    """Temp-file SQLite engine — the historical default path, unchanged."""
    db_path = tmp_path / "test_art.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return engine, None


def _postgres_engine():
    """Postgres engine scoped to a throwaway schema.

    A schema per test, not a database per test: `CREATE SCHEMA` is far cheaper
    than `CREATE DATABASE` across 800+ tests, and it keeps
    `_migrate_pg_uuid_columns`'s ``WHERE table_schema = current_schema()`` filter
    correct with no change to production code.

    `public` stays on the search_path *after* the test schema so the `vector`
    type resolves (the extension lives in public), while `current_schema()` and
    every `create_all` still land in the test schema.
    """
    schema = f"art_test_{uuid4().hex[:16]}"
    admin = create_engine(ART_TEST_DATABASE_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    admin.dispose()

    engine = create_engine(
        ART_TEST_DATABASE_URL,
        connect_args={"options": f"-csearch_path={schema},public"},
    )
    SQLModel.metadata.create_all(engine)
    return engine, schema


def _drop_schema(schema):
    """Drop a test schema. Runs in a finally, so a failing test cannot leak it."""
    admin = create_engine(ART_TEST_DATABASE_URL, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    finally:
        admin.dispose()


@pytest.fixture()
def isolated_engine(tmp_path, monkeypatch, _pg_ready):
    """Per-test isolated engine, with all module-level engine refs patched.

    SQLite temp file by default; a throwaway Postgres schema when
    ART_TEST_DATABASE_URL is set. Everything below this point is
    dialect-independent, so no consumer of the fixture needs to care which.
    """
    if ART_TEST_DATABASE_URL:
        engine, schema = _postgres_engine()
    else:
        engine, schema = _sqlite_engine(tmp_path)

    profile_file = tmp_path / "active_profile_id"

    monkeypatch.setattr(db_module, "engine", engine)
    monkeypatch.setattr(chat_module, "engine", engine)
    monkeypatch.setattr(kg_builder_module, "engine", engine)
    monkeypatch.setattr(services_module, "engine", engine)
    monkeypatch.setattr(user_utils_module, "engine", engine)
    monkeypatch.setattr(user_utils_module, "ACTIVE_PROFILE_FILE", profile_file)
    monkeypatch.setattr(user_utils_module, "ART_DIR", tmp_path)

    # Clear any request-user binding left by a previous test in this thread
    # (issue #73): the ContextVar would otherwise shadow the profile file.
    user_utils_module.set_request_user(None)

    engine._test_profile_file = profile_file
    try:
        yield engine
    finally:
        engine.dispose()
        if schema is not None:
            _drop_schema(schema)


def _seed_user_and_skill(engine):
    """Seed a test user with one Python skill and write the active-profile pointer.

    Returns a SimpleNamespace(user_id=...) so callers can access user_id without
    SQLAlchemy session state complications.
    """
    from types import SimpleNamespace

    with Session(engine) as session:
        user = User(name="Test User", email="test@example.com")
        session.add(user)
        session.commit()
        session.refresh(user)
        uid = user.user_id

        skill = Skill(name="Python", category="language")
        session.add(skill)
        session.commit()
        session.refresh(skill)

        user_skill = UserSkill(
            user_id=uid,
            skill_id=skill.skill_id,
            proficiency=5,
            evidence_source="resume",
            confidence_score=0.95,
        )
        session.add(user_skill)
        session.commit()

    if hasattr(engine, "_test_profile_file"):
        engine._test_profile_file.write_text(str(uid))

    return SimpleNamespace(user_id=uid)
