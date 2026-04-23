from sqlmodel import SQLModel, create_engine, Session
from config import DATABASE_URL
from database.models import * # Import all models to register them

# connect_args needed for SQLite to allow usage across threads
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})

def _migrate_db() -> None:
    """Apply incremental column additions for existing DBs (SQLite, no Alembic)."""
    from sqlalchemy import text
    migrations = [
        "ALTER TABLE user ADD COLUMN onboarding_complete INTEGER DEFAULT 0",
        "ALTER TABLE user ADD COLUMN onboarding_steps TEXT DEFAULT '{}'",
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
