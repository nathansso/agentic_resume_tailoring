import asyncio
import subprocess
import sys
import time
from pathlib import Path

import pytest
from sqlmodel import SQLModel, Session, create_engine, select
from textual.widgets import Input

import agents.chat as chat_module
import database.db as db_module
import knowledge_graph.builder as kg_builder_module
import tui.app as tui_module
from database.models import JobDescription, Skill, User, UserSkill

ROOT = Path(__file__).resolve().parent


@pytest.fixture()
def isolated_engine(tmp_path, monkeypatch):
    db_path = tmp_path / "test_art.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)

    # Point all modules used by these tests at the isolated DB.
    monkeypatch.setattr(db_module, "engine", engine)
    monkeypatch.setattr(chat_module, "engine", engine)
    monkeypatch.setattr(tui_module, "engine", engine)
    monkeypatch.setattr(kg_builder_module, "engine", engine)
    monkeypatch.setattr(tui_module, "init_db", lambda: None)

    return engine


def _seed_user_and_skill(engine):
    with Session(engine) as session:
        user = User(name="Test User", email="test@example.com")
        session.add(user)
        session.commit()
        session.refresh(user)

        skill = Skill(name="Python", category="language")
        session.add(skill)
        session.commit()
        session.refresh(skill)

        user_skill = UserSkill(
            user_id=user.user_id,
            skill_id=skill.skill_id,
            proficiency=5,
            evidence_source="resume",
            confidence_score=0.95,
        )
        session.add(user_skill)
        session.commit()


def test_chat_semantic_routing_uses_tool_and_is_fast(isolated_engine, monkeypatch):
    _seed_user_and_skill(isolated_engine)

    class ShouldNotBeCalledLLM:
        def invoke(self, *_args, **_kwargs):
            raise AssertionError("LLM should not be called for command-like skill query")

    monkeypatch.setattr(chat_module, "get_llm", lambda temperature=0.2: ShouldNotBeCalledLLM())

    agent = chat_module.ChatAgent()

    start = time.perf_counter()
    response = agent.chat("could you please shwo me all my skils")
    duration = time.perf_counter() - start

    assert response.startswith("Your skills (")
    assert "Python" in response
    assert "| source:" in response
    assert duration < 1.0


def test_tui_new_job_flow(isolated_engine):
    async def _run():
        app = tui_module.ArtApp()

        with Session(isolated_engine) as session:
            count_before = len(session.exec(select(JobDescription)).all())

        async with app.run_test() as pilot:
            await pilot.pause()

            # Open inline form and attempt invalid save.
            app.action_new_job()
            await pilot.pause()
            app._save_new_job()
            await pilot.pause()

            with Session(isolated_engine) as session:
                count_after_invalid = len(session.exec(select(JobDescription)).all())
            assert count_after_invalid == count_before

            # Valid save.
            app.query_one("#job-title-input", Input).value = "Smoke Test Role"
            app.query_one("#job-company-input", Input).value = "SmokeCo"
            app._save_new_job()
            await pilot.pause()

            assert app.query_one("#job-title-input", Input).value == ""
            assert app.query_one("#job-company-input", Input).value == ""
            assert not app.query_one("#job-input-area").has_class("visible")

        with Session(isolated_engine) as session:
            count_after_valid = len(session.exec(select(JobDescription)).all())
            created = session.exec(
                select(JobDescription).where(
                    JobDescription.title == "Smoke Test Role",
                    JobDescription.company == "SmokeCo",
                )
            ).first()

        assert count_after_valid == count_before + 1
        assert created is not None

    asyncio.run(_run())


@pytest.mark.integration
@pytest.mark.slow
def test_full_cli_ingestion_and_tailor_pipeline():
    py = sys.executable

    steps = [
        [py, "cli.py", "ingest-resume", "Nathaniel Oliver Resume - 3_27_6.md"],
        [py, "cli.py", "ingest-github"],
        [py, "cli.py", "tailor", "test.txt"],
    ]

    for cmd in steps:
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        assert proc.returncode == 0, (
            f"Command failed: {' '.join(cmd)}\n"
            f"STDOUT:\n{proc.stdout[-2000:]}\n"
            f"STDERR:\n{proc.stderr[-2000:]}"
        )

    out_json = ROOT / "tailored_output.json"
    out_md = ROOT / "tailored_resume.md"

    assert out_json.exists() and out_json.stat().st_size > 0
    assert out_md.exists() and out_md.stat().st_size > 0
