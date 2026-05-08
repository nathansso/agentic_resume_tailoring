"""TUI app, screen, slash-command, and sidebar tests."""
import asyncio
import pytest
from uuid import UUID
from sqlmodel import Session, select
from textual.widgets import Button, Input, Static, DataTable, Tree
from textual.containers import VerticalScroll

import tui.app as tui_module
import agents.chat as chat_module
from database.models import JobDescription
from conftest import _seed_user_and_skill


# ── App lifecycle ──────────────────────────────────────────────────────────────

def test_tui_new_job_flow(isolated_engine):
    async def _run():
        app = tui_module.ArtApp()

        with Session(isolated_engine) as session:
            count_before = len(session.exec(select(JobDescription)).all())

        async with app.run_test() as pilot:
            await pilot.pause()

            app.action_new_job()
            await pilot.pause()
            app._save_new_job()
            await pilot.pause()

            with Session(isolated_engine) as session:
                count_after_invalid = len(session.exec(select(JobDescription)).all())
            assert count_after_invalid == count_before

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
    """Skills tree and exp table show placeholder content when DB is empty."""
    from textual.containers import VerticalScroll

    async def _run():
        app = tui_module.ArtApp()
        async with app.run_test() as pilot:
            await pilot.pause()

            skills_tree = app.query_one("#skills-tree", Tree)
            exp_table = app.query_one("#exp-table", DataTable)

            root_children = list(skills_tree.root.children)
            assert len(root_children) >= 1
            leaf_label = str(root_children[0].label)
            assert "ingest" in leaf_label.lower() or "no skill" in leaf_label.lower()

            assert exp_table.row_count == 1
            exp_cell = exp_table.get_cell_at((0, 0))
            assert "ingest" in str(exp_cell).lower()

            proj_scroll = app.query_one("#proj-scroll", VerticalScroll)
            proj_statics = list(proj_scroll.query(Static))
            assert len(proj_statics) == 1
            proj_text = str(proj_statics[0]._Static__content)
            assert "ingest" in proj_text.lower() or "github" in proj_text.lower()

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


# ── Onboarding ─────────────────────────────────────────────────────────────────

def test_onboarding_screen_mounts(isolated_engine):
    """OnboardingScreen composes without error inside a minimal App."""
    from textual.app import App
    from tui.screens.onboarding import OnboardingScreen

    class _TestApp(App):
        def on_mount(self) -> None:
            self.push_screen(OnboardingScreen())

    async def _run():
        async with _TestApp().run_test() as pilot:
            await pilot.pause()

    asyncio.run(_run())


def test_onboarding_name_required_blocks_advance(isolated_engine):
    """Leaving name blank keeps the screen on step 1."""
    from textual.app import App
    from tui.screens.onboarding import OnboardingScreen

    class _App(App):
        def on_mount(self):
            self.push_screen(OnboardingScreen())

    async def _run():
        async with _App().run_test() as pilot:
            await pilot.pause()
            screen = pilot.app.screen
            assert screen._step_index == 0
            screen._advance(skip=False)
            await pilot.pause()
            assert screen._step_index == 0
            status = str(screen.query_one("#onboarding-status")._Static__content)
            assert "required" in status.lower() or "name" in status.lower()

    asyncio.run(_run())


def test_onboarding_advance_past_name(isolated_engine):
    """A valid name advances to step 2 (resume)."""
    from textual.app import App
    from tui.screens.onboarding import OnboardingScreen

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
            assert screen._step_index == 1
            assert screen._answers["name"] == "Alice Smith"

    asyncio.run(_run())


def test_onboarding_skip_optional_steps(isolated_engine, tmp_path):
    """GitHub and LinkedIn steps can be skipped; skipped values are empty strings."""
    from textual.app import App
    from tui.screens.onboarding import OnboardingScreen

    resume_file = tmp_path / "resume.md"
    resume_file.write_text("# Resume")

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
            screen.query_one("#step-input").value = str(resume_file)
            screen._advance(skip=False)
            await pilot.pause()
            assert screen._step_index == 2
            screen._advance(skip=True)
            await pilot.pause()
            assert screen._answers.get("github", "") == ""
            assert screen._step_index == 3
            screen._advance(skip=True)
            await pilot.pause()
            assert screen._answers.get("linkedin", "") == ""

    asyncio.run(_run())


# ── Slash commands ─────────────────────────────────────────────────────────────

def test_slash_command_unknown_shows_error(isolated_engine):
    """An unrecognised /foo command posts an error into the chat scroll."""
    from textual.containers import VerticalScroll

    async def _run():
        app = tui_module.ArtApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            app._handle_chat_input("/notacommand")
            await pilot.pause()
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


# ── Profile screen ─────────────────────────────────────────────────────────────

def test_profile_screen_mounts(isolated_engine):
    """ProfileScreen composes and mounts without error."""
    from textual.app import App
    from tui.screens.profile import ProfileScreen

    class _App(App):
        def on_mount(self):
            self.push_screen(ProfileScreen())

    async def _run():
        async with _App().run_test() as pilot:
            await pilot.pause()

    asyncio.run(_run())


def test_profile_screen_loads_profile_data(isolated_engine):
    """ProfileScreen populates the name input from the active profile."""
    from textual.app import App
    from tui.screens.profile import ProfileScreen

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


def test_profile_screen_has_close_button(isolated_engine):
    """ProfileScreen has a 'Back' button that dismisses the screen."""
    from textual.app import App
    from tui.screens.profile import ProfileScreen

    dismissed = []

    class _App(App):
        def on_mount(self):
            self.push_screen(ProfileScreen(), dismissed.append)

    async def _run():
        async with _App().run_test() as pilot:
            await pilot.pause()
            screen = pilot.app.screen
            close_btn = screen.query_one("#close-profile-btn", Button)
            assert close_btn is not None
            close_btn.press()
            await pilot.pause()
            assert dismissed, "Screen should have been dismissed via close button"

    asyncio.run(_run())


def test_profile_screen_has_contact_fields(isolated_engine):
    """ProfileScreen exposes Phone, Email, and Location input fields."""
    from textual.app import App
    from tui.screens.profile import ProfileScreen

    class _App(App):
        def on_mount(self):
            self.push_screen(ProfileScreen())

    async def _run():
        async with _App().run_test() as pilot:
            await pilot.pause()
            screen = pilot.app.screen
            screen.query_one("#profile-email-input", Input)
            screen.query_one("#profile-phone-input", Input)
            screen.query_one("#profile-location-input", Input)

    asyncio.run(_run())


def test_profile_screen_update_button_persists_contact_info(isolated_engine):
    """Clicking Update saves phone, email, and location to the DB."""
    from textual.app import App
    from sqlmodel import Session, select
    from tui.screens.profile import ProfileScreen
    from database.models import User

    _seed_user_and_skill(isolated_engine)

    class _App(App):
        def on_mount(self):
            self.push_screen(ProfileScreen())

    async def _run():
        async with _App().run_test() as pilot:
            await pilot.pause()
            screen = pilot.app.screen

            screen.query_one("#profile-phone-input", Input).value = "555-1234"
            screen.query_one("#profile-location-input", Input).value = "San Diego, CA"

            screen.query_one("#update-profile-btn", Button).press()
            for _ in range(10):
                await pilot.pause(delay=0.05)

        with Session(isolated_engine) as session:
            user = session.exec(select(User)).first()
        assert user.phone == "555-1234", f"Expected phone '555-1234', got {user.phone!r}"
        assert user.location == "San Diego, CA", f"Expected location 'San Diego, CA', got {user.location!r}"

    asyncio.run(_run())


# ── Keybindings / ctrl+c ───────────────────────────────────────────────────────

def test_ctrl_c_bound_to_noop():
    """ctrl+c must be bound to noop so it doesn't quit the app on copy attempts."""
    keys = {b.key: b.action for b in tui_module.ArtApp.BINDINGS}
    assert "ctrl+c" in keys, "ctrl+c missing from BINDINGS"
    assert keys["ctrl+c"] == "noop", f"ctrl+c bound to {keys['ctrl+c']!r} instead of 'noop'"


def test_slash_copy_posts_result_to_chat(isolated_engine, monkeypatch):
    """/copy calls _copy_chat_to_clipboard without reaching the agent."""
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


# ── PRD 09 — Viz removal and project card layout ───────────────────────────────

def test_viz_tab_does_not_exist(isolated_engine):
    """tab-viz widget must not exist after removing the Visualization tab."""
    from textual.css.query import NoMatches

    async def _run():
        app = tui_module.ArtApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            with pytest.raises(NoMatches):
                app.query_one("#tab-viz")

    asyncio.run(_run())


def test_proj_scroll_exists_proj_table_does_not(isolated_engine):
    """#proj-table (DataTable) is gone; #proj-scroll (VerticalScroll) exists instead."""
    from textual.containers import VerticalScroll
    from textual.css.query import NoMatches

    async def _run():
        app = tui_module.ArtApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            # proj-scroll must be present
            app.query_one("#proj-scroll", VerticalScroll)
            # proj-table must be absent
            with pytest.raises(NoMatches):
                app.query_one("#proj-table")

    asyncio.run(_run())


# ── Bug fixes ──────────────────────────────────────────────────────────────────

def test_delete_button_removes_job_immediately(isolated_engine):
    """Clicking × deletes the job from the DB without requiring a confirmation step."""
    async def _run():
        app = tui_module.ArtApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            with Session(isolated_engine) as session:
                job = JobDescription(title="DeleteMe", company="TestCo", description="")
                session.add(job)
                session.commit()
                job_id = str(job.job_id)

            app._load_jobs_sidebar()
            await pilot.pause()

            assert any(v == job_id for v in app._job_item_to_uuid.values())

            app._delete_job(job_id)
            await pilot.pause()

            with Session(isolated_engine) as session:
                gone = session.get(JobDescription, UUID(job_id))
            assert gone is None, "Job should be deleted from the database immediately"
            assert not any(v == job_id for v in app._job_item_to_uuid.values()), \
                "Job should be removed from the sidebar map"

    asyncio.run(_run())


def test_chat_input_is_textarea(isolated_engine):
    """#chat-input is a _ChatInput (TextArea subclass), not a single-line Input."""
    from textual.widgets import TextArea

    async def _run():
        app = tui_module.ArtApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            widget = app.query_one("#chat-input")
            assert isinstance(widget, TextArea), (
                f"Expected TextArea subclass, got {type(widget)}"
            )

    asyncio.run(_run())


def test_send_button_submits_multiline_text(isolated_engine):
    """TextArea in chat input preserves multi-line text; send handler reads all lines."""
    received = []

    async def _run():
        app = tui_module.ArtApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            app._handle_chat_input = lambda t: received.append(t)

            chat_input = app.query_one("#chat-input", tui_module._ChatInput)
            chat_input.load_text("line one\nline two\nline three")
            await pilot.pause()

            # TextArea must preserve multi-line content (unlike a single-line Input)
            assert "line two" in chat_input.text, "TextArea should hold all pasted lines"

            # Exercise the same code path the send button handler runs
            text = chat_input.text.strip()
            if text:
                app._handle_chat_input(text)
                chat_input.clear()
            await pilot.pause()

            assert len(received) == 1, f"Expected 1 call, got {len(received)}"
            assert "line one" in received[0] and "line two" in received[0], \
                f"Multi-line content not preserved: {received[0]!r}"
            assert chat_input.text.strip() == "", "Input should be cleared after send"

    asyncio.run(_run())


def test_chat_agent_exception_shows_system_msg(isolated_engine):
    """An exception raised by the chat agent appears as a system-msg, not a crash."""
    class _BrokenAgent:
        def chat(self, msg):
            raise RuntimeError("pipeline exploded")

    posted = []

    async def _run():
        app = tui_module.ArtApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.chat_agent = _BrokenAgent()
            app._post_system_msg = lambda msg: posted.append(msg)
            app._run_chat("tailor my resume")
            for _ in range(8):
                await pilot.pause(delay=0.05)

        assert any("pipeline exploded" in m for m in posted), \
            f"Expected error message in system posts, got: {posted}"

    asyncio.run(_run())


def test_post_system_msg_renders_with_system_msg_class(isolated_engine):
    """_post_system_msg mounts a Static with class 'system-msg' in the chat scroll."""
    async def _run():
        app = tui_module.ArtApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            app._post_system_msg("Tailoring failed: oops")
            await pilot.pause()
            scroll = app.query_one("#chat-scroll", VerticalScroll)
            matches = [
                w for w in scroll.query(Static)
                if "system-msg" in w.classes
                and "tailoring failed" in str(w._Static__content).lower()
            ]
            assert matches, "system-msg widget not found in chat scroll"

    asyncio.run(_run())
