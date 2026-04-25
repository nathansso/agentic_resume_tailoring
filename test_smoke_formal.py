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
import database.user_utils as user_utils_module
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

    profile_file = tmp_path / "active_profile_id"

    # Point all modules used by these tests at the isolated DB.
    monkeypatch.setattr(db_module, "engine", engine)
    monkeypatch.setattr(chat_module, "engine", engine)
    monkeypatch.setattr(tui_module, "engine", engine)
    monkeypatch.setattr(kg_builder_module, "engine", engine)
    monkeypatch.setattr(services_module, "engine", engine)
    monkeypatch.setattr(user_utils_module, "engine", engine)
    monkeypatch.setattr(user_utils_module, "ACTIVE_PROFILE_FILE", profile_file)
    monkeypatch.setattr(user_utils_module, "ART_DIR", tmp_path)
    monkeypatch.setattr(tui_module, "init_db", lambda: None)

    # Expose profile_file on the engine object so helpers can write to it.
    engine._test_profile_file = profile_file
    return engine


def _seed_user_and_skill(engine):
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

    # Write the active profile pointer so get_active_profile() resolves the test user.
    if hasattr(engine, "_test_profile_file"):
        engine._test_profile_file.write_text(str(uid))


def test_chat_semantic_routing_uses_tool_and_is_fast(isolated_engine, monkeypatch):
    """Fuzzy skill queries now reach the LLM (fast-path removed); LLM resolves via TOOL_CALL."""
    _seed_user_and_skill(isolated_engine)

    class FakeLLM:
        def invoke(self, *_args, **_kwargs):
            class Resp:
                content = "TOOL_CALL: query_skills_vs_jobs()"
            return Resp()

    monkeypatch.setattr(chat_module, "get_llm", lambda role="chat", temperature=0.2: FakeLLM())

    agent = chat_module.ChatAgent()
    response = agent.chat("could you please shwo me all my skils")

    # LLM is called and its TOOL_CALL is resolved — no jobs seeded so guidance message returned.
    assert response  # non-empty
    assert "tailor" in response.lower() or "no jobs" in response.lower() or "skills" in response.lower()


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
    """Skills tree and exp/proj tables show placeholder content when DB is empty."""
    async def _run():
        app = tui_module.ArtApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            from textual.widgets import DataTable, Tree

            skills_tree = app.query_one("#skills-tree", Tree)
            exp_table = app.query_one("#exp-table", DataTable)
            proj_table = app.query_one("#proj-table", DataTable)

            # Skills tree should have a placeholder leaf under root
            root_children = list(skills_tree.root.children)
            assert len(root_children) >= 1
            leaf_label = str(root_children[0].label)
            assert "ingest" in leaf_label.lower() or "no skill" in leaf_label.lower()

            assert exp_table.row_count == 1
            assert proj_table.row_count == 1

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


def test_short_message_now_reaches_llm(isolated_engine, monkeypatch):
    """Short/unrecognized messages pass through to the LLM now that fast-path guard is removed."""
    llm_called = []

    class TrackingLLM:
        def invoke(self, *_args, **_kwargs):
            llm_called.append(True)
            class Resp:
                content = "I can help with that."
            return Resp()

    monkeypatch.setattr(chat_module, "get_llm", lambda role="chat", temperature=0.2: TrackingLLM())

    agent = chat_module.ChatAgent()
    response = agent.chat("hmm")

    assert llm_called, "Short unrecognized messages should now reach the LLM"
    assert response == "I can help with that."


def test_short_message_passes_through_when_bot_asked_question(isolated_engine, monkeypatch):
    """Short reply after the bot asked a question goes to the LLM (not fast-path)."""
    llm_called = []

    class TrackingLLM:
        def invoke(self, *_args, **_kwargs):
            llm_called.append(True)
            class Resp:
                content = "Got it."
            return Resp()

    monkeypatch.setattr(chat_module, "get_llm", lambda role="chat", temperature=0.2: TrackingLLM())

    agent = chat_module.ChatAgent()
    # Seed history so the last assistant turn ends with a question mark.
    agent.history.append({"role": "user", "content": "ingest my github"})
    agent.history.append({"role": "assistant", "content": "What is your GitHub username?"})

    agent.chat("nathansso")

    assert llm_called, "LLM should be called when the bot previously asked a question"


def test_ingest_keyword_returns_numbered_options(isolated_engine, monkeypatch):
    """Typing 'ingest' alone returns numbered ingestion choices, not generic help."""
    class ShouldNotBeCalledLLM:
        def invoke(self, *_a, **_kw):
            raise AssertionError("LLM must not be called for 'ingest'")

    monkeypatch.setattr(chat_module, "get_llm", lambda role="chat", temperature=0.0: ShouldNotBeCalledLLM())

    agent = chat_module.ChatAgent()
    response = agent.chat("ingest")

    assert "1" in response and "2" in response and "3" in response
    assert "github" in response.lower()
    assert "resume" in response.lower()
    assert "linkedin" in response.lower()
    # Pending options should be populated for the three choices.
    assert agent._pending_options, "pending_options should be set after offering choices"


def test_pending_option_resolved_by_digit_reply(isolated_engine, monkeypatch):
    """Replying '1' after numbered options are presented resolves the option without LLM."""
    import tui.services as svc
    monkeypatch.setattr(svc, "ingest_github", lambda username="": f"GitHub ingested for {username}")

    class ShouldNotBeCalledLLM:
        def invoke(self, *_a, **_kw):
            raise AssertionError("LLM must not be called when resolving a pending option")

    monkeypatch.setattr(chat_module, "get_llm", lambda role="chat", temperature=0.0: ShouldNotBeCalledLLM())

    from database.user_utils import create_profile
    user = create_profile("Test User", "testuser@local")
    # Set a github_username so option 1 triggers the ingestion.
    from database.db import engine as db_engine
    from sqlmodel import Session as S
    with S(db_engine) as sess:
        u = sess.get(type(user), user.user_id)
        u.github_username = "myghuser"
        sess.add(u)
        sess.commit()

    agent = chat_module.ChatAgent()
    # First message presents the options.
    agent.chat("ingest github")
    assert agent._pending_options, "pending_options should be set after 'ingest github'"
    # Second message picks option 1.
    response = agent.chat("1")
    assert "myghuser" in response or "GitHub ingested" in response


def test_ingest_token_combo_routes_to_github(isolated_engine, monkeypatch):
    """'i want to ingest skill from my github' routes to GitHub ingestion, not query_skills."""
    class ShouldNotBeCalledLLM:
        def invoke(self, *_a, **_kw):
            raise AssertionError("LLM must not be called for ingest+github token combo")

    monkeypatch.setattr(chat_module, "get_llm", lambda role="chat", temperature=0.0: ShouldNotBeCalledLLM())

    agent = chat_module.ChatAgent()
    response = agent.chat("i want to ingest skill from my github")

    assert "github" in response.lower()
    assert "ingest github" in response.lower() or "username" in response.lower()
    assert "Your skills" not in response


def test_ingest_token_combo_routes_to_resume(isolated_engine, monkeypatch):
    """'can you fetch my resume' routes to resume ingestion instructions without LLM."""
    class ShouldNotBeCalledLLM:
        def invoke(self, *_a, **_kw):
            raise AssertionError("LLM must not be called for ingest+resume token combo")

    monkeypatch.setattr(chat_module, "get_llm", lambda role="chat", temperature=0.0: ShouldNotBeCalledLLM())

    agent = chat_module.ChatAgent()
    response = agent.chat("can you fetch my resume and add it")

    assert "ingest resume" in response.lower()
    assert "path" in response.lower() or "file" in response.lower()


def test_ingest_token_combo_routes_to_linkedin(isolated_engine, monkeypatch):
    """'load my linkedin data' routes to LinkedIn ingestion instructions without LLM."""
    class ShouldNotBeCalledLLM:
        def invoke(self, *_a, **_kw):
            raise AssertionError("LLM must not be called for ingest+linkedin token combo")

    monkeypatch.setattr(chat_module, "get_llm", lambda role="chat", temperature=0.0: ShouldNotBeCalledLLM())

    agent = chat_module.ChatAgent()
    response = agent.chat("load my linkedin data")

    assert "linkedin" in response.lower()
    assert "ingest linkedin" in response.lower() or "pdf" in response.lower()


def test_query_skills_vs_jobs_no_jobs(isolated_engine):
    """query_skills_vs_jobs returns helpful guidance when no jobs are saved."""
    from database.user_utils import create_profile
    create_profile("Test User", "test@local")

    result = chat_module.query_skills_vs_jobs()
    assert "no jobs" in result.lower() or "tailor" in result.lower()


def test_query_skills_vs_jobs_with_job_result(isolated_engine):
    """query_skills_vs_jobs shows match score and skill breakdown when results exist."""
    import uuid
    from datetime import datetime
    from database.user_utils import create_profile
    from database.models import JobDescription, UserJobResult
    from database.db import engine as db_engine
    from sqlmodel import Session as S

    user = create_profile("Match User", "matchuser@local")
    job_id = uuid.uuid4()
    with S(db_engine) as sess:
        sess.add(JobDescription(
            job_id=job_id, title="ML Engineer", company="Acme",
            description="Build models.", created_at=datetime.utcnow(),
        ))
        sess.add(UserJobResult(
            result_id=uuid.uuid4(), user_id=user.user_id, job_id=job_id,
            ats_score=78.5,
            matched_skills={"Python": 1, "PyTorch": 1},
            missing_skills=["Go", "Kubernetes"],
            created_at=datetime.utcnow(),
        ))
        sess.commit()

    result = chat_module.query_skills_vs_jobs()
    assert "ML Engineer" in result
    assert "78%" in result or "78" in result
    assert "Python" in result
    assert "Go" in result


def test_ingestion_diff_shows_new_skills(isolated_engine):
    """ingest_resume_file diff lists only skills not previously on the profile."""
    import uuid
    from database.user_utils import create_profile
    from database.models import Skill, UserSkill
    from database.db import engine as db_engine
    from sqlmodel import Session as S

    user = create_profile("Diff User", "diffuser@local")

    # Pre-seed one skill so it shows as existing, not new
    with S(db_engine) as sess:
        existing_skill = Skill(name="Python", category="language")
        sess.add(existing_skill)
        sess.commit()
        sess.refresh(existing_skill)
        sess.add(UserSkill(
            user_id=user.user_id,
            skill_id=existing_skill.skill_id,
            evidence_source="resume",
            confidence_score=0.9,
        ))
        sess.commit()

    # Simulate ingestion adding a new skill by directly inserting after snapshot
    pre = services_module._snapshot_user_data(user.user_id)

    with S(db_engine) as sess:
        new_skill = Skill(name="Rust", category="language")
        sess.add(new_skill)
        sess.commit()
        sess.refresh(new_skill)
        sess.add(UserSkill(
            user_id=user.user_id,
            skill_id=new_skill.skill_id,
            evidence_source="resume",
            confidence_score=0.8,
        ))
        sess.commit()

    result = services_module._format_ingestion_diff(
        user.user_id, pre[0], pre[1], pre[2], "test_resume.pdf"
    )

    assert "Rust" in result
    assert "Python" not in result.split("New skills")[1].split("\n")[0]
    assert "New skills (1)" in result
    assert "New experiences (0)" in result


def test_ingestion_diff_no_new_content(isolated_engine):
    """When nothing new is added, diff reports zero changes."""
    from database.user_utils import create_profile

    user = create_profile("Same User", "sameuser@local")
    pre = services_module._snapshot_user_data(user.user_id)
    result = services_module._format_ingestion_diff(
        user.user_id, pre[0], pre[1], pre[2], "repeat_resume.pdf"
    )

    assert "New skills (0)" in result
    assert "already on your profile" in result


def test_suppress_output_restores_streams():
    """_suppress_output context manager restores sys.stdout/stderr after exiting."""
    import sys
    original_stdout = sys.stdout
    original_stderr = sys.stderr

    with services_module._suppress_output():
        assert sys.stdout is not original_stdout
        assert sys.stderr is not original_stderr

    assert sys.stdout is original_stdout
    assert sys.stderr is original_stderr


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


def test_chat_ingest_github_no_username_returns_prompt(isolated_engine, monkeypatch):
    """agent.chat('ingest github') with no username returns a prompt, not a service call."""
    class ShouldNotBeCalledLLM:
        def invoke(self, *_a, **_kw):
            raise AssertionError("LLM must not be called for ingest github")

    monkeypatch.setattr(chat_module, "get_llm", lambda role="chat", temperature=0.0: ShouldNotBeCalledLLM())

    agent = chat_module.ChatAgent()
    response = agent.chat("ingest github")
    assert "username" in response.lower()
    assert "ingest github" in response.lower()


def test_chat_ingest_github_with_username_calls_service(isolated_engine, monkeypatch):
    """agent.chat('ingest github <user>') calls the service without the LLM."""
    import tui.services as svc
    monkeypatch.setattr(svc, "ingest_github", lambda username="": f"GitHub ingested for {username}")

    class ShouldNotBeCalledLLM:
        def invoke(self, *_a, **_kw):
            raise AssertionError("LLM must not be called for ingest github <user>")

    monkeypatch.setattr(chat_module, "get_llm", lambda role="chat", temperature=0.0: ShouldNotBeCalledLLM())

    agent = chat_module.ChatAgent()
    response = agent.chat("ingest github nathansso")
    assert "nathansso" in response


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


def test_get_active_profile_returns_none_on_empty_db(isolated_engine):
    """get_active_profile returns None when no profile file or DB record exists."""
    result = user_utils_module.get_active_profile()
    assert result is None


def test_create_profile_persists_and_loads(isolated_engine):
    """create_profile saves to DB and get_active_profile reloads it."""
    user = user_utils_module.create_profile("Alice", "alice@test.com", github_username="alicecodes")
    assert user is not None
    assert user.name == "Alice"
    assert isolated_engine._test_profile_file.exists()

    loaded = user_utils_module.get_active_profile()
    assert loaded is not None
    assert loaded.user_id == user.user_id
    assert loaded.name == "Alice"


def test_onboarding_screen_mounts(isolated_engine):
    """OnboardingScreen composes without error inside a minimal App."""
    from textual.app import App, ComposeResult
    from tui.screens.onboarding import OnboardingScreen

    class _TestApp(App):
        def on_mount(self) -> None:
            self.push_screen(OnboardingScreen())

    async def _run():
        async with _TestApp().run_test() as pilot:
            await pilot.pause()

    asyncio.run(_run())


def test_graph_summary_returns_structure(isolated_engine, monkeypatch):
    """get_graph_summary returns a dict with top_skills, by_category, evidence keys."""
    import tui.services as svc
    monkeypatch.setattr(svc, "engine", isolated_engine)

    result = svc.get_graph_summary(None)
    assert "top_skills" in result
    assert "by_category" in result
    assert "evidence" in result

    # Seed a user and check with a real user_id (graph will be empty but keys must exist)
    from database.models import User
    with Session(isolated_engine) as session:
        user = User(name="Bob", email="bob@test.com")
        session.add(user)
        session.commit()
        session.refresh(user)
        uid = user.user_id

    result2 = svc.get_graph_summary(uid)
    assert isinstance(result2["top_skills"], list)
    assert isinstance(result2["by_category"], dict)
    assert isinstance(result2["evidence"], dict)


# ── Onboarding step flow ────────────────────────────────────────────────────

def test_onboarding_name_required_blocks_advance(isolated_engine):
    """Leaving name blank keeps the screen on step 1."""
    from tui.screens.onboarding import OnboardingScreen
    from textual.app import App

    class _App(App):
        def on_mount(self):
            self.push_screen(OnboardingScreen())

    async def _run():
        async with _App().run_test() as pilot:
            await pilot.pause()
            screen = pilot.app.screen
            assert screen._step_index == 0
            screen._advance(skip=False)   # empty name
            await pilot.pause()
            assert screen._step_index == 0   # still on step 0
            status = str(screen.query_one("#onboarding-status")._Static__content)
            assert "required" in status.lower() or "name" in status.lower()

    asyncio.run(_run())


def test_onboarding_advance_past_name(isolated_engine):
    """A valid name advances to step 2 (resume)."""
    from tui.screens.onboarding import OnboardingScreen
    from textual.app import App

    class _App(App):
        def on_mount(self):
            self.push_screen(OnboardingScreen())

    async def _run():
        async with _App().run_test() as pilot:
            await pilot.pause()
            screen = pilot.app.screen
            screen.query_one("#step-input").value = "Alice Smith"
            screen._advance(skip=False)
            await pilot.pause()
            assert screen._step_index == 1   # moved to resume step
            assert screen._answers["name"] == "Alice Smith"

    asyncio.run(_run())


def test_onboarding_skip_optional_steps(isolated_engine, tmp_path):
    """GitHub and LinkedIn steps can be skipped; skipped values are empty strings."""
    from tui.screens.onboarding import OnboardingScreen
    from textual.app import App

    resume_file = tmp_path / "resume.md"
    resume_file.write_text("# Resume")

    class _App(App):
        def on_mount(self):
            self.push_screen(OnboardingScreen())

    async def _run():
        async with _App().run_test() as pilot:
            await pilot.pause()
            screen = pilot.app.screen
            # Step 0: name
            screen.query_one("#step-input").value = "Alice Smith"
            screen._advance(skip=False)
            await pilot.pause()
            # Step 1: resume
            screen.query_one("#step-input").value = str(resume_file)
            screen._advance(skip=False)
            await pilot.pause()
            # Step 2: github — skip
            assert screen._step_index == 2
            screen._advance(skip=True)
            await pilot.pause()
            assert screen._answers.get("github", "") == ""
            # Step 3: linkedin — skip
            assert screen._step_index == 3
            screen._advance(skip=True)
            await pilot.pause()
            assert screen._answers.get("linkedin", "") == ""

    asyncio.run(_run())


# ── Slash command routing ────────────────────────────────────────────────────

def test_slash_command_unknown_shows_error(isolated_engine):
    """An unrecognised /foo command posts an error into the chat scroll."""
    from textual.widgets import Static

    async def _run():
        app = tui_module.ArtApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            app._handle_chat_input("/notacommand")
            await pilot.pause()
            from textual.containers import VerticalScroll
            scroll = app.query_one("#chat-scroll", VerticalScroll)
            texts = [str(w._Static__content) for w in scroll.query(Static)]
            assert any("unknown" in t.lower() or "available" in t.lower() for t in texts)

    asyncio.run(_run())


def test_slash_commands_do_not_reach_agent(isolated_engine, monkeypatch):
    """/ingest and /data are handled locally — the chat agent is never called."""
    calls = []

    class _FakeAgent:
        def chat(self, msg):
            calls.append(msg)
            return "agent response"

    async def _run():
        app = tui_module.ArtApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.chat_agent = _FakeAgent()
            app._handle_chat_input("/ingest")
            app._handle_chat_input("/data")
            await pilot.pause()
            assert calls == [], f"Agent was called unexpectedly: {calls}"

    asyncio.run(_run())


# ── Profile service functions ────────────────────────────────────────────────

def test_get_profile_data_returns_none_without_profile(isolated_engine):
    """get_profile_data returns None when no active profile exists."""
    result = services_module.get_profile_data()
    assert result is None


def test_get_profile_data_returns_structure(isolated_engine):
    """get_profile_data returns a dict with expected keys when profile exists."""
    _seed_user_and_skill(isolated_engine)
    result = services_module.get_profile_data()
    assert result is not None
    for key in ("user_id", "name", "github_username", "linkedin_url",
                "skills", "experiences", "projects", "sources"):
        assert key in result, f"Missing key: {key}"
    assert result["name"] == "Test User"
    assert result["skills"] >= 1


def test_update_profile_persists_changes(isolated_engine):
    """update_profile writes new name/github/linkedin back to the DB."""
    from database.models import User
    _seed_user_and_skill(isolated_engine)
    data = services_module.get_profile_data()
    assert data is not None

    msg = services_module.update_profile(
        data["user_id"], "Updated Name", "newgithub", "https://linkedin.com/in/test"
    )
    assert "updated" in msg.lower()

    updated = services_module.get_profile_data()
    assert updated["name"] == "Updated Name"
    assert updated["github_username"] == "newgithub"
    assert updated["linkedin_url"] == "https://linkedin.com/in/test"


# ── ProfileScreen ────────────────────────────────────────────────────────────

def test_profile_screen_mounts(isolated_engine):
    """ProfileScreen composes and mounts without error."""
    from tui.screens.profile import ProfileScreen
    from textual.app import App

    class _App(App):
        def on_mount(self):
            self.push_screen(ProfileScreen())

    async def _run():
        async with _App().run_test() as pilot:
            await pilot.pause()

    asyncio.run(_run())


def test_profile_screen_loads_profile_data(isolated_engine):
    """ProfileScreen populates the name input from the active profile."""
    from tui.screens.profile import ProfileScreen
    from textual.app import App

    _seed_user_and_skill(isolated_engine)

    class _App(App):
        def on_mount(self):
            self.push_screen(ProfileScreen())

    async def _run():
        async with _App().run_test() as pilot:
            await pilot.pause()
            screen = pilot.app.screen
            name_val = screen.query_one("#profile-name-input").value
            assert name_val == "Test User"

    asyncio.run(_run())


# ── ctrl+c binding ───────────────────────────────────────────────────────────

def test_ctrl_c_bound_to_noop():
    """ctrl+c must be bound to noop so it doesn't quit the app on copy attempts."""
    keys = {b.key: b.action for b in tui_module.ArtApp.BINDINGS}
    assert "ctrl+c" in keys, "ctrl+c missing from BINDINGS"
    assert keys["ctrl+c"] == "noop", f"ctrl+c bound to {keys['ctrl+c']!r} instead of 'noop'"


def test_slash_copy_posts_result_to_chat(isolated_engine, monkeypatch):
    """/copy calls _copy_chat_to_clipboard and posts a result message without reaching the agent."""
    copied = []

    async def _run():
        app = tui_module.ArtApp()
        monkeypatch.setattr(app.__class__, "_copy_chat_to_clipboard",
                            lambda self: copied.append("called"))
        async with app.run_test() as pilot:
            await pilot.pause()
            app._handle_chat_input("/copy")
            await pilot.pause()
            assert copied == ["called"], "_copy_chat_to_clipboard was not called"

    asyncio.run(_run())


# ── PRD 06.1 — router prompt hardening and repo-scoped ingestion ─────────────

def test_build_router_prompt_contains_state(isolated_engine):
    """build_router_prompt injects runtime state into the system prompt."""
    prompt = chat_module.build_router_prompt(
        has_profile=True,
        profile_name="Alice",
        github_username="alicecodes",
        waiting_for_clarification=False,
    )
    assert "Role" in prompt
    assert "Current state" in prompt
    assert "Allowed actions" in prompt
    assert "Alice" in prompt
    assert "alicecodes" in prompt
    assert "TOOL_CALL:" in prompt
    assert "CLARIFY:" in prompt
    assert "RESPONSE:" in prompt
    assert "run_ingest_github_repo" in prompt

    # No-profile branch
    prompt_no_profile = chat_module.build_router_prompt(has_profile=False)
    assert "none" in prompt_no_profile.lower()


def test_malformed_router_output_falls_back_safely(isolated_engine, monkeypatch):
    """LLM returning gibberish is treated as plain text without raising."""
    class GibberishLLM:
        def invoke(self, *_a, **_kw):
            class Resp:
                content = "I dunno lol just do stuff maybe??"
            return Resp()

    monkeypatch.setattr(chat_module, "get_llm", lambda role="chat", temperature=0.2: GibberishLLM())

    agent = chat_module.ChatAgent()
    response = agent.chat("what is the meaning of life")
    # Must not raise; malformed output is returned as-is.
    assert response == "I dunno lol just do stuff maybe??"


def test_ingest_repo_owner_repo_fast_path(isolated_engine, monkeypatch):
    """agent.chat('ingest github repo owner/repo') calls the repo service without the LLM."""
    import tui.services as svc
    monkeypatch.setattr(svc, "ingest_github_repo", lambda ref: f"Single repo ingested: {ref}")

    class ShouldNotBeCalledLLM:
        def invoke(self, *_a, **_kw):
            raise AssertionError("LLM must not be called for repo fast-path")

    monkeypatch.setattr(chat_module, "get_llm", lambda role="chat", temperature=0.0: ShouldNotBeCalledLLM())

    agent = chat_module.ChatAgent()
    response = agent.chat("ingest github repo openai/evals")
    assert "openai/evals" in response
    assert "LLM" not in response


def test_ingest_github_url_fast_path(isolated_engine, monkeypatch):
    """agent.chat('ingest https://github.com/owner/repo') routes to the repo service without LLM."""
    import tui.services as svc
    monkeypatch.setattr(svc, "ingest_github_repo", lambda ref: f"Single repo ingested: {ref}")

    class ShouldNotBeCalledLLM:
        def invoke(self, *_a, **_kw):
            raise AssertionError("LLM must not be called for GitHub URL fast-path")

    monkeypatch.setattr(chat_module, "get_llm", lambda role="chat", temperature=0.0: ShouldNotBeCalledLLM())

    agent = chat_module.ChatAgent()
    response = agent.chat("ingest https://github.com/openai/evals")
    assert "openai" in response or "evals" in response
    assert "LLM" not in response


def test_ingest_new_github_repo_returns_clarification(isolated_engine, monkeypatch):
    """'ingest a new github repo' returns a repo-specific clarification, not account-wide ingestion."""
    class ShouldNotBeCalledLLM:
        def invoke(self, *_a, **_kw):
            raise AssertionError("LLM must not be called for repo clarification fast-path")

    monkeypatch.setattr(chat_module, "get_llm", lambda role="chat", temperature=0.0: ShouldNotBeCalledLLM())

    agent = chat_module.ChatAgent()
    response = agent.chat("ingest a new github repo")

    # Must ask for a repo ref, not trigger account-level ingestion prompt.
    assert "owner/repo" in response.lower() or "github url" in response.lower() or "provide" in response.lower()
    # Must NOT look like an account-level ingestion prompt.
    assert "ingest github <username>" not in response


def test_ingest_github_repo_invalid_ref(isolated_engine):
    """services.ingest_github_repo with an invalid ref returns an error string and does not raise."""
    result = services_module.ingest_github_repo("not-a-valid-ref")
    assert isinstance(result, str)
    assert "invalid" in result.lower() or "error" in result.lower()
    assert "not-a-valid-ref" in result


def test_ingest_github_repo_summary_mentions_single_repo(isolated_engine, monkeypatch):
    """ingest_github_repo summary clearly says 'single repo', not account-level ingestion."""
    import ingestion.github as gh_module
    import agents.parser as parser_module
    from database.user_utils import create_profile

    create_profile("Test User", "test@local")

    fake_repo = {
        "name": "evals",
        "description": "Evals for LLMs",
        "url": "https://github.com/openai/evals",
        "stars": 10,
        "updated_at": "2024-01-01T00:00:00Z",
        "languages": ["Python"],
        "readme": "# evals",
        "dependencies": {},
        "owner": "openai",
    }
    monkeypatch.setattr(gh_module.GitHubIngestor, "fetch_repo", lambda owner, repo_name, token="": fake_repo)
    monkeypatch.setattr(parser_module.ResumeParserAgent, "parse_and_save", lambda self, data: None)

    result = services_module.ingest_github_repo("openai/evals")
    assert "single repo" in result.lower()
    assert "openai" in result
    assert "evals" in result


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
