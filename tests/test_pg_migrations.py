"""Postgres migration-chain tests (issue #149).

Until #149 the suite never called `init_db()` at all — `conftest` went straight
to `SQLModel.metadata.create_all`, which builds every column from the model
definitions and therefore always gets the types right. The raw `ALTER TABLE`
list in `database/db.py::_migrate_db` — the thing that actually runs against
real databases — was exercised by nothing.

That is precisely how the UUID defect shipped. `ALTER TABLE jobdescription ADD
COLUMN user_id UUID` was originally written without the type, producing a TEXT
column. On SQLite that is invisible (no real types); on Postgres
`text_col = uuid_param` has no operator, so every query filtering that column
500s. `_migrate_pg_uuid_columns` exists solely to repair databases that got the
TEXT version.

These tests close that gap from both directions: the forward invariant that no
future migration may add a UUID column as TEXT, and the regression that the
existing self-heal actually heals.
"""
import sqlalchemy as sa
from sqlalchemy import text
from sqlmodel import SQLModel, Session, select

from conftest import requires_postgres


def _assert_on_a_test_schema(engine):
    """Refuse to run DDL anywhere but a throwaway test schema.

    These tests call `init_db()`, which issues ALTER TABLE against whatever
    `database.db.engine` currently points at. A developer's `.env` routinely
    holds the production Supabase DSN, so "the fixture surely patched it" is
    not a good enough guarantee for a function that mutates schemas. Assert it.
    """
    with Session(engine) as session:
        schema = session.execute(text("SELECT current_schema()")).scalar()
    assert schema and schema.startswith("art_test_"), (
        f"refusing to run migrations against schema {schema!r} — "
        "expected a throwaway art_test_* schema"
    )


def _make_user_id_legacy_text(session):
    """Put jobdescription.user_id into the state the old migration left it in.

    Drop and re-add rather than `ALTER COLUMN ... TYPE text`: `create_all` gives
    the column a foreign key, and Postgres refuses to retype a column under an
    FK to a type incompatible with its target ("Key columns are of incompatible
    types: text and uuid").

    That refusal is itself the point. The historical
    `ALTER TABLE jobdescription ADD COLUMN user_id TEXT` added a **bare column
    with no constraint**, which is exactly why the bad type was accepted and
    then sat there unnoticed. Dropping the column first reproduces the real
    legacy shape; retyping in place would be testing a state that never existed.
    """
    session.execute(text("ALTER TABLE jobdescription DROP COLUMN user_id"))
    session.execute(text("ALTER TABLE jobdescription ADD COLUMN user_id TEXT"))
    session.commit()


def _column_types(session):
    """{(table, column): data_type} for the current schema."""
    rows = session.execute(text(
        "SELECT table_name, column_name, data_type FROM information_schema.columns "
        "WHERE table_schema = current_schema()"
    )).fetchall()
    return {(r[0], r[1]): r[2] for r in rows}


def _model_uuid_columns():
    """Every (table, column) the models declare as a UUID."""
    return {
        (table.name, col.name)
        for table in SQLModel.metadata.tables.values()
        for col in table.columns
        if isinstance(col.type, sa.Uuid)
    }


@requires_postgres
def test_every_uuid_model_column_is_uuid_typed_after_init_db(isolated_engine):
    """No UUID-typed model column may exist as text after the full chain runs.

    The forward-looking half: this fails if any future migration adds a UUID
    column without its type, which is the bug class that produced
    _migrate_pg_uuid_columns in the first place.
    """
    import database.db as db

    _assert_on_a_test_schema(isolated_engine)
    db.init_db()  # create_all + _migrate_db + both pg repairs

    with Session(isolated_engine) as session:
        live = _column_types(session)
        mistyped = {
            key for key in _model_uuid_columns()
            if key in live and live[key] != "uuid"
        }

    assert not mistyped, (
        f"UUID model columns exist as non-uuid types in Postgres: {sorted(mistyped)}. "
        "A migration added them without a type — see database/db.py::_migrate_db."
    )


@requires_postgres
def test_pg_uuid_migration_repairs_a_legacy_text_column(isolated_engine):
    """The self-heal converts a legacy TEXT user_id back to uuid.

    Reproduces the original defect by retyping the column to text, the state a
    database created by the old migration is in.
    """
    import database.db as db

    _assert_on_a_test_schema(isolated_engine)
    with Session(isolated_engine) as session:
        _make_user_id_legacy_text(session)
        assert _column_types(session)[("jobdescription", "user_id")] == "text"

    db._migrate_pg_uuid_columns()

    with Session(isolated_engine) as session:
        assert _column_types(session)[("jobdescription", "user_id")] == "uuid"


@requires_postgres
def test_uuid_filtered_query_works_after_repair(isolated_engine):
    """The repair restores the query that the TEXT column made impossible.

    This is the actual production symptom, not a proxy for it: filtering
    JobDescription by user_id is what 500'd, so the test asserts the query
    itself runs rather than only inspecting information_schema.
    """
    import database.db as db
    from database.models import JobDescription
    from conftest import _seed_user_and_skill

    _assert_on_a_test_schema(isolated_engine)
    seeded = _seed_user_and_skill(isolated_engine)

    with Session(isolated_engine) as session:
        _make_user_id_legacy_text(session)

    db._migrate_pg_uuid_columns()

    with Session(isolated_engine) as session:
        job = JobDescription(
            title="Backend Engineer", company="Acme", user_id=seeded.user_id
        )
        session.add(job)
        session.commit()

        # On the text column this raises ProgrammingError: operator does not
        # exist: text = uuid.
        found = session.exec(
            select(JobDescription).where(JobDescription.user_id == seeded.user_id)
        ).all()

    assert [j.title for j in found] == ["Backend Engineer"]


@requires_postgres
def test_vector_columns_exist_after_init_db(isolated_engine):
    """_migrate_pg_vector_columns actually adds the pgvector accelerator columns.

    Guarded on dialect, so on the SQLite leg it is a no-op that
    test_vector_search pins from the other side.
    """
    import database.db as db

    _assert_on_a_test_schema(isolated_engine)
    db.init_db()

    with Session(isolated_engine) as session:
        live = _column_types(session)

    assert ("skill", "embedding_vec") in live
    assert ("jobdescription", "embedding_vec") in live
    # The portable JSON column stays the source of truth beside it. SQLModel
    # maps Optional[str] to VARCHAR, so this is "character varying" on
    # Postgres and "text" on SQLite — assert it is a string column, not a
    # specific spelling of one.
    assert live[("skill", "embedding")] in ("text", "character varying")
