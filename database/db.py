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
        "ALTER TABLE jobdescription ADD COLUMN user_id TEXT",
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
    ]
    with engine.connect() as conn:
        for stmt in migrations:
            try:
                conn.execute(text(stmt))
                conn.commit()
            except Exception:
                conn.rollback()  # PostgreSQL aborts the txn on error; rollback resets it


def init_db():
    SQLModel.metadata.create_all(engine)
    _migrate_db()

def get_session():
    with Session(engine) as session:
        yield session
