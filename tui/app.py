"""
ART TUI — Hybrid chat-first terminal interface for Agentic Resume Tailoring.

Layout:
  ┌─────────────────────────────────────────────────────┐
  │  Header                                             │
  ├────────────┬────────────────────────────────────────┤
  │  Jobs      │  [Chat] [Data]                         │
  │  sidebar   │                                        │
  │            │  Chat messages / data tables           │
  │            │                                        │
  │  + New Job │  [input box]                           │
  ├────────────┴────────────────────────────────────────┤
  │  Footer (F1=Ingest F2=Data F3=Tailor)               │
  └─────────────────────────────────────────────────────┘

Run: python -m tui.app  OR  python cli.py tui
"""
import logging
from typing import Optional
from uuid import UUID, uuid4

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import (
    Header, Footer, Static, Input, Button, ListView,
    ListItem, Label, TabbedContent, TabPane, DataTable, Tree, TextArea,
)
from textual import work, events

from database.db import init_db, engine
from tui.screens.onboarding import OnboardingScreen
from tui.screens.profile import ProfileScreen, _initials
from database.models import JobDescription, Skill, User, UserJobResult, UserSkill
from sqlmodel import Session, select
from tui import services

logger = logging.getLogger(__name__)


class _ChatInput(TextArea):
    """TextArea that submits on Enter; preserves multi-line paste."""

    async def _on_key(self, event: events.Key) -> None:
        if event.key == "enter":
            event.stop()
            event.prevent_default()
            text = self.text.strip()
            if text:
                self.clear()
                self.app._handle_chat_input(text)
        else:
            await super()._on_key(event)


_WELCOME_MSG = (
    "Welcome to ART! I'm your resume tailoring assistant.\n\n"
    "I can help you:\n"
    " * Ingest your resume, GitHub, or LinkedIn data\n"
    " * View your skills, experiences, and projects\n"
    " * Analyze job descriptions and find skill gaps\n"
    " * Tailor your resume for specific roles\n\n"
    "Slash commands: /ingest  /data  /tailor  /profile  /copy\n"
    "Try: 'show my skills' or /ingest  —  use /copy to copy chat"
)


class AppState:
    SETUP = "setup"
    PROFILE_READY = "profile_ready"
    JOB_SELECTED = "job_selected"
    TAILORING_COMPLETE = "tailoring_complete"


# ───────────────────────────────────────────────────────────
#  Main App
# ───────────────────────────────────────────────────────────

class ArtApp(App):
    """ART — Agentic Resume Tailoring TUI"""

    CSS = """
    Screen {
        background: $surface;
    }

    #main-container {
        height: 1fr;
    }

    /* ── Status Bar ── */
    #status-bar-row {
        height: 1;
        background: $boost;
        padding: 0 0 0 2;
    }
    #status-bar {
        width: 1fr;
        height: 1;
        color: $accent;
        content-align: left middle;
    }
    #avatar-btn {
        width: 6;
        min-width: 6;
        height: 1;
        background: $accent;
        color: $background;
        text-style: bold;
        border: none;
        padding: 0 1;
    }

    /* ── Sidebar ── */
    #sidebar {
        width: 32;
        border-right: solid $primary;
        padding: 1;
    }
    #sidebar-title {
        text-style: bold;
        color: $accent;
        padding-bottom: 1;
    }
    #job-list {
        height: 1fr;
    }
    #new-job-btn {
        width: 100%;
        margin-top: 1;
    }

    /* ── Chat Area ── */
    #chat-area {
        width: 1fr;
    }

    /* Chat Tab */
    #chat-scroll {
        height: 1fr;
        padding: 0 1;
    }
    #chat-input-row {
        height: auto;
        padding: 1;
    }
    #chat-input {
        width: 1fr;
        height: 5;
    }
    #send-btn {
        width: 10;
    }

    .user-msg {
        color: $text;
        background: $primary 15%;
        padding: 0 1;
        margin: 0 0 1 10;
    }
    .bot-msg {
        color: $text;
        background: $surface;
        padding: 0 1;
        margin: 0 10 1 0;
        border-left: thick $accent;
    }
    .system-msg {
        color: $text-muted;
        text-style: italic;
        padding: 0 1;
        margin: 0 0 1 0;
    }

    /* Graph Tree */
    #graph-tree {
        height: 1fr;
        padding: 0 1;
    }

    /* Skills Tree */
    #skills-tree {
        height: 1fr;
        padding: 0 1;
    }

    /* New Job Inputs */
    #job-title-input, #job-company-input {
        margin-bottom: 1;
    }
    #job-input-area {
        height: auto;
        padding: 1;
        display: none;
    }
    #job-input-area.visible {
        display: block;
    }

    /* ── Project cards ── */
    .proj-card {
        padding: 1 2;
        border-bottom: solid $primary 30%;
    }

    /* ── Job list delete button ── */
    .job-item-row {
        height: auto;
        width: 100%;
    }
    .job-label {
        width: 1fr;
    }
    .job-del-btn {
        width: 3;
        min-width: 3;
        height: 1;
        border: none;
        background: transparent;
        color: $error;
        padding: 0;
        margin: 0;
    }

    """

    TITLE = "ART — Agentic Resume Tailoring"
    SUB_TITLE = "Hybrid Chat + Tools"

    BINDINGS = [
        Binding("ctrl+c", "noop", show=False),   # prevent quit on copy attempt
        Binding("ctrl+n", "new_job", "New Job"),
        Binding("ctrl+q", "quit", "Quit"),
    ]

    def __init__(self):
        super().__init__()
        self.chat_agent = None  # Lazy-init to avoid slow import at startup
        self._job_item_to_uuid: dict[str, str] = {}
        self.app_state: str = AppState.SETUP
        self._selected_job_id: Optional[str] = None
        self._selected_job_label: str = ""
        self._job_chat_cache: dict[str, list[tuple[str, str]]] = {}
        self._active_context_key: str = "landing"

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="status-bar-row"):
            yield Static("", id="status-bar")
            yield Button("?", id="avatar-btn")
        with Horizontal(id="main-container"):
            # ── Sidebar: Job List ──
            with Vertical(id="sidebar"):
                yield Static("Jobs", id="sidebar-title")
                yield ListView(id="job-list")
                # Inline add-job form (hidden by default)
                with Vertical(id="job-input-area"):
                    yield Input(placeholder="Job Title", id="job-title-input")
                    yield Input(placeholder="Company", id="job-company-input")
                    yield Horizontal(
                        Button("Save", variant="primary", id="save-job-btn"),
                        Button("Cancel", id="cancel-job-btn"),
                    )
                yield Button("+ New Job", id="new-job-btn", variant="primary")

            # ── Main Content: Tabs ──
            with Vertical(id="chat-area"):
                with TabbedContent(id="chat-tabs"):
                    # ── Chat Tab ──
                    with TabPane("Chat", id="tab-chat"):
                        with VerticalScroll(id="chat-scroll"):
                            yield Static(_WELCOME_MSG, classes="bot-msg")
                        with Horizontal(id="chat-input-row"):
                            yield _ChatInput(id="chat-input")
                            yield Button("Send", variant="primary", id="send-btn")

                    # ── Data Tab ──
                    with TabPane("Data", id="tab-data"):
                        with TabbedContent(id="data-subtabs"):
                            with TabPane("Skills", id="subtab-skills"):
                                yield Tree("Skills", id="skills-tree")
                            with TabPane("Experiences", id="subtab-exp"):
                                yield DataTable(id="exp-table")
                            with TabPane("Projects", id="subtab-proj"):
                                with VerticalScroll(id="proj-scroll"):
                                    pass
                            with TabPane("Graph", id="subtab-graph"):
                                yield Tree("Knowledge Graph", id="graph-tree")

        yield Footer()

    # ───────────────────────────────────────────────────────
    #  Lifecycle
    # ───────────────────────────────────────────────────────

    def on_mount(self) -> None:
        from config import ensure_app_dirs
        ensure_app_dirs()
        init_db()
        self._job_chat_cache["landing"] = [("bot-msg", _WELCOME_MSG)]

        from config_validator import validate_config
        config_errors = validate_config()
        if config_errors:
            for err in config_errors:
                logger.warning("Config error: %s", err)
            try:
                self.query_one("#status-bar", Static).update(
                    f"[CONFIG ERROR] {config_errors[0]}"
                )
            except Exception:
                pass

        self._refresh_app_state()
        self._load_jobs_sidebar()
        self._load_data_tables()
        from database.user_utils import get_active_profile
        if get_active_profile() is None:
            self.push_screen(OnboardingScreen(), callback=self._on_onboarding_done)

    def _on_onboarding_done(self, result: dict | None) -> None:
        self._refresh_app_state()
        self._load_jobs_sidebar()
        self._load_data_tables()
        if not result:
            return
        scroll = self.query_one("#chat-scroll", VerticalScroll)
        scroll.mount(Static(
            f"Welcome, {result.get('name', '')}! Resume ingested successfully.\n\n"
            f"{result.get('ingest_result', '')}",
            classes="bot-msg",
        ))
        github = result.get("github_username", "")
        if github:
            scroll.mount(Static(
                f"GitHub username detected ({github}).\n"
                f"Type `ingest github {github}` to fetch your repos and extract skills/projects.",
                classes="bot-msg",
            ))
        scroll.scroll_end()

    # ───────────────────────────────────────────────────────
    #  Chat
    # ───────────────────────────────────────────────────────

    def action_noop(self) -> None:
        """Absorb ctrl+c so it doesn't quit the app while the user is copying text."""

    def _copy_chat_to_clipboard(self) -> None:
        """Collect visible chat messages and copy them to the Windows clipboard."""
        import re
        import subprocess
        scroll = self.query_one("#chat-scroll", VerticalScroll)
        lines = []
        for widget in scroll.query(Static):
            raw = str(widget._Static__content)
            # Strip Rich markup tags e.g. [bold], [/bold], [dim cyan]…
            text = re.sub(r"\[/?[^\]]*\]", "", raw).strip()
            if not text:
                continue
            classes = widget.classes
            if "user-msg" in classes:
                lines.append(text)          # already prefixed "You: …"
            elif "bot-msg" in classes:
                lines.append(f"ART: {text}")
            # skip system-msg (thinking indicators, etc.)
        if not lines:
            self._post_chat_response("Nothing in chat to copy.")
            return
        content = "\n\n".join(lines)
        try:
            proc = subprocess.Popen(
                "clip", stdin=subprocess.PIPE, shell=True, stderr=subprocess.DEVNULL
            )
            proc.communicate(input=content.encode("utf-16-le"))
            self._post_chat_response(f"Copied {len(lines)} messages to clipboard.")
        except Exception as e:
            self._post_chat_response(f"Copy failed: {e}")

    def _get_agent(self):
        if self.chat_agent is None:
            import os
            from agents.chat import ChatAgent
            trace_sink = None
            if os.environ.get("ART_LOG_CHAT_EVAL") == "1":
                try:
                    from verification.chat_eval.artifacts import make_live_session_sink
                    trace_sink = make_live_session_sink()
                except Exception as exc:
                    logger.warning("Could not create eval trace sink: %s", exc)
            self.chat_agent = ChatAgent(trace_sink=trace_sink)
        return self.chat_agent

    # ───────────────────────────────────────────────────────
    #  App State
    # ───────────────────────────────────────────────────────

    def _refresh_app_state(self) -> str:
        db_state = services.compute_app_state()
        if db_state == AppState.SETUP:
            self.app_state = AppState.SETUP
            self._selected_job_id = None
            self._selected_job_label = ""
        elif self._selected_job_id is not None:
            self.app_state = AppState.JOB_SELECTED
        else:
            self.app_state = db_state
        self._update_status_bar()
        return self.app_state

    def _update_status_bar(self) -> None:
        msgs = {
            AppState.SETUP: "No profile yet -- type /ingest to add your resume",
            AppState.PROFILE_READY: "Profile ready -- select a job or press Ctrl+N to create one",
            AppState.JOB_SELECTED: f"Job selected: {self._selected_job_label} -- type /tailor to tailor",
            AppState.TAILORING_COMPLETE: "Tailoring complete -- view results in the sidebar",
        }
        try:
            self.query_one("#status-bar", Static).update(msgs.get(self.app_state, ""))
        except Exception:
            pass
        try:
            from database.user_utils import get_active_profile
            user = get_active_profile()
            initials = _initials(user.name) if user else "?"
            self.query_one("#avatar-btn", Button).label = initials
        except Exception:
            pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn = event.button.id
        if btn == "send-btn":
            chat_input = self.query_one("#chat-input", _ChatInput)
            if chat_input.text.strip():
                self._handle_chat_input(chat_input.text)
                chat_input.clear()
        elif btn == "avatar-btn":
            self._open_profile()
        elif btn == "new-job-btn":
            self.action_new_job()
        elif btn == "save-job-btn":
            self._save_new_job()
        elif btn == "cancel-job-btn":
            self._hide_job_input()
        elif btn and btn.startswith("del-"):
            item_id = btn[4:]
            job_uuid = self._job_item_to_uuid.get(item_id)
            if job_uuid:
                self._delete_job(job_uuid)

    def _open_profile(self) -> None:
        self.push_screen(ProfileScreen(), callback=self._on_profile_done)

    def _on_profile_done(self, result: dict | None) -> None:
        if result and result.get("name"):
            self._update_status_bar()
            self._refresh_app_state()

    def _handle_chat_input(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        # Slash commands — handled locally, never sent to the chat agent.
        if text.startswith("/"):
            cmd = text[1:].lower().strip()
            if cmd == "ingest":
                self.action_ingest()
            elif cmd == "data":
                self.action_show_data()
            elif cmd == "tailor":
                self.action_tailor()
            elif cmd == "profile":
                self._open_profile()
            elif cmd == "copy":
                self._copy_chat_to_clipboard()
            else:
                scroll = self.query_one("#chat-scroll", VerticalScroll)
                scroll.mount(Static(
                    f"Unknown command: {text}\n"
                    "Available: /ingest  /data  /tailor  /profile  /copy",
                    classes="system-msg",
                ))
                scroll.scroll_end()
            return
        scroll = self.query_one("#chat-scroll", VerticalScroll)
        scroll.mount(Static(f"You: {text}", classes="user-msg"))
        self._job_chat_cache.setdefault(self._active_context_key, []).append(
            ("user-msg", f"You: {text}")
        )
        scroll.mount(Static("Thinking...", classes="system-msg", id="thinking"))
        scroll.scroll_end()
        self._run_chat(text)

    @work(thread=True)
    def _run_chat(self, text: str) -> None:
        """Run chat agent in a background thread so the UI stays responsive."""
        try:
            agent = self._get_agent()
            response = agent.chat(text)
            self.call_from_thread(self._post_chat_response, response)
        except Exception as e:
            logger.exception("_run_chat error")
            try:
                self.call_from_thread(self._post_system_msg, f"Error: {e}")
            except Exception:
                pass

    def _post_system_msg(self, msg: str) -> None:
        """Post an error or system notice to the chat scroll."""
        try:
            scroll = self.query_one("#chat-scroll", VerticalScroll)
            for t in scroll.query("#thinking"):
                t.remove()
            scroll.mount(Static(msg, classes="system-msg"))
            scroll.scroll_end()
        except Exception:
            pass

    def _post_chat_response(self, response: str) -> None:
        scroll = self.query_one("#chat-scroll", VerticalScroll)
        for t in scroll.query("#thinking"):
            t.remove()
        scroll.mount(Static(response, classes="bot-msg"))
        self._job_chat_cache.setdefault(self._active_context_key, []).append(
            ("bot-msg", response)
        )
        scroll.scroll_end()

    # ───────────────────────────────────────────────────────
    #  F-Key Actions
    # ───────────────────────────────────────────────────────

    def action_ingest(self) -> None:
        """F1 — Show ingestion guide in chat."""
        self._switch_to_chat()
        scroll = self.query_one("#chat-scroll", VerticalScroll)
        scroll.mount(Static(
            "What would you like to ingest?\n\n"
            "Type one of:\n"
            " * ingest resume <path>  — Parse a resume file (PDF, DOCX, MD)\n"
            " * ingest github         — Fetch your GitHub repos\n"
            " * ingest github <user>  — Fetch a specific user's repos\n"
            " * ingest linkedin <url> — Scrape LinkedIn (opens browser)",
            classes="bot-msg",
        ))
        scroll.scroll_end()
        self.query_one("#chat-input", _ChatInput).focus()

    def action_show_data(self) -> None:
        """F2 — Switch to data tab and refresh."""
        self._load_data_tables()
        tabs = self.query_one("#chat-tabs", TabbedContent)
        tabs.active = "tab-data"

    def action_tailor(self) -> None:
        """F3 — Show tailoring guide in chat."""
        self._switch_to_chat()
        scroll = self.query_one("#chat-scroll", VerticalScroll)
        scroll.mount(Static(
            "Ready to tailor your resume!\n\n"
            "Select a job from the sidebar, or type:\n"
            " * tailor <job_file>  — Tailor for a job description file\n"
            " * Or paste a job description and I'll analyze it",
            classes="bot-msg",
        ))
        scroll.scroll_end()
        self.query_one("#chat-input", _ChatInput).focus()

    def action_new_job(self) -> None:
        """Ctrl+N — Toggle the inline add-job form."""
        area = self.query_one("#job-input-area")
        area.toggle_class("visible")
        if area.has_class("visible"):
            self.query_one("#job-title-input", Input).focus()

    def _switch_to_chat(self) -> None:
        tabs = self.query_one("#chat-tabs", TabbedContent)
        tabs.active = "tab-chat"

    # ───────────────────────────────────────────────────────
    #  Jobs Sidebar
    # ───────────────────────────────────────────────────────

    def _load_jobs_sidebar(self) -> None:
        job_list = self.query_one("#job-list", ListView)
        job_list.clear()
        self._job_item_to_uuid.clear()
        for job in services.get_jobs():
            # Use a unique widget ID every render to avoid duplicate-ID races
            # when ListView items are reloaded rapidly.
            item_id = f"job-item-{uuid4().hex}"
            self._job_item_to_uuid[item_id] = job["job_id"]
            status = job.get("status", "created")
            status_tag = f" [{status}]" if status != "created" else ""
            job_list.append(ListItem(
                Horizontal(
                    Label(
                        f"{job['title']}\n{job['company']}{job['score']}{status_tag}",
                        classes="job-label",
                    ),
                    Button("×", id=f"del-{item_id}", classes="job-del-btn"),
                    classes="job-item-row",
                ),
                id=item_id,
            ))

    def _save_new_job(self) -> None:
        title = self.query_one("#job-title-input", Input).value.strip()
        company = self.query_one("#job-company-input", Input).value.strip()
        scroll = self.query_one("#chat-scroll", VerticalScroll)
        if not title or not company:
            scroll.mount(Static("Please enter both job title and company.", classes="system-msg"))
            scroll.scroll_end()
            return

        try:
            with Session(engine) as session:
                job = JobDescription(title=title, company=company, description="")
                session.add(job)
                session.commit()
        except Exception as e:
            logger.exception("Failed to save new job")
            scroll.mount(Static(f"Failed to save job: {e}", classes="system-msg"))
            scroll.scroll_end()
            return

        self.query_one("#job-title-input", Input).value = ""
        self.query_one("#job-company-input", Input).value = ""
        self._hide_job_input()
        self._load_jobs_sidebar()
        self._refresh_app_state()
        scroll.mount(Static(f"Job saved: {title} @ {company}", classes="bot-msg"))
        scroll.scroll_end()

    def _hide_job_input(self) -> None:
        self.query_one("#job-input-area").remove_class("visible")

    def _delete_job(self, job_uuid: str) -> None:
        """Delete the job immediately and reload the sidebar."""
        services.delete_job(job_uuid)
        self._job_chat_cache.pop(job_uuid, None)
        for k in [k for k, v in self._job_item_to_uuid.items() if v == job_uuid]:
            del self._job_item_to_uuid[k]
        if self._selected_job_id == job_uuid:
            self._selected_job_id = None
            self._selected_job_label = ""
            self._active_context_key = "landing"
            if self.chat_agent:
                self.chat_agent.set_active_job(None)
            scroll = self.query_one("#chat-scroll", VerticalScroll)
            scroll.remove_children()
            for css_class, text in self._job_chat_cache.get("landing", []):
                scroll.mount(Static(text, classes=css_class))
            scroll.scroll_end()
        self._load_jobs_sidebar()
        self._refresh_app_state()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.list_view.id == "job-list":
            item_id = event.item.id or ""
            job_uuid = self._job_item_to_uuid.get(item_id)
            if not job_uuid:
                return
            try:
                UUID(job_uuid)
            except ValueError:
                scroll = self.query_one("#chat-scroll", VerticalScroll)
                scroll.mount(Static("Selected job has invalid ID.", classes="system-msg"))
                scroll.scroll_end()
                return
            self._show_job_details(job_uuid)

    def _show_job_details(self, job_uuid: str) -> None:
        self._switch_to_chat()
        scroll = self.query_one("#chat-scroll", VerticalScroll)
        self._active_context_key = job_uuid
        scroll.remove_children()

        agent = self._get_agent()

        if job_uuid in self._job_chat_cache:
            # Returning to a previously visited job — replay the cached messages.
            for css_class, text in self._job_chat_cache[job_uuid]:
                scroll.mount(Static(text, classes=css_class))
            agent.set_active_job(job_uuid)
            detail = services.get_job_details(job_uuid)
            if detail:
                self._selected_job_label = f"{detail['title']} @ {detail['company']}"
        else:
            # First visit this session — show the job detail card.
            detail = services.get_job_details(job_uuid)
            if not detail:
                return

            agent.set_active_job(job_uuid)

            # Reconstruct scroll from DB history (e.g. after an app restart).
            db_history = services.load_chat_history(job_uuid)
            if db_history:
                cached = []
                for msg in db_history:
                    if msg["role"] == "user":
                        css, text = "user-msg", f"You: {msg['content']}"
                    else:
                        css, text = "bot-msg", msg["content"]
                    scroll.mount(Static(text, classes=css))
                    cached.append((css, text))
                self._job_chat_cache[job_uuid] = cached

            status = detail.get("status", "created")
            lines = [f"{detail['title']} @ {detail['company']}  [{status}]"]
            if "ats_score" in detail:
                lines.append(f"\nLatest ATS Score: {detail['ats_score']}%")
                if detail.get("matched_skills"):
                    lines.append(f"Matched: {', '.join(detail['matched_skills'])}")
                if detail.get("missing_skills"):
                    lines.append(f"Missing: {', '.join(detail['missing_skills'])}")

            if status == "created":
                lines.append('\nPaste the job description in chat, then type "analyze".')
            elif status == "analyzed":
                lines.append('\nSkills extracted. Type "tailor" to tailor your resume.')
            elif status == "tailored":
                lines.append('\nTailoring complete. Type "export" to save the tailored resume.')
            elif status == "exported":
                lines.append("\nResume exported. Select another job or iterate further.")
            else:
                lines.append("\nNo tailoring results yet. Press F3 to tailor.")

            detail_text = "\n".join(lines)
            scroll.mount(Static(detail_text, classes="bot-msg"))
            self._job_chat_cache.setdefault(job_uuid, []).append(("bot-msg", detail_text))
            self._selected_job_label = f"{detail['title']} @ {detail['company']}"

        scroll.scroll_end()
        self._selected_job_id = job_uuid
        self._refresh_app_state()

    # ───────────────────────────────────────────────────────
    #  Data Tables
    # ───────────────────────────────────────────────────────

    def _load_data_tables(self) -> None:
        self._load_skills_table()
        self._load_exp_table()
        self._load_proj_cards()
        self._load_graph_view()

    def _load_skills_table(self) -> None:
        tree = self.query_one("#skills-tree", Tree)
        tree.clear()
        rows = services.get_skills(services.get_first_user_id())
        if not rows:
            tree.root.set_label("[bold]Skills[/bold]")
            tree.root.add_leaf("[dim]No skills found — type /ingest to add your resume[/dim]")
            tree.root.expand()
            return

        # Group by category
        by_category: dict[str, list] = {}
        for row in rows:
            cat = (row.get("category") or "Uncategorized").strip() or "Uncategorized"
            by_category.setdefault(cat, []).append(row)

        tree.root.set_label(f"[bold]Skills[/bold] — {len(rows)} total")
        for cat in sorted(by_category):
            items = by_category[cat]
            cat_node = tree.root.add(f"[bold cyan]{cat}[/bold cyan] ({len(items)})")
            for row in sorted(items, key=lambda r: r["name"]):
                label = f"[cyan]{row['name']}[/cyan]"
                detail = []
                if row.get("proficiency"):
                    detail.append(f"proficiency: {row['proficiency']}")
                if row.get("confidence"):
                    detail.append(f"confidence: {row['confidence']}")
                if row.get("source"):
                    detail.append(f"source: {row['source']}")
                if detail:
                    label += f"  [dim]{' · '.join(detail)}[/dim]"
                cat_node.add_leaf(label)
            cat_node.expand()
        tree.root.expand()

    def _load_exp_table(self) -> None:
        table = self.query_one("#exp-table", DataTable)
        table.clear(columns=True)
        table.add_columns("Title", "Company", "Start", "End")
        rows = services.get_experiences(services.get_first_user_id())
        if not rows:
            table.add_row("No experiences found -- type /ingest to add your resume", "", "", "")
            return
        for row in rows:
            table.add_row(row["title"], row["company"], row["start"], row["end"])

    def _load_proj_cards(self) -> None:
        scroll = self.query_one("#proj-scroll", VerticalScroll)
        scroll.remove_children()
        rows = services.get_projects(services.get_first_user_id())
        if not rows:
            scroll.mount(Static(
                "No projects found — type `ingest github <username>` to add GitHub repos",
                classes="system-msg",
            ))
            return
        for row in rows:
            desc = row["desc"]
            if len(desc) > 120:
                desc = desc[:120] + "..."
            scroll.mount(Static(
                f"[bold]{row['name']}[/bold]\n"
                f"[dim]URL:[/dim]   {row['url']}\n"
                f"[dim]Desc:[/dim]  {desc}",
                classes="proj-card",
            ))

    @work(thread=True)
    def _load_graph_view(self) -> None:
        self.call_from_thread(self._init_graph_tree)
        try:
            from knowledge_graph.builder import SkillGraphBuilder
            G = SkillGraphBuilder().build_graph()
            data = self._extract_graph_data(G)
            self.call_from_thread(self._render_graph_tree, data)
        except Exception as e:
            self.call_from_thread(self._graph_tree_error, str(e))

    def _init_graph_tree(self) -> None:
        tree = self.query_one("#graph-tree", Tree)
        tree.clear()
        tree.root.set_label("Loading knowledge graph...")

    def _extract_graph_data(self, G) -> dict:
        """Build a serializable data dict from the graph (runs in background thread)."""
        skill_nodes = [(n, d) for n, d in G.nodes(data=True) if d.get("type") == "Skill"]
        exp_nodes   = [(n, d) for n, d in G.nodes(data=True) if d.get("type") == "Experience"]
        proj_nodes  = [(n, d) for n, d in G.nodes(data=True) if d.get("type") == "Project"]

        skills = []
        for node, data in sorted(skill_nodes, key=lambda x: x[1].get("name", "")):
            preds = list(G.predecessors(node))
            proj_srcs = [G.nodes[p].get("name", p) for p in preds if G.nodes[p].get("type") == "Project"]
            exp_srcs  = [G.nodes[p].get("name", p) for p in preds if G.nodes[p].get("type") == "Experience"]
            skills.append({
                "name": data.get("name", node),
                "category": data.get("category") or "Uncategorized",
                "projects": proj_srcs,
                "experiences": exp_srcs,
            })

        experiences = []
        for node, data in sorted(exp_nodes, key=lambda x: x[1].get("name", "")):
            skill_names = [G.nodes[s].get("name", s) for s in G.successors(node)
                           if G.nodes[s].get("type") == "Skill"]
            experiences.append({
                "title": data.get("name", ""),
                "company": data.get("company", ""),
                "skills": sorted(skill_names),
            })

        projects = []
        for node, data in sorted(proj_nodes, key=lambda x: x[1].get("name", "")):
            skill_names = [G.nodes[s].get("name", s) for s in G.successors(node)
                           if G.nodes[s].get("type") == "Skill"]
            projects.append({"name": data.get("name", node), "skills": sorted(skill_names)})

        return {
            "nodes": G.number_of_nodes(),
            "edges": G.number_of_edges(),
            "skills": skills,
            "experiences": experiences,
            "projects": projects,
        }

    def _render_graph_tree(self, data: dict) -> None:
        from textual.css.query import NoMatches
        try:
            tree = self.query_one("#graph-tree", Tree)
        except NoMatches:
            return
        tree.clear()
        tree.root.set_label(
            f"[bold]Knowledge Graph[/bold] — {data['nodes']} nodes · {data['edges']} edges"
        )

        # Skills branch — grouped by category
        skills_node = tree.root.add(
            f"[bold cyan]Skills ({len(data['skills'])})[/bold cyan]"
        )
        by_cat: dict[str, list] = {}
        for s in data["skills"]:
            by_cat.setdefault(s["category"], []).append(s)
        for cat in sorted(by_cat):
            cat_node = skills_node.add(f"[cyan]{cat}[/cyan] ({len(by_cat[cat])})")
            for s in by_cat[cat]:
                skill_node = cat_node.add(f"[bold]{s['name']}[/bold]")
                if s["projects"]:
                    skill_node.add_leaf(f"[dim]Projects:[/dim]  {', '.join(s['projects'])}")
                if s["experiences"]:
                    skill_node.add_leaf(f"[dim]Experiences:[/dim]  {', '.join(s['experiences'])}")
                if not s["projects"] and not s["experiences"]:
                    skill_node.add_leaf("[dim]No connections yet[/dim]")

        # Experiences branch
        exp_node = tree.root.add(
            f"[bold green]Experiences ({len(data['experiences'])})[/bold green]"
        )
        for e in data["experiences"]:
            label = f"[green]{e['title']}[/green]"
            if e["company"]:
                label += f"  [dim]@ {e['company']}[/dim]"
            n = exp_node.add(label)
            if e["skills"]:
                n.add_leaf(f"[dim]Skills:[/dim]  {', '.join(e['skills'])}")
            else:
                n.add_leaf("[dim]No skills linked[/dim]")

        # Projects branch
        proj_node = tree.root.add(
            f"[bold yellow]Projects ({len(data['projects'])})[/bold yellow]"
        )
        for p in data["projects"]:
            n = proj_node.add(f"[yellow]{p['name']}[/yellow]")
            if p["skills"]:
                n.add_leaf(f"[dim]Skills:[/dim]  {', '.join(p['skills'])}")
            else:
                n.add_leaf("[dim]No skills linked[/dim]")

        tree.root.expand()

    def _graph_tree_error(self, error: str) -> None:
        from textual.css.query import NoMatches
        try:
            tree = self.query_one("#graph-tree", Tree)
        except NoMatches:
            return
        tree.clear()
        tree.root.set_label(f"[red]Error loading graph: {error}[/red]")


# ───────────────────────────────────────────────────────────
#  Entry point
# ───────────────────────────────────────────────────────────

def main():
    import signal
    # Suppress SIGINT (ctrl+c) at the OS level so PowerShell cannot kill the
    # process mid-session.  ctrl+q remains the quit binding inside the app.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    app = ArtApp()
    app.run()


if __name__ == "__main__":
    main()
