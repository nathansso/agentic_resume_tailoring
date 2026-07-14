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


def init_db():
    SQLModel.metadata.create_all(engine)
    _migrate_db()
    _migrate_pg_uuid_columns()

def get_session():
    with Session(engine) as session:
        yield session
