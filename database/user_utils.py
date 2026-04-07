from sqlmodel import Session, select
from database.models import User
from database.db import engine

def get_or_create_default_user() -> User:
    with Session(engine) as session:
        # Check if any user exists
        statement = select(User).limit(1)
        results = session.exec(statement)
        user = results.first()
        
        if user:
            return user
            
        # Create default user
        print("Creating default user...")
        new_user = User(
            name="Default User",
            email="user@example.com",
            linkedin_url="",
            github_username=""
        )
        session.add(new_user)
        session.commit()
        session.refresh(new_user)
        return new_user
