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
import tui.services as services_module
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
    monkeypatch.setattr(services_module, "engine", engine)
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

    monkeypatch.setattr(chat_module, "get_llm", lambda role="chat", temperature=0.2: ShouldNotBeCalledLLM())

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


def test_empty_state_tables_show_placeholders(isolated_engine):
    """Skills/exp/proj tables show placeholder rows when DB is empty."""
    async def _run():
        app = tui_module.ArtApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            from textual.widgets import DataTable
            skills_table = app.query_one("#skills-table", DataTable)
            exp_table = app.query_one("#exp-table", DataTable)
            proj_table = app.query_one("#proj-table", DataTable)

            assert skills_table.row_count == 1
            assert exp_table.row_count == 1
            assert proj_table.row_count == 1

            skills_cell = skills_table.get_cell_at((0, 0))
            assert "ingest" in str(skills_cell).lower()
            exp_cell = exp_table.get_cell_at((0, 0))
            assert "ingest" in str(exp_cell).lower()
            proj_cell = proj_table.get_cell_at((0, 0))
            assert "ingest" in str(proj_cell).lower() or "github" in str(proj_cell).lower()

    asyncio.run(_run())


def test_refresh_app_state_empty_and_with_skills(isolated_engine):
    """_refresh_app_state returns 'setup' on empty DB, 'profile_ready' when user has skills."""
    async def _run():
        app = tui_module.ArtApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app._refresh_app_state() == "setup"

            _seed_user_and_skill(isolated_engine)
            assert app._refresh_app_state() == "profile_ready"

    asyncio.run(_run())


def test_status_panel_updates_with_state(isolated_engine):
    """Status bar text matches app state."""
    async def _run():
        app = tui_module.ArtApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            from textual.widgets import Static

            status = app.query_one("#status-bar", Static)

            def get_text():
                return str(status._Static__content)

            app._refresh_app_state()
            text = get_text()
            assert "F1" in text or "ingest" in text.lower()

            _seed_user_and_skill(isolated_engine)
            app._refresh_app_state()
            text = get_text()
            assert "job" in text.lower() or "Ctrl+N" in text

    asyncio.run(_run())


def test_fast_path_help_command(isolated_engine, monkeypatch):
    """agent.chat('help') returns without calling LLM and contains command list."""
    class ShouldNotBeCalledLLM:
        def invoke(self, *_args, **_kwargs):
            raise AssertionError("LLM must not be called for 'help'")

    monkeypatch.setattr(chat_module, "get_llm", lambda role="chat", temperature=0.0: ShouldNotBeCalledLLM())

    agent = chat_module.ChatAgent()
    response = agent.chat("help")

    assert "skills" in response.lower()
    assert "projects" in response.lower()
    assert "ingest" in response.lower() or "F1" in response


def test_fast_path_short_unrecognized(isolated_engine, monkeypatch):
    """agent.chat('hmm') returns clarification without calling the LLM."""
    class ShouldNotBeCalledLLM:
        def invoke(self, *_args, **_kwargs):
            raise AssertionError("LLM must not be called for short unrecognized input")

    monkeypatch.setattr(chat_module, "get_llm", lambda role="chat", temperature=0.0: ShouldNotBeCalledLLM())

    agent = chat_module.ChatAgent()
    response = agent.chat("hmm")

    assert len(response) > 0
    assert "?" in response or "not sure" in response.lower() or "try" in response.lower()


def test_get_llm_roles(monkeypatch):
    """get_llm returns a BaseChatModel for each role without error (anthropic + openai)."""
    import llm as llm_module
    from langchain_core.language_models.chat_models import BaseChatModel

    class FakeModel(BaseChatModel):
        def _generate(self, *a, **kw): pass
        @property
        def _llm_type(self): return "fake"

    def fake_model(**kwargs):
        return FakeModel()

    import langchain_anthropic, langchain_openai
    monkeypatch.setattr(langchain_anthropic, "ChatAnthropic", fake_model)
    monkeypatch.setattr(langchain_openai, "ChatOpenAI", fake_model)

    for provider in ("anthropic", "openai"):
        monkeypatch.setattr(llm_module, "LLM_PROVIDER", provider)
        for role in ("chat", "extract", "tailor"):
            model = llm_module.get_llm(role=role)
            assert isinstance(model, BaseChatModel), f"Expected BaseChatModel for provider={provider} role={role}"


def test_chat_ingest_resume_fast_path(isolated_engine, monkeypatch):
    """agent.chat('ingest resume test.md') calls service without LLM."""
    import tui.services as svc
    monkeypatch.setattr(svc, "ingest_resume_file", lambda path: f"Resume ingested: {path}")

    class ShouldNotBeCalledLLM:
        def invoke(self, *_a, **_kw):
            raise AssertionError("LLM must not be called for ingest resume")

    monkeypatch.setattr(chat_module, "get_llm", lambda role="chat", temperature=0.0: ShouldNotBeCalledLLM())

    agent = chat_module.ChatAgent()
    response = agent.chat("ingest resume my_resume.md")

    assert "my_resume.md" in response
    assert "LLM" not in response


def test_chat_ingest_github_fast_path(isolated_engine, monkeypatch):
    """agent.chat('ingest github') calls service without LLM."""
    import tui.services as svc
    monkeypatch.setattr(svc, "ingest_github", lambda username="": f"GitHub ingested for {username or 'default'}")

    class ShouldNotBeCalledLLM:
        def invoke(self, *_a, **_kw):
            raise AssertionError("LLM must not be called for ingest github")

    monkeypatch.setattr(chat_module, "get_llm", lambda role="chat", temperature=0.0: ShouldNotBeCalledLLM())

    agent = chat_module.ChatAgent()
    response = agent.chat("ingest github")
    assert "github" in response.lower()


def test_chat_tailor_fast_path(isolated_engine, monkeypatch):
    """agent.chat('tailor <job>') calls run_tailor without LLM."""
    monkeypatch.setattr(chat_module, "run_tailor", lambda job: f"Tailored for: {job}")

    class ShouldNotBeCalledLLM:
        def invoke(self, *_a, **_kw):
            raise AssertionError("LLM must not be called for tailor fast-path")

    monkeypatch.setattr(chat_module, "get_llm", lambda role="chat", temperature=0.0: ShouldNotBeCalledLLM())

    agent = chat_module.ChatAgent()
    response = agent.chat("tailor Senior Engineer at Acme Corp")
    assert "Senior Engineer at Acme Corp" in response


def test_ingest_resume_file_missing_path(isolated_engine):
    """ingest_resume_file returns error string for missing file, does not raise."""
    import tui.services as svc
    result = svc.ingest_resume_file("definitely_does_not_exist_12345.md")
    assert "not found" in result.lower() or "error" in result.lower()
    assert isinstance(result, str)


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
