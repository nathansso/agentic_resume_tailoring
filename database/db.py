from sqlmodel import SQLModel, create_engine, Session
from config import DATABASE_URL
from database.models import * # Import all models to register them


def _migrate_db_location() -> None:
    """One-time, non-destructive copy of art.db from project root → ~/.art/art.db."""
    import shutil
    import logging
    from config import APP_DATA_DIR, BASE_DIR

    old_db = BASE_DIR / "art.db"
    new_db = APP_DATA_DIR / "art.db"
    if old_db.exists() and not new_db.exists():
        try:
            APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
            shutil.copy2(old_db, new_db)
            logging.getLogger("ART").info("Migrated art.db to ~/.art/art.db")
        except Exception as exc:
            logging.getLogger("ART").warning("DB migration skipped: %s", exc)


_sqlite = DATABASE_URL.startswith("sqlite")

if _sqlite:
    _migrate_db_location()

# check_same_thread is SQLite-only; PostgreSQL handles threading natively
_connect_args = {"check_same_thread": False} if _sqlite else {}
engine = create_engine(DATABASE_URL, connect_args=_connect_args)

def _migrate_db() -> None:
    """Apply incremental column additions for existing DBs (SQLite and PostgreSQL)."""
    from sqlalchemy import text
    # "user" is a reserved word in PostgreSQL — quote it; SQLite accepts quoted identifiers too
    migrations = [
        # PRD 03
        'ALTER TABLE "user" ADD COLUMN onboarding_complete INTEGER DEFAULT 0',
        "ALTER TABLE \"user\" ADD COLUMN onboarding_steps TEXT DEFAULT '{}'",
        # PRD 04
        "ALTER TABLE jobdescription ADD COLUMN status TEXT DEFAULT 'created'",
        "ALTER TABLE jobdescription ADD COLUMN description TEXT DEFAULT ''",
        "ALTER TABLE userjobresult ADD COLUMN revision_notes TEXT",
        "ALTER TABLE userjobresult ADD COLUMN export_path TEXT",
        # PRD 07
        'ALTER TABLE "user" ADD COLUMN resume_path TEXT',
        # contact info fields
        'ALTER TABLE "user" ADD COLUMN phone TEXT',
        'ALTER TABLE "user" ADD COLUMN location TEXT',
        # resume style capture
        'ALTER TABLE "user" ADD COLUMN resume_markdown TEXT',
        'ALTER TABLE "user" ADD COLUMN resume_style TEXT',
        # issue 24: persisted chat summaries
        "ALTER TABLE jobdescription ADD COLUMN chat_summary TEXT",
        # issue 35: auth columns
        'ALTER TABLE "user" ADD COLUMN username TEXT',
        'ALTER TABLE "user" ADD COLUMN password_hash TEXT',
        'ALTER TABLE "user" ADD COLUMN supabase_uid TEXT',
        # UUID, not TEXT: on PostgreSQL a text column can't be compared to the
        # model's uuid params (SQLite doesn't care). _migrate_pg_uuid_columns
        # repairs DBs that already got the TEXT version.
        "ALTER TABLE jobdescription ADD COLUMN user_id UUID",
        # issue 4: GitHub OAuth token per user
        'ALTER TABLE "user" ADD COLUMN github_access_token TEXT',
        # issue 2: ATS scoring breakdown
        "ALTER TABLE userjobresult ADD COLUMN score_breakdown TEXT DEFAULT '{}'",
        # issue 12: algorithmic score of tailored output
        "ALTER TABLE userjobresult ADD COLUMN tailored_score_breakdown TEXT DEFAULT '{}'",
        # issue 46: GitHub project metrics for complexity scoring
        "ALTER TABLE project ADD COLUMN metrics TEXT DEFAULT '{}'",
        # issue 13: LinkedIn (Bright Data) ingestion lifecycle
        'ALTER TABLE "user" ADD COLUMN linkedin_ingested_url TEXT',
        'ALTER TABLE "user" ADD COLUMN linkedin_ingest_status TEXT',
        'ALTER TABLE "user" ADD COLUMN linkedin_ingest_error TEXT',
        'ALTER TABLE "user" ADD COLUMN linkedin_ingested_at TIMESTAMP',
        # issue 13: per-kind usage cap (LinkedIn scrapes are paid; capped separately)
        "ALTER TABLE aiusage ADD COLUMN kind TEXT DEFAULT 'ai'",
        # issue 54: cached skill/JD embeddings for the semantic scoring component
        "ALTER TABLE skill ADD COLUMN embedding TEXT",
        "ALTER TABLE skill ADD COLUMN embedding_model TEXT",
        "ALTER TABLE jobdescription ADD COLUMN embedding TEXT",
        "ALTER TABLE jobdescription ADD COLUMN embedding_model TEXT",
        # issue 54: user-pinned core skills (always rendered)
        "ALTER TABLE userskill ADD COLUMN is_core BOOLEAN DEFAULT FALSE",
        # issue 73: landing-context chat messages are scoped per user
        "ALTER TABLE chatmessage ADD COLUMN user_id UUID",
        # issue 75: personal-site link (header) and project demo link (auto-embed)
        'ALTER TABLE "user" ADD COLUMN portfolio_url TEXT',
        "ALTER TABLE project ADD COLUMN demo_url TEXT",
        # issue 70: lifetime per-job tailor-run counter
        "ALTER TABLE jobdescription ADD COLUMN retailor_count INTEGER DEFAULT 0",
        # issue 71: manually edited resume .tex per tailoring result
        "ALTER TABLE userjobresult ADD COLUMN edited_tex TEXT",
        "ALTER TABLE userjobresult ADD COLUMN edited_tex_updated_at TIMESTAMP",
        # issue 69: persisted raw LinkedIn scrape JSON for replay
        'ALTER TABLE "user" ADD COLUMN linkedin_raw_record TEXT',
        # issue 92: manual-edit protection for knowledge-graph rows
        "ALTER TABLE experience ADD COLUMN manually_edited BOOLEAN DEFAULT FALSE",
        "ALTER TABLE education ADD COLUMN manually_edited BOOLEAN DEFAULT FALSE",
        "ALTER TABLE project ADD COLUMN manually_edited BOOLEAN DEFAULT FALSE",
        # issues 91/51: per-run tailoring decision log (planner actions + reward)
        "ALTER TABLE userjobresult ADD COLUMN tailoring_decisions TEXT DEFAULT '[]'",
        # issue 91: one-level undo for chat REVERT
        "ALTER TABLE userjobresult ADD COLUMN tailored_resume_previous TEXT DEFAULT '{}'",
        # issue 21: soft origin-chat back-reference on chat-captured artifacts.
        # Free-form text, never an FK — job/chat deletion must not cascade here.
        "ALTER TABLE userskill ADD COLUMN source_context TEXT",
        "ALTER TABLE project ADD COLUMN source_context TEXT",
        "ALTER TABLE experience ADD COLUMN source_context TEXT",
        # issue 118: explicit user arrangement overrides. Nullable with no
        # default — NULL is the "ranker decides" case, so existing rows keep
        # behaving exactly as they did.
        "ALTER TABLE userjobresult ADD COLUMN layout_overrides TEXT",
        # issue 180: per-conversation insertion ordinal. Plain INTEGER, not an
        # autoincrement: SQLite cannot add an AUTOINCREMENT column to an existing
        # table and Postgres would need BIGSERIAL, so a portable ALTER has to
        # carry an ordinal the write path assigns itself. Backfilled by
        # _backfill_chat_seq below.
        "ALTER TABLE chatmessage ADD COLUMN seq INTEGER",
        # issue 180: résumé-document ordinal, same mechanism. Ingestion writes
        # every row of a section in one loop, so they share a `created_at` and
        # ordering on it alone is undefined — and this one reaches the rendered
        # resume. Backfilled by _backfill_document_seq below.
        "ALTER TABLE experience ADD COLUMN seq INTEGER",
        "ALTER TABLE education ADD COLUMN seq INTEGER",
        "ALTER TABLE achievement ADD COLUMN seq INTEGER",
        "ALTER TABLE project ADD COLUMN seq INTEGER",
        # issue 180: per-(user, job) run ordinal, so "the latest result" is
        # decided by the run that produced it. Backfilled by
        # _backfill_result_seq below.
        "ALTER TABLE userjobresult ADD COLUMN seq INTEGER",
    ]
    with engine.connect() as conn:
        for stmt in migrations:
            try:
                conn.execute(text(stmt))
                conn.commit()
            except Exception:
                conn.rollback()  # PostgreSQL aborts the txn on error; rollback resets it


def _uuid_column_fix_statements(text_columns: set) -> list:
    """ALTER statements converting TEXT columns to uuid where the model says UUID.

    *text_columns* is a set of (table_name, column_name) pairs that are
    text/varchar in the live database. Columns added by the raw ALTER TABLE
    migrations above were typed TEXT — fine on SQLite, but on PostgreSQL a
    `text_col = uuid_param` comparison has no operator and every query
    filtering on the column 500s. NULLIF handles empty strings left over from
    SQLite-era rows.
    """
    import sqlalchemy as sa
    stmts = []
    for table in SQLModel.metadata.tables.values():
        for col in table.columns:
            if isinstance(col.type, sa.Uuid) and (table.name, col.name) in text_columns:
                stmts.append(
                    f'ALTER TABLE "{table.name}" ALTER COLUMN "{col.name}" '
                    f"TYPE uuid USING NULLIF(\"{col.name}\", '')::uuid"
                )
    return stmts


def _migrate_pg_uuid_columns() -> None:
    """PostgreSQL only: retype UUID-model columns that exist as TEXT."""
    # Check the live engine's dialect, not DATABASE_URL: tests patch in a
    # SQLite engine while the env var may still point at PostgreSQL.
    if engine.dialect.name != "postgresql":
        return
    from sqlalchemy import text
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema() "
            "AND data_type IN ('text', 'character varying')"
        )).fetchall()
        for stmt in _uuid_column_fix_statements({(r[0], r[1]) for r in rows}):
            try:
                conn.execute(text(stmt))
                conn.commit()
            except Exception:
                conn.rollback()


def _migrate_pg_vector_columns() -> None:
    """PostgreSQL only: enable pgvector and add vector(384) accelerator columns.

    Guarded on the live engine's dialect (issue #142), the same way
    _migrate_pg_uuid_columns is — tests patch in a SQLite engine while the env
    var may still point at PostgreSQL, so DATABASE_URL is not consulted here.

    The JSON `embedding` TEXT column stays the SQLite path and the portable
    source of truth; this only adds a Postgres-side ANN column beside it, so a
    SQLite DB never gains (or attempts) a vector column. Idempotent via
    IF NOT EXISTS.
    """
    if engine.dialect.name != "postgresql":
        return
    from sqlalchemy import text
    import logging
    statements = [
        "CREATE EXTENSION IF NOT EXISTS vector",
        "ALTER TABLE skill ADD COLUMN IF NOT EXISTS embedding_vec vector(384)",
        "ALTER TABLE jobdescription ADD COLUMN IF NOT EXISTS embedding_vec vector(384)",
    ]
    with engine.connect() as conn:
        for stmt in statements:
            try:
                conn.execute(text(stmt))
                conn.commit()
            except Exception as exc:  # extension unavailable / perms — degrade
                conn.rollback()
                logging.getLogger("ART").warning(
                    "pgvector migration step skipped (%s): %s", stmt.split()[0:2], exc
                )


def _assign_missing_seq(session, model, scope_columns, sort_key) -> bool:
    """Give every `seq IS NULL` row in *model* an ordinal. Returns whether any.

    The shared body of all three backfills below. They differ only in how rows
    are grouped into series (*scope_columns*) and how a series is ordered
    (*sort_key*); the algorithm is identical, and three hand-copies of it would
    be three places for the NULL handling and the continue-after-existing rule
    to drift apart.

    Python-side rather than one UPDATE with a window function: `UPDATE ... FROM`
    is spelled differently on SQLite and Postgres, and the row counts are
    bounded (chat is capped per conversation; résumé and result rows are
    per-user and small).

    Touches only unassigned rows, so it is idempotent — it runs on every
    `init_db()` and costs one indexed lookup once everything is assigned.
    """
    from sqlmodel import select

    pending = session.exec(select(model).where(model.seq.is_(None))).all()
    if not pending:
        return False

    series: dict = {}
    for row in pending:
        series.setdefault(tuple(getattr(row, c) for c in scope_columns),
                          []).append(row)

    for key, rows in series.items():
        # Where a series is partly assigned, continue after its highest
        # existing ordinal rather than restarting at 0 and colliding.
        query = select(model).where(model.seq.is_not(None))
        for column, value in zip(scope_columns, key):
            col = getattr(model, column)
            # `is_(None)` rather than `== None`: chat's landing context is
            # keyed by a NULL job_id, and `= NULL` matches nothing in SQL.
            query = query.where(col.is_(None) if value is None else col == value)
        assigned = [r.seq for r in session.exec(query).all()]
        next_free = max(assigned) + 1 if assigned else 0

        rows.sort(key=sort_key)
        for offset, row in enumerate(rows):
            row.seq = next_free + offset
            session.add(row)
    return True


def _backfill_chat_seq() -> None:
    """Assign `seq` to chat rows that predate the column (issue #180).

    Grouped per conversation — a job's thread, or one user's landing context.

    **The ordering rule for legacy rows.** Rows are ordered by `created_at`,
    then `user` before `assistant`, then `message_id`. The middle term is a
    deliberate heuristic over history that is otherwise unrecoverable: the
    dominant tie is a question and its reply written in the same clock tick, and
    this recovers that pair the right way round. For any other tie, true
    insertion order is **not recoverable** — the order assigned here is
    arbitrary, but it is frozen at backfill and deterministic after it.
    """
    from sqlmodel import Session
    from database.models import ChatMessage

    with Session(engine) as session:
        if _assign_missing_seq(
            session, ChatMessage, ("job_id", "user_id"),
            lambda r: (r.created_at, 0 if r.role == "user" else 1,
                       str(r.message_id)),
        ):
            session.commit()


def next_seq(session, model, user_id, **extra_scope) -> int:
    """The next ordinal for this user's rows in *model* (issue #180).

    Shared by every write path that appends to `Experience`, `Education`,
    `Achievement`, `Project` or `UserJobResult`, so position is recorded once at
    write time rather than guessed at read time from a timestamp the whole
    writing loop shares.

    *extra_scope* narrows the series further as `column=value` pairs.
    `UserJobResult` passes `job_id=` so each job carries its own run sequence;
    the résumé tables pass nothing and are numbered per user.

    Called inside the caller's open session, typically mid-loop with earlier
    rows still pending. SQLAlchemy autoflushes before evaluating a query, so
    those pending rows are included in the MAX and consecutive calls return
    consecutive ordinals — the behaviour this relies on, stated because it is
    not obvious from the call site.
    """
    from sqlalchemy import func
    from sqlmodel import select

    query = select(func.max(model.seq)).where(model.user_id == user_id)
    for column, value in extra_scope.items():
        query = query.where(getattr(model, column) == value)
    highest = session.exec(query).one()
    return 0 if highest is None else highest + 1


def latest_result(results):
    """The most recent `UserJobResult` in *results*, or None if empty (#180).

    Replaces eight copies of `max(results, key=lambda r: r.created_at)`. That
    form is undefined under a tie: `max` returns the first maximal element in
    iteration order, and the iteration order is an unordered `select()` — so
    "the latest tailoring result" could be either of two written in one tick,
    and could differ between the two engines. This value decides the score shown
    for a job and the content exported for it.

    Ordered on the run ordinal, so which row wins is **determined by the run
    that produced it** rather than by a timestamp two runs can share. `seq` is
    assigned per (user, job) at the single write site, `agents/matcher.py`.

    Rows with no ordinal sort as oldest and fall back to `(created_at,
    result_id)` among themselves. That covers two cases: rows predating the
    column, in the window before `_backfill_result_seq` runs, and rows built
    directly by a test or fixture. A set where none is assigned therefore
    behaves exactly as it did before, rather than resolving arbitrarily.
    """
    return max(
        results,
        key=lambda r: (r.seq if r.seq is not None else -1,
                       r.created_at, str(r.result_id)),
        default=None,
    )


def _backfill_document_seq() -> None:
    """Assign `seq` to résumé rows that predate the column (issue #180).

    Same shape and the same honest limit as `_backfill_chat_seq`: rows are
    ordered by `(created_at, <pk>)` per user, so a database whose rows carry
    distinct timestamps is restored to its true document order, while rows
    written in one ingestion tick get an arbitrary order that is frozen here and
    deterministic afterwards.

    There is no role-style heuristic to apply on this side — nothing in a
    résumé row indicates which of two entries came first in the source document
    — so none is invented.
    """
    from sqlmodel import Session
    from database.models import Achievement, Education, Experience, Project

    tables = [
        (Experience, "experience_id"),
        (Education, "education_id"),
        (Achievement, "achievement_id"),
        (Project, "project_id"),
    ]
    with Session(engine) as session:
        wrote = False
        for model, pk in tables:
            # `pk=pk` binds the loop variable per iteration; a bare closure over
            # `pk` would sort every table by the last primary key in the list.
            wrote |= _assign_missing_seq(
                session, model, ("user_id",),
                lambda r, pk=pk: (r.created_at, str(getattr(r, pk))),
            )
        if wrote:
            session.commit()


def _backfill_result_seq() -> None:
    """Assign `seq` to tailoring results that predate the column (issue #180).

    Grouped per (user, job), so each job carries its own run sequence.

    Ordered by `(created_at, result_id)`. As with the résumé tables there is no
    heuristic to apply: nothing on a result records which of two same-tick runs
    finished second. Distinct timestamps — which is what deployed data carries,
    since Railway runs Linux — are restored to true run order.
    """
    from sqlmodel import Session
    from database.models import UserJobResult

    with Session(engine) as session:
        if _assign_missing_seq(
            session, UserJobResult, ("user_id", "job_id"),
            lambda r: (r.created_at, str(r.result_id)),
        ):
            session.commit()


def init_db():
    SQLModel.metadata.create_all(engine)
    _migrate_db()
    _migrate_pg_uuid_columns()
    _migrate_pg_vector_columns()
    _backfill_chat_seq()
    _backfill_document_seq()
    _backfill_result_seq()

def get_session():
    with Session(engine) as session:
        yield session
