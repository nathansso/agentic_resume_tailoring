"""
ART TUI — Hybrid chat-first terminal interface for Agentic Resume Tailoring.

Layout:
  ┌─────────────────────────────────────────────────────┐
  │  Header                                             │
  ├────────────┬────────────────────────────────────────┤
  │  Jobs      │  [Chat] [Data] [Viz]                   │
  │  sidebar   │                                        │
  │            │  Chat messages / data tables / charts  │
  │            │                                        │
  │  + New Job │  [input box]                           │
  ├────────────┴────────────────────────────────────────┤
  │  Footer (F1=Ingest F2=Data F3=Tailor F4=Viz)       │
  └─────────────────────────────────────────────────────┘

Run: python -m tui.app  OR  python cli.py tui
"""
import json
import logging
from uuid import UUID, uuid4

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import (
    Header, Footer, Static, Input, Button, ListView,
    ListItem, Label, TabbedContent, TabPane, DataTable, RichLog,
)
from textual import work

from database.db import init_db, engine
from database.models import (
    User, Skill, UserSkill, Experience, Project,
    JobDescription, UserJobResult,
)
from sqlmodel import Session, select

logger = logging.getLogger(__name__)


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

    /* Viz Tab */
    #viz-content {
        height: 1fr;
        padding: 1;
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
    """

    TITLE = "ART — Agentic Resume Tailoring"
    SUB_TITLE = "Hybrid Chat + Tools"

    BINDINGS = [
        Binding("f1", "ingest", "Ingest"),
        Binding("f2", "show_data", "Data"),
        Binding("f3", "tailor", "Tailor"),
        Binding("f4", "show_viz", "Viz"),
        Binding("ctrl+n", "new_job", "New Job"),
        Binding("ctrl+q", "quit", "Quit"),
    ]

    def __init__(self):
        super().__init__()
        self.chat_agent = None  # Lazy-init to avoid slow import at startup
        self._job_item_to_uuid: dict[str, str] = {}

    def compose(self) -> ComposeResult:
        yield Header()
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
                            yield Static(
                                "Welcome to ART! I'm your resume tailoring assistant.\n\n"
                                "I can help you:\n"
                                " * Ingest your resume, GitHub, or LinkedIn data\n"
                                " * View your skills, experiences, and projects\n"
                                " * Analyze job descriptions and find skill gaps\n"
                                " * Tailor your resume for specific roles\n\n"
                                "Type a message below, or use F1-F4 for shortcuts.\n"
                                "Try: 'show my skills' or 'profile'",
                                classes="bot-msg",
                            )
                        with Horizontal(id="chat-input-row"):
                            yield Input(
                                placeholder="Type a message... (F1=Ingest F2=Data F3=Tailor F4=Viz)",
                                id="chat-input",
                            )
                            yield Button("Send", variant="primary", id="send-btn")

                    # ── Data Tab ──
                    with TabPane("Data", id="tab-data"):
                        with TabbedContent(id="data-subtabs"):
                            with TabPane("Skills", id="subtab-skills"):
                                yield DataTable(id="skills-table")
                            with TabPane("Experiences", id="subtab-exp"):
                                yield DataTable(id="exp-table")
                            with TabPane("Projects", id="subtab-proj"):
                                yield DataTable(id="proj-table")
                            with TabPane("Graph", id="subtab-graph"):
                                yield RichLog(id="graph-log", markup=True)

                    # ── Visualization Tab ──
                    with TabPane("Viz", id="tab-viz"):
                        yield RichLog(id="viz-content", markup=True)

        yield Footer()

    # ───────────────────────────────────────────────────────
    #  Lifecycle
    # ───────────────────────────────────────────────────────

    def on_mount(self) -> None:
        init_db()
        self._load_jobs_sidebar()
        self._load_data_tables()
        self._load_viz()

    # ───────────────────────────────────────────────────────
    #  Chat
    # ───────────────────────────────────────────────────────

    def _get_agent(self):
        if self.chat_agent is None:
            from agents.chat import ChatAgent
            self.chat_agent = ChatAgent()
        return self.chat_agent

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "chat-input":
            self._handle_chat_input(event.value)
            event.input.value = ""

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn = event.button.id
        if btn == "send-btn":
            chat_input = self.query_one("#chat-input", Input)
            if chat_input.value.strip():
                self._handle_chat_input(chat_input.value)
                chat_input.value = ""
        elif btn == "new-job-btn":
            self.action_new_job()
        elif btn == "save-job-btn":
            self._save_new_job()
        elif btn == "cancel-job-btn":
            self._hide_job_input()

    def _handle_chat_input(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        scroll = self.query_one("#chat-scroll", VerticalScroll)
        scroll.mount(Static(f"You: {text}", classes="user-msg"))
        scroll.mount(Static("Thinking...", classes="system-msg", id="thinking"))
        scroll.scroll_end()
        self._run_chat(text)

    @work(thread=True)
    def _run_chat(self, text: str) -> None:
        """Run chat agent in a background thread so the UI stays responsive."""
        try:
            agent = self._get_agent()
            response = agent.chat(text)
        except Exception as e:
            response = f"Error: {e}"
        self.call_from_thread(self._post_chat_response, response)

    def _post_chat_response(self, response: str) -> None:
        scroll = self.query_one("#chat-scroll", VerticalScroll)
        for t in scroll.query("#thinking"):
            t.remove()
        scroll.mount(Static(response, classes="bot-msg"))
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
        self.query_one("#chat-input", Input).focus()

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
        self.query_one("#chat-input", Input).focus()

    def action_show_viz(self) -> None:
        """F4 — Switch to viz tab and refresh."""
        self._load_viz()
        tabs = self.query_one("#chat-tabs", TabbedContent)
        tabs.active = "tab-viz"

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
        with Session(engine) as session:
            jobs = session.exec(select(JobDescription)).all()
            for job in jobs:
                results = session.exec(
                    select(UserJobResult).where(UserJobResult.job_id == job.job_id)
                ).all()
                score = ""
                if results:
                    best = max(r.ats_score for r in results)
                    score = f" [{best:.0f}%]"

                # Use a unique widget ID every render to avoid duplicate-ID races
                # when ListView items are reloaded rapidly.
                item_id = f"job-item-{uuid4().hex}"
                self._job_item_to_uuid[item_id] = str(job.job_id)
                item = ListItem(
                    Label(f"{job.title}\n{job.company}{score}"),
                    id=item_id,
                )
                job_list.append(item)

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
        # Confirm in chat
        scroll.mount(Static(f"Job saved: {title} @ {company}", classes="bot-msg"))
        scroll.scroll_end()

    def _hide_job_input(self) -> None:
        self.query_one("#job-input-area").remove_class("visible")

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
        with Session(engine) as session:
            job = session.get(JobDescription, UUID(job_uuid))
            if not job:
                return
            results = session.exec(
                select(UserJobResult).where(UserJobResult.job_id == job.job_id)
            ).all()
            lines = [f"{job.title} @ {job.company}"]
            if results:
                latest = max(results, key=lambda r: r.created_at)
                lines.append(f"\nLatest ATS Score: {latest.ats_score}%")
                if latest.matched_skills:
                    matched = list(latest.matched_skills.keys())[:10]
                    lines.append(f"Matched: {', '.join(matched)}")
                if latest.missing_skills:
                    missing = latest.missing_skills[:10]
                    lines.append(f"Missing: {', '.join(missing)}")
            else:
                lines.append("\nNo tailoring results yet. Press F3 to tailor.")
            scroll.mount(Static("\n".join(lines), classes="bot-msg"))
            scroll.scroll_end()

    # ───────────────────────────────────────────────────────
    #  Data Tables
    # ───────────────────────────────────────────────────────

    def _load_data_tables(self) -> None:
        self._load_skills_table()
        self._load_exp_table()
        self._load_proj_table()
        self._load_graph_view()

    def _load_skills_table(self) -> None:
        table = self.query_one("#skills-table", DataTable)
        table.clear(columns=True)
        table.add_columns("Skill", "Source", "Proficiency", "Confidence")
        with Session(engine) as session:
            user = session.exec(select(User).limit(1)).first()
            if not user:
                return
            user_skills = session.exec(
                select(UserSkill).where(UserSkill.user_id == user.user_id)
            ).all()
            for us in user_skills:
                skill = session.get(Skill, us.skill_id)
                if skill:
                    source = (us.evidence_source or "unknown").split(":")[0]
                    table.add_row(
                        skill.name,
                        source,
                        str(us.proficiency or "N/A"),
                        f"{us.confidence_score:.1f}",
                    )

    def _load_exp_table(self) -> None:
        table = self.query_one("#exp-table", DataTable)
        table.clear(columns=True)
        table.add_columns("Title", "Company", "Start", "End")
        with Session(engine) as session:
            user = session.exec(select(User).limit(1)).first()
            if not user:
                return
            exps = session.exec(
                select(Experience).where(Experience.user_id == user.user_id)
            ).all()
            for e in exps:
                table.add_row(e.title, e.company, e.start_date or "?", e.end_date or "?")

    def _load_proj_table(self) -> None:
        table = self.query_one("#proj-table", DataTable)
        table.clear(columns=True)
        table.add_columns("Project", "URL", "Description")
        with Session(engine) as session:
            user = session.exec(select(User).limit(1)).first()
            if not user:
                return
            projs = session.exec(
                select(Project).where(Project.user_id == user.user_id)
            ).all()
            for p in projs:
                desc = (p.description or "")[:60]
                table.add_row(p.name, p.repo_url or "—", desc)

    def _load_graph_view(self) -> None:
        log = self.query_one("#graph-log", RichLog)
        log.clear()
        try:
            from knowledge_graph.builder import SkillGraphBuilder
            builder = SkillGraphBuilder()
            G = builder.build_graph()

            log.write(f"[bold]Knowledge Graph[/bold]: {G.number_of_nodes()} nodes, "
                      f"{G.number_of_edges()} edges\n")

            skills = [n for n, d in G.nodes(data=True) if d.get("type") == "Skill"]
            projects = [n for n, d in G.nodes(data=True) if d.get("type") == "Project"]
            experiences = [n for n, d in G.nodes(data=True) if d.get("type") == "Experience"]

            log.write(f"\n[bold cyan]Skills ({len(skills)}):[/bold cyan]")
            for s in sorted(skills):
                name = G.nodes[s].get("name", s)
                preds = list(G.predecessors(s))
                if preds:
                    sources = [G.nodes[p].get("name", p) for p in preds[:3]]
                    log.write(f"  {name} <- {', '.join(sources)}")
                else:
                    log.write(f"  {name}")

            log.write(f"\n[bold green]Experiences ({len(experiences)}):[/bold green]")
            for e in sorted(experiences):
                name = G.nodes[e].get("name", e)
                company = G.nodes[e].get("company", "")
                succs = list(G.successors(e))
                skill_names = [G.nodes[s].get("name", s) for s in succs[:5]]
                log.write(f"  {name} @ {company}")
                if skill_names:
                    log.write(f"    -> {', '.join(skill_names)}")

            log.write(f"\n[bold yellow]Projects ({len(projects)}):[/bold yellow]")
            for p in sorted(projects)[:20]:
                name = G.nodes[p].get("name", p)
                succs = list(G.successors(p))
                skill_names = [G.nodes[s].get("name", s) for s in succs[:5]]
                log.write(f"  {name}")
                if skill_names:
                    log.write(f"    -> {', '.join(skill_names)}")

        except Exception as e:
            log.write(f"[red]Error loading graph: {e}[/red]")

    # ───────────────────────────────────────────────────────
    #  Visualization
    # ───────────────────────────────────────────────────────

    def _load_viz(self) -> None:
        log = self.query_one("#viz-content", RichLog)
        log.clear()

        try:
            import plotext as plt
        except ImportError:
            log.write("[red]plotext not installed. Run: pip install plotext[/red]")
            return

        with Session(engine) as session:
            user = session.exec(select(User).limit(1)).first()
            if not user:
                log.write("[yellow]No data to visualize. Ingest your resume first.[/yellow]")
                return

            user_skills = session.exec(
                select(UserSkill).where(UserSkill.user_id == user.user_id)
            ).all()

            # ── Chart 1: Skills by Source ──
            source_counts: dict[str, int] = {}
            for us in user_skills:
                src = (us.evidence_source or "unknown").split(":")[0]
                source_counts[src] = source_counts.get(src, 0) + 1

            if source_counts:
                plt.clear_figure()
                plt.theme("dark")
                plt.bar(list(source_counts.keys()), list(source_counts.values()))
                plt.title("Skills by Source")
                plt.xlabel("Source")
                plt.ylabel("Count")
                log.write(plt.build())

            # ── Chart 2: Top Skills by Confidence ──
            skill_scores = []
            for us in user_skills:
                skill = session.get(Skill, us.skill_id)
                if skill and us.confidence_score > 0:
                    skill_scores.append((skill.name, us.confidence_score))
            skill_scores.sort(key=lambda x: x[1], reverse=True)
            top = skill_scores[:15]

            if top:
                plt.clear_figure()
                plt.theme("dark")
                names = [s[0] for s in reversed(top)]
                scores = [s[1] for s in reversed(top)]
                plt.bar(names, scores, orientation="h")
                plt.title("Top Skills by Confidence")
                log.write("\n" + plt.build())

            # ── Chart 3: Knowledge Graph Connectivity ──
            try:
                from knowledge_graph.builder import SkillGraphBuilder
                builder = SkillGraphBuilder()
                G = builder.build_graph()
                degrees = sorted(G.degree(), key=lambda x: x[1], reverse=True)[:10]
                if degrees:
                    plt.clear_figure()
                    plt.theme("dark")
                    names = [d[0].split(":")[-1][:20] for d in reversed(degrees)]
                    counts = [d[1] for d in reversed(degrees)]
                    plt.bar(names, counts, orientation="h")
                    plt.title("Most Connected Nodes (Knowledge Graph)")
                    log.write("\n" + plt.build())
            except Exception:
                pass

            # ── Chart 4: ATS Score History ──
            results = session.exec(
                select(UserJobResult).where(UserJobResult.user_id == user.user_id)
            ).all()
            if results:
                # Sort by created_at
                results_sorted = sorted(results, key=lambda r: r.created_at)
                plt.clear_figure()
                plt.theme("dark")
                dates = [r.created_at.strftime("%m/%d") for r in results_sorted]
                scores = [r.ats_score for r in results_sorted]
                plt.bar(dates, scores)
                plt.title("ATS Scores Over Time")
                plt.ylabel("Score %")
                log.write("\n" + plt.build())


# ───────────────────────────────────────────────────────────
#  Entry point
# ───────────────────────────────────────────────────────────

def main():
    app = ArtApp()
    app.run()


if __name__ == "__main__":
    main()
