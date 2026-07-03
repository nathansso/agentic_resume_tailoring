"""
Shared fixtures and helpers for the ART test suite.

All test files in this directory have access to these fixtures automatically.
The _seed_user_and_skill helper can be imported: from conftest import _seed_user_and_skill
"""
import sys
from pathlib import Path

# Ensure project root is importable when pytest is invoked from any directory.
# pytest.ini pythonpath=. also handles this; this is a belt-and-suspenders guard.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest
from sqlmodel import SQLModel, Session, create_engine

import agents.chat as chat_module
import database.db as db_module
import database.user_utils as user_utils_module
import knowledge_graph.builder as kg_builder_module
import services as services_module
from database.models import User, Skill, UserSkill


@pytest.fixture()
def isolated_engine(tmp_path, monkeypatch):
    """SQLite engine backed by a temp file, with all module-level engine refs patched."""
    db_path = tmp_path / "test_art.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)

    profile_file = tmp_path / "active_profile_id"

    monkeypatch.setattr(db_module, "engine", engine)
    monkeypatch.setattr(chat_module, "engine", engine)
    monkeypatch.setattr(kg_builder_module, "engine", engine)
    monkeypatch.setattr(services_module, "engine", engine)
    monkeypatch.setattr(user_utils_module, "engine", engine)
    monkeypatch.setattr(user_utils_module, "ACTIVE_PROFILE_FILE", profile_file)
    monkeypatch.setattr(user_utils_module, "ART_DIR", tmp_path)

    engine._test_profile_file = profile_file
    return engine


def _seed_user_and_skill(engine):
    """Seed a test user with one Python skill and write the active-profile pointer.

    Returns a SimpleNamespace(user_id=...) so callers can access user_id without
    SQLAlchemy session state complications.
    """
    from types import SimpleNamespace

    with Session(engine) as session:
        user = User(name="Test User", email="test@example.com")
        session.add(user)
        session.commit()
        session.refresh(user)
        uid = user.user_id

        skill = Skill(name="Python", category="language")
        session.add(skill)
        session.commit()
        session.refresh(skill)

        user_skill = UserSkill(
            user_id=uid,
            skill_id=skill.skill_id,
            proficiency=5,
            evidence_source="resume",
            confidence_score=0.95,
        )
        session.add(user_skill)
        session.commit()

    if hasattr(engine, "_test_profile_file"):
        engine._test_profile_file.write_text(str(uid))

    return SimpleNamespace(user_id=uid)
