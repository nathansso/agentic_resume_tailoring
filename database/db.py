from sqlmodel import SQLModel, create_engine, Session
from config import DATABASE_URL
from database.models import * # Import all models to register them

# connect_args needed for SQLite to allow usage across threads
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})

def init_db():
    print("Initializing Database...")
    SQLModel.metadata.create_all(engine)
    print(f"Database created at {DATABASE_URL}")

def get_session():
    with Session(engine) as session:
        yield session
