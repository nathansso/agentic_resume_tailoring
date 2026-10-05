"""Timezone-aware datetimes, and databases that predate them (issue #210).

sqlmodel >= 0.0.47 maps a `datetime` field to `UTCDateTime`: it refuses a naive
value on write and returns an aware UTC value on read, whatever the column
physically holds. The column contract is in `database/clock.py`. These tests pin
the two halves of it:

* every "now" the code produces is aware UTC, and every model accepts it;
* a database written before the change, with naive timestamps, still loads, sorts
  and compares: SQLite rows are naive strings, and production Supabase's columns
  are `timestamp without time zone`.
"""
import re
from datetime import datetime, timedelta, timezone
from importlib.metadata import version
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import text
from sqlmodel import Session, SQLModel, select

from conftest import _seed_user_and_skill, requires_postgres
from database.clock import as_utc, parse_utc, utc_now
from database.models import JobDescription, UserJobResult, UserPreference

ROOT = Path(__file__).resolve().parent.parent
UTC = timezone.utc


def _version_tuple(v: str):
    return tuple(int(p) for p in re.findall(r"\d+", v)[:3])


# ---------------------------------------------------------------------------
# The clock
# ---------------------------------------------------------------------------

def test_utc_now_is_aware_utc():
    now = utc_now()
    assert now.tzinfo is not None and now.utcoffset() == timedelta(0)
    assert abs((datetime.now(UTC) - now).total_seconds()) < 5


def test_as_utc_reads_naive_as_utc_and_converts_aware():
    assert as_utc(None) is None
    naive = datetime(2026, 8, 12, 9, 0, 0)
    assert as_utc(naive) == datetime(2026, 8, 12, 9, 0, 0, tzinfo=UTC)
    pacific = timezone(timedelta(hours=-7))
    shifted = as_utc(datetime(2026, 8, 12, 2, 0, 0, tzinfo=pacific))
    assert shifted == datetime(2026, 8, 12, 9, 0, 0, tzinfo=UTC) and shifted.utcoffset() == timedelta(0)


def test_parse_utc_accepts_the_old_offsetless_form_and_the_new_one():
    expected = datetime(2026, 8, 12, 9, 0, 0, tzinfo=UTC)
    assert parse_utc("2026-08-12T09:00:00") == expected
    assert parse_utc("2026-08-12T09:00:00+00:00") == expected
    assert parse_utc("2026-08-12T09:00:00Z") == expected
    assert parse_utc("") is None and parse_utc(None) is None
    with pytest.raises(ValueError):
        parse_utc("not a date")


def test_no_source_file_reads_a_naive_utc_clock():
    """`datetime.utcnow()` is naive (and deprecated since 3.12): nothing may call it.

    `utc_now()` is the one clock. A scan, not a convention, because the failure it
    prevents is a TypeError in whichever comparison first meets the stray value.
    """
    offenders = []
    for path in ROOT.rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith((".venv/", "venv/", "node_modules/", "dist/", "build/", ".claude/")):
            continue
        if rel == "tests/test_timezone_datetimes.py":
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            code = line.split("#", 1)[0]
            if re.search(r"\butcnow\s*\(", code) or "default_factory=datetime." in code:
                offenders.append(f"{rel}:{n}: {line.strip()}")
    assert not offenders, "use database.clock.utc_now:\n" + "\n".join(offenders)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

def _datetime_fields():
    """(model, field name, FieldInfo) for every datetime field of every table model."""
    import typing

    out = []
    for table in SQLModel.metadata.tables.values():
        model = next((m for m in SQLModel.__subclasses__()
                      if getattr(m, "__tablename__", None) == table.name), None)
        if model is None:
            continue
        for name, info in model.model_fields.items():
            ann = info.annotation
            args = typing.get_args(ann) or (ann,)
            if datetime in args:
                out.append((model, name, info))
    return out


def test_every_datetime_default_is_aware_utc():
    fields = _datetime_fields()
    assert len(fields) > 40, "the scan found too few datetime fields to be meaningful"
    defaulted = 0
    for model, name, info in fields:
        if info.default_factory is None:
            continue
        defaulted += 1
        value = info.default_factory()
        assert value.tzinfo is not None and value.utcoffset() == timedelta(0), (
            f"{model.__name__}.{name} defaults to a naive datetime"
        )
    assert defaulted > 30


def test_every_datetime_column_takes_aware_utc():
    """sqlmodel >= 0.0.47 accepts the aware value for every datetime column of
    every table, on both dialects, and still refuses a naive one."""
    from sqlalchemy.dialects import postgresql, sqlite
    from sqlmodel.sql.sqltypes import UTCDateTime

    assert _version_tuple(version("sqlmodel")) >= (0, 0, 47)
    now = utc_now()
    seen = 0
    for table in SQLModel.metadata.tables.values():
        for col in table.columns:
            underlying = getattr(col.type, "impl", col.type)  # a TypeDecorator wraps its impl
            if not isinstance(underlying, sa.DateTime):
                continue
            seen += 1
            assert isinstance(col.type, UTCDateTime), f"{table.name}.{col.name} is not UTCDateTime"
            for dialect in (sqlite.dialect(), postgresql.dialect()):
                assert col.type.process_bind_param(now, dialect) == now
                with pytest.raises(ValueError, match="timezone"):
                    col.type.process_bind_param(datetime(2026, 1, 1), dialect)
    assert seen > 40


def test_a_row_written_with_utc_now_round_trips_aware(isolated_engine):
    with Session(isolated_engine) as s:
        job = JobDescription(title="Role", company="Co", description="")
        s.add(job)
        s.commit()
        job_id = job.job_id
    with Session(isolated_engine) as s:
        job = s.get(JobDescription, job_id)
        assert job.created_at.utcoffset() == timedelta(0)
        assert abs((utc_now() - job.created_at).total_seconds()) < 60


# ---------------------------------------------------------------------------
# Databases that predate the change
# ---------------------------------------------------------------------------

def _add_jobs(engine, titles):
    with Session(engine) as s:
        for t in titles:
            s.add(JobDescription(title=t, company="Co", description=""))
        s.commit()


def _write_naive(engine, title, stamp):
    """Overwrite a row's timestamps with an offset-less string, bypassing the ORM,
    which is how a pre-#210 writer left them (SQLite text, or a `timestamp`)."""
    with engine.begin() as conn:
        conn.execute(text("UPDATE jobdescription SET created_at = :ts, updated_at = :ts "
                          "WHERE title = :t"), {"ts": stamp, "t": title})


def test_legacy_naive_rows_load_sort_and_compare(isolated_engine):
    _add_jobs(isolated_engine, ["A", "B", "C"])
    _write_naive(isolated_engine, "A", "2026-03-01 10:00:00.500000")
    _write_naive(isolated_engine, "B", "2026-03-01 10:00:00")          # no fraction
    _write_naive(isolated_engine, "C", "2026-01-01 00:00:00.000000")

    with Session(isolated_engine) as s:
        jobs = {j.title: j for j in s.exec(select(JobDescription)).all()}
        for j in jobs.values():
            assert j.created_at.tzinfo is not None and j.created_at.utcoffset() == timedelta(0)
        assert jobs["A"].created_at == datetime(2026, 3, 1, 10, 0, 0, 500000, tzinfo=UTC)
        assert [j.title for j in sorted(jobs.values(), key=lambda j: j.created_at)] == ["C", "B", "A"]
        # Subtracting and comparing against a fresh aware value must not raise.
        assert utc_now() - jobs["C"].created_at > timedelta(days=1)
        assert jobs["A"].created_at > jobs["B"].created_at
        # And the comparison done by the database, against the legacy text.
        cutoff = datetime(2026, 2, 1, tzinfo=UTC)
        older = s.exec(select(JobDescription).where(JobDescription.created_at < cutoff)).all()
        assert [j.title for j in older] == ["C"]

        # A legacy row can be written back: the stored value stays naive-compatible.
        jobs["C"].updated_at = utc_now()
        s.add(jobs["C"])
        s.commit()
    with Session(isolated_engine) as s:
        c = s.exec(select(JobDescription).where(JobDescription.title == "C")).one()
        assert abs((utc_now() - c.updated_at).total_seconds()) < 60
        assert c.created_at == datetime(2026, 1, 1, tzinfo=UTC)


def test_latest_result_and_preferences_order_legacy_rows(isolated_engine):
    """The helpers that sort on a timestamp, over rows a pre-#210 writer left."""
    from database.db import latest_result
    from services import load_preferences

    uid = _seed_user_and_skill(isolated_engine).user_id
    _add_jobs(isolated_engine, ["J"])
    with Session(isolated_engine) as s:
        job_id = s.exec(select(JobDescription)).one().job_id
        for n in range(2):
            s.add(UserJobResult(user_id=uid, job_id=job_id, ats_score=float(n)))
            s.add(UserPreference(user_id=uid, text=f"pref {n}", status="active"))
        s.commit()
    with isolated_engine.begin() as conn:
        # Both kinds, oldest-first by score / text, but written in reverse.
        conn.execute(text("UPDATE userjobresult SET created_at = '2026-05-02 00:00:00' WHERE ats_score = 1"))
        conn.execute(text("UPDATE userjobresult SET created_at = '2026-05-01 00:00:00' WHERE ats_score = 0"))
        conn.execute(text("UPDATE userpreference SET created_at = '2026-05-02 00:00:00' WHERE text = 'pref 1'"))
        conn.execute(text("UPDATE userpreference SET created_at = '2026-05-01 00:00:00' WHERE text = 'pref 0'"))

    with Session(isolated_engine) as s:
        assert latest_result(s.exec(select(UserJobResult)).all()).ats_score == 1.0
    assert [p["text"] for p in load_preferences(uid)] == ["pref 0", "pref 1"]


def test_recency_weight_accepts_naive_and_aware_alike():
    from agents.job_card import _recency_weight

    aware_now = datetime(2026, 7, 20, tzinfo=UTC)
    old = datetime(2026, 4, 21, tzinfo=UTC)
    assert _recency_weight(old, aware_now) == pytest.approx(_recency_weight(old.replace(tzinfo=None), aware_now))
    assert _recency_weight(old.replace(tzinfo=None), aware_now.replace(tzinfo=None)) == pytest.approx(
        _recency_weight(old, aware_now))
    assert _recency_weight("2026-04-21", aware_now) == 0.0


def test_a_recording_with_naive_created_at_imports_aware(isolated_engine):
    """Committed Jev recordings were exported before #210, with offset-less stamps."""
    from harness.decisions import cache, recordings

    entry = {"cache_key": "k1", "point": "p", "question_version": "1", "requested_model": "m",
             "resolved_model": "m", "question": {"q": 1}, "answer": {"a": 1},
             "created_at": "2026-08-01T12:30:00"}
    doc = {"format": recordings.FORMAT, "version": recordings.VERSION, "decisions": [entry]}
    assert recordings.import_recordings(doc)["added"] == 1
    (row,) = cache.all_rows()
    assert row.created_at == datetime(2026, 8, 1, 12, 30, tzinfo=UTC)
    exported = recordings.export_recordings()
    assert exported["decisions"][0]["created_at"] == "2026-08-01T12:30:00+00:00"
    assert re.match(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+00:00$", exported["exported_at"])


@requires_postgres
def test_postgres_timestamp_without_time_zone_columns_still_work(isolated_engine):
    """Production Supabase's columns were created `timestamp WITHOUT time zone`.

    `create_all` builds `timestamptz` now, so the test schema is converted to the
    old shape first. Writes must land as UTC even from a session whose own time
    zone is not UTC, reads must come back aware, and a legacy naive value must
    compare against a fresh one.
    """
    from database.db import pin_utc_session

    with isolated_engine.begin() as conn:
        cols = conn.execute(text(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND data_type = 'timestamp with time zone'"
        )).fetchall()
        assert len(cols) > 40
        for table, column in cols:
            conn.execute(text(f'ALTER TABLE "{table}" ALTER COLUMN "{column}" '
                              f"TYPE timestamp without time zone USING \"{column}\" AT TIME ZONE 'UTC'"))

    # A connection whose session zone is deliberately not UTC, with the app's pin on top.
    url = isolated_engine.url
    with isolated_engine.connect() as conn:
        schema = conn.execute(text("SELECT current_schema()")).scalar()
    odd =sa.create_engine(url, connect_args={
        "options": f"-csearch_path={schema},public -ctimezone=America/Los_Angeles"})
    pin_utc_session(odd)
    try:
        stamp = datetime(2026, 8, 12, 9, 0, 0, 123456, tzinfo=UTC)
        with Session(odd) as s:
            assert s.execute(text("SHOW TimeZone")).scalar() == "UTC"
            job = JobDescription(title="Legacy", company="Co", description="", created_at=stamp)
            s.add(job)
            s.commit()
            job_id = job.job_id
        with odd.connect() as conn:
            raw = conn.execute(text("SELECT created_at FROM jobdescription")).scalar()
        assert raw == datetime(2026, 8, 12, 9, 0, 0, 123456), "stored as naive UTC, not shifted"
        with Session(odd) as s:
            back = s.get(JobDescription, job_id)
            assert back.created_at == stamp and back.created_at.utcoffset() == timedelta(0)
            assert utc_now() - back.created_at > timedelta(days=1)
            earlier = s.exec(select(JobDescription).where(
                JobDescription.created_at < datetime(2026, 9, 1, tzinfo=UTC))).all()
            assert [j.job_id for j in earlier] == [job_id]
    finally:
        odd.dispose()
