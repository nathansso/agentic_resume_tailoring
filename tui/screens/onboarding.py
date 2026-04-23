"""OnboardingScreen — first-run profile setup."""
import time
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, Horizontal
from textual.screen import Screen
from textual.widgets import Header, Footer, Static, Input, Button, Label
from textual import work


class OnboardingScreen(Screen):
    """First-run screen: collect profile info and ingest the resume."""

    CSS = """
    OnboardingScreen {
        align: center middle;
    }

    #onboarding-panel {
        width: 72;
        height: auto;
        border: solid $primary;
        padding: 2 4;
        background: $surface;
    }

    #onboarding-title {
        text-style: bold;
        color: $accent;
        text-align: center;
        padding-bottom: 1;
    }

    #onboarding-subtitle {
        color: $text-muted;
        text-align: center;
        padding-bottom: 1;
    }

    .field-label {
        padding-top: 1;
        color: $text-muted;
    }

    #onboarding-status {
        padding-top: 1;
        color: $accent;
        text-align: center;
        height: 2;
    }

    #submit-btn {
        width: 100%;
        margin-top: 2;
    }
    """

    BINDINGS = [Binding("ctrl+q", "app.quit", "Quit")]

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="onboarding-panel"):
            yield Static("ART -- Agentic Resume Tailoring", id="onboarding-title")
            yield Static("Create your profile to get started.", id="onboarding-subtitle")
            yield Label("Name *", classes="field-label")
            yield Input(placeholder="Your full name", id="name-input")
            yield Label("Email *", classes="field-label")
            yield Input(placeholder="your@email.com", id="email-input")
            yield Label("Resume file path *", classes="field-label")
            yield Input(placeholder="path/to/resume.md  (.md, .pdf, .docx)", id="resume-input")
            yield Label("GitHub username (optional)", classes="field-label")
            yield Input(placeholder="github-username", id="github-input")
            yield Label("LinkedIn URL (optional)", classes="field-label")
            yield Input(placeholder="https://linkedin.com/in/...", id="linkedin-input")
            yield Button("Create Profile & Ingest Resume", variant="primary", id="submit-btn")
            yield Static("", id="onboarding-status")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "submit-btn":
            self._submit()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "resume-input":
            self._submit()

    def _set_status(self, msg: str) -> None:
        self.query_one("#onboarding-status", Static).update(msg)

    def _submit(self) -> None:
        name   = self.query_one("#name-input",    Input).value.strip()
        email  = self.query_one("#email-input",   Input).value.strip()
        resume = self.query_one("#resume-input",  Input).value.strip()
        github = self.query_one("#github-input",  Input).value.strip()
        linkedin = self.query_one("#linkedin-input", Input).value.strip()

        if not name:
            self._set_status("Name is required.")
            return
        if not email:
            self._set_status("Email is required.")
            return
        if not resume:
            self._set_status("Resume file path is required.")
            return
        if not Path(resume).exists():
            self._set_status(f"File not found: {resume}")
            return

        self.query_one("#submit-btn", Button).disabled = True
        self._run_onboarding(name, email, resume, github, linkedin)

    @work(thread=True)
    def _run_onboarding(
        self,
        name: str,
        email: str,
        resume_path: str,
        github_username: str,
        linkedin_url: str,
    ) -> None:
        from database.user_utils import create_profile
        from tui import services

        try:
            self.call_from_thread(self._set_status, "Creating profile...")
            user = create_profile(
                name=name,
                email=email,
                github_username=github_username,
                linkedin_url=linkedin_url,
            )

            self.call_from_thread(self._set_status, "Parsing resume...")
            ingest_result = services.ingest_resume_file(resume_path)

            self.call_from_thread(self._set_status, "Done -- profile created!")
            time.sleep(0.6)
            self.call_from_thread(
                self.dismiss,
                {
                    "user_id": str(user.user_id),
                    "name": name,
                    "github_username": github_username,
                    "ingest_result": ingest_result,
                },
            )
        except Exception as e:
            self.call_from_thread(self._set_status, f"Error: {e}")
            self.call_from_thread(
                lambda: setattr(self.query_one("#submit-btn", Button), "disabled", False)
            )
