from sqlmodel import SQLModel, create_engine, Session
from config import DATABASE_URL
from database.models import * # Import all models to register them

engine = create_engine(DATABASE_URL)

def init_db():
    print("Initializing Database...")
    SQLModel.metadata.create_all(engine)
    print(f"Database created at {DATABASE_URL}")

def get_session():
    with Session(engine) as session:
        yield session
