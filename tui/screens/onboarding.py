"""OnboardingScreen — step-by-step first-run profile setup."""
import time
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, Horizontal
from textual.screen import Screen
from textual.widgets import Header, Footer, Static, Input, Button, Label
from textual import work


_STEPS = ["name", "resume", "github", "linkedin"]


class OnboardingScreen(Screen):
    """First-run screen: collect profile info one step at a time."""

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

    #step-indicator {
        color: $text-muted;
        text-align: center;
        padding-bottom: 1;
    }

    #step-question {
        text-style: bold;
        padding-bottom: 1;
    }

    #step-hint {
        color: $text-muted;
        padding-bottom: 1;
    }

    #step-input {
        width: 1fr;
    }

    #onboarding-status {
        padding-top: 1;
        color: $accent;
        text-align: center;
        height: 2;
    }

    #btn-row {
        height: auto;
        margin-top: 1;
    }

    #next-btn {
        width: 1fr;
    }

    #skip-btn {
        width: 14;
        margin-left: 1;
    }

    .hidden {
        display: none;
    }
    """

    BINDINGS = [Binding("ctrl+q", "app.quit", "Quit")]

    # Step metadata: (question, hint, placeholder, skippable)
    _STEP_META = {
        "name": (
            "What's your name?",
            "This will appear on your profile.",
            "Your full name",
            False,
        ),
        "resume": (
            "Upload your resume.",
            "Accepted formats: PDF, DOCX, MD — paste the full file path or click Upload.",
            "Path to resume file",
            False,
        ),
        "github": (
            "What's your GitHub username?",
            "We'll fetch your public repos to extract skills and projects.",
            "github-username  (optional)",
            True,
        ),
        "linkedin": (
            "What's your LinkedIn profile URL?",
            "Optional — used for profile enrichment.",
            "https://linkedin.com/in/...  (optional)",
            True,
        ),
    }

    def __init__(self):
        super().__init__()
        self._step_index = 0
        self._answers: dict[str, str] = {}

    @property
    def _current_step(self) -> str:
        return _STEPS[self._step_index]

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="onboarding-panel"):
            yield Static("ART -- Agentic Resume Tailoring", id="onboarding-title")
            yield Static("", id="step-indicator")
            yield Static("", id="step-question")
            yield Static("", id="step-hint")
            with Horizontal(id="input-row"):
                yield Input(placeholder="", id="step-input")
                yield Button("Upload", id="upload-btn", classes="hidden")
            yield Static("", id="onboarding-status")
            with Horizontal(id="btn-row"):
                yield Button("Next", variant="primary", id="next-btn")
                yield Button("Skip", id="skip-btn", classes="hidden")
        yield Footer()

    def on_mount(self) -> None:
        self._render_step()

    def _render_step(self) -> None:
        step = self._current_step
        question, hint, placeholder, skippable = self._STEP_META[step]
        total = len(_STEPS)
        current = self._step_index + 1

        self.query_one("#step-indicator", Static).update(f"Step {current} of {total}")
        self.query_one("#step-question", Static).update(question)
        self.query_one("#step-hint", Static).update(hint)
        self.query_one("#onboarding-status", Static).update("")

        inp = self.query_one("#step-input", Input)
        inp.placeholder = placeholder
        inp.value = self._answers.get(step, "")

        upload_btn = self.query_one("#upload-btn", Button)
        skip_btn = self.query_one("#skip-btn", Button)

        if step == "resume":
            upload_btn.remove_class("hidden")
        else:
            upload_btn.add_class("hidden")

        if skippable:
            skip_btn.remove_class("hidden")
        else:
            skip_btn.add_class("hidden")

        inp.focus()

    def _set_status(self, msg: str) -> None:
        self.query_one("#onboarding-status", Static).update(msg)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "step-input":
            self._advance(skip=False)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "next-btn":
            self._advance(skip=False)
        elif event.button.id == "skip-btn":
            self._advance(skip=True)
        elif event.button.id == "upload-btn":
            self._open_file_dialog()

    def _open_file_dialog(self) -> None:
        """Open a native file-picker and put the chosen path in the input."""
        try:
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            path = filedialog.askopenfilename(
                title="Select your resume",
                filetypes=[
                    ("Resume files", "*.pdf *.docx *.doc *.md"),
                    ("All files", "*.*"),
                ],
            )
            root.destroy()
            if path:
                self.query_one("#step-input", Input).value = path
        except Exception as e:
            self._set_status(f"File picker unavailable: {e}")

    def _advance(self, skip: bool) -> None:
        step = self._current_step
        value = "" if skip else self.query_one("#step-input", Input).value.strip()

        # Validate required steps
        if not skip:
            if step == "name" and not value:
                self._set_status("Name is required.")
                return
            if step == "resume":
                if not value:
                    self._set_status("Resume path is required.")
                    return
                if not Path(value).exists():
                    self._set_status(f"File not found: {value}")
                    return

        self._answers[step] = value
        self._step_index += 1

        if self._step_index >= len(_STEPS):
            self._submit()
        else:
            self._render_step()

    def _submit(self) -> None:
        self.query_one("#next-btn", Button).disabled = True
        self._run_onboarding(
            name=self._answers.get("name", ""),
            email="",
            resume_path=self._answers.get("resume", ""),
            github_username=self._answers.get("github", ""),
            linkedin_url=self._answers.get("linkedin", ""),
        )

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
            self.app.call_from_thread(self._set_status, "Creating profile...")
            user = create_profile(
                name=name,
                email=email,
                github_username=github_username,
                linkedin_url=linkedin_url,
            )

            self.app.call_from_thread(self._set_status, "Parsing resume...")
            ingest_result = services.ingest_resume_file(resume_path)

            self.app.call_from_thread(self._set_status, "Done -- profile created!")
            time.sleep(0.6)
            self.app.call_from_thread(
                self.dismiss,
                {
                    "user_id": str(user.user_id),
                    "name": name,
                    "github_username": github_username,
                    "ingest_result": ingest_result,
                },
            )
        except Exception as e:
            self.app.call_from_thread(self._set_status, f"Error: {e}")
            self.app.call_from_thread(
                lambda: setattr(self.query_one("#next-btn", Button), "disabled", False)
            )
