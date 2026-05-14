import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session, select
from database.db import engine
from database.models import Skill, Project

def inspect():
    with Session(engine) as session:
        skills = session.exec(select(Skill).limit(20)).all()
        projects = session.exec(select(Project).limit(5)).all()
        
        print("--- Skills (Sample) ---")
        for s in skills:
            print(f"- {s.name}")
            
        print("\n--- Projects (Sample) ---")
        for p in projects:
            print(f"- {p.name}: {p.description}")

if __name__ == "__main__":
    inspect()
