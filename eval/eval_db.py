"""Throwaway database URLs for the eval harnesses (issue #149).

Both `tailoring_benchmark.py` and `knowledge_updates_eval.py` hardcoded
`sqlite:///<workdir>/...`, so the numbers that judge the system end to end were
measured on an engine production does not run. This picks Postgres when
`ART_TEST_DATABASE_URL` is set and keeps the SQLite file otherwise, so the
offline/no-Docker path still works.

A whole database per *run* here, not the schema-per-test the suite uses
(`tests/conftest.py`): an eval run is a single long process, `CREATE DATABASE`
costs nothing at that granularity, and a separate database is the stronger
isolation of the two.
"""
import os
from pathlib import Path
from uuid import uuid4

ART_TEST_DATABASE_URL = os.getenv("ART_TEST_DATABASE_URL") or None


def _admin_engine(url):
    from sqlalchemy import create_engine

    return create_engine(url, isolation_level="AUTOCOMMIT")


def make_throwaway_db(label: str, workdir: Path) -> str:
    """Return a DATABASE_URL for an empty database this run owns.

    On Postgres the database is created here and should be dropped with
    `drop_throwaway_db`; on SQLite the file is created lazily by the engine and
    disappears with *workdir*.
    """
    if ART_TEST_DATABASE_URL is None:
        return f"sqlite:///{workdir / f'{label}.db'}"

    from sqlalchemy.engine import make_url

    name = f"art_eval_{label}_{uuid4().hex[:12]}"
    admin = _admin_engine(ART_TEST_DATABASE_URL)
    try:
        with admin.connect() as conn:
            conn.execute(_text(f'CREATE DATABASE "{name}"'))
    finally:
        admin.dispose()

    url = make_url(ART_TEST_DATABASE_URL).set(database=name)
    # pgvector is per-database, so the fresh database needs its own extension
    # before database/db.py::_migrate_pg_vector_columns can add vector columns.
    created = _admin_engine(url)
    try:
        with created.connect() as conn:
            conn.execute(_text("CREATE EXTENSION IF NOT EXISTS vector"))
    finally:
        created.dispose()

    return url.render_as_string(hide_password=False)


def drop_throwaway_db(url: str) -> None:
    """Drop a database made by `make_throwaway_db`. No-op for SQLite."""
    if ART_TEST_DATABASE_URL is None or not str(url).startswith("postgresql"):
        return

    from sqlalchemy.engine import make_url

    name = make_url(url).database
    admin = _admin_engine(ART_TEST_DATABASE_URL)
    try:
        with admin.connect() as conn:
            # Terminate stragglers first; DROP DATABASE fails while any
            # connection remains, and a benchmark run leaves pooled ones behind.
            conn.execute(_text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = :n AND pid <> pg_backend_pid()"
            ), {"n": name})
            conn.execute(_text(f'DROP DATABASE IF EXISTS "{name}"'))
    except Exception:
        # A leaked eval database is untidy, never fatal — the run's results
        # matter more than the cleanup.
        pass
    finally:
        admin.dispose()


def _text(sql):
    from sqlalchemy import text

    return text(sql)
