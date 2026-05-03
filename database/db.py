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


_migrate_db_location()

# connect_args needed for SQLite to allow usage across threads
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})

def _migrate_db() -> None:
    """Apply incremental column additions for existing DBs (SQLite, no Alembic)."""
    from sqlalchemy import text
    migrations = [
        # PRD 03
        "ALTER TABLE user ADD COLUMN onboarding_complete INTEGER DEFAULT 0",
        "ALTER TABLE user ADD COLUMN onboarding_steps TEXT DEFAULT '{}'",
        # PRD 04
        "ALTER TABLE jobdescription ADD COLUMN status TEXT DEFAULT 'created'",
        "ALTER TABLE jobdescription ADD COLUMN description TEXT DEFAULT ''",
        "ALTER TABLE userjobresult ADD COLUMN revision_notes TEXT",
        "ALTER TABLE userjobresult ADD COLUMN export_path TEXT",
        # PRD 07
        "ALTER TABLE user ADD COLUMN resume_path TEXT",
    ]
    with engine.connect() as conn:
        for stmt in migrations:
            try:
                conn.execute(text(stmt))
                conn.commit()
            except Exception:
                pass  # Column already exists — safe to ignore


def init_db():
    SQLModel.metadata.create_all(engine)
    _migrate_db()

def get_session():
    with Session(engine) as session:
        yield session
