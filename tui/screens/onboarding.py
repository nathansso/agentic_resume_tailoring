"""OnboardingScreen — step-by-step first-run profile setup."""
import os
import re
import time
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, Horizontal
from textual.screen import Screen
from textual.widgets import Header, Footer, Static, Input, Button, Label
from textual import work


_STEPS = ["provider", "username", "password", "confirm_password", "name", "resume", "github", "linkedin"]

_USERNAME_RE = re.compile(r"^[a-zA-Z0-9_-]{3,32}$")


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

    #provider-row {
        height: auto;
        margin-bottom: 1;
    }

    #provider-row Button {
        margin-right: 1;
        min-width: 14;
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
        "provider": (
            "Which AI provider do you use?",
            "Select a provider, then enter your API key below.",
            "Paste your API key here",
            False,
        ),
        "username": (
            "Choose a username.",
            "3–32 characters: letters, numbers, underscores, hyphens. Used to log in.",
            "your-username",
            False,
        ),
        "password": (
            "Create a password.",
            "Minimum 8 characters.",
            "Password",
            False,
        ),
        "confirm_password": (
            "Confirm your password.",
            "Re-enter your password.",
            "Confirm password",
            False,
        ),
        "name": (
            "What's your full name?",
            "This will appear on your resume.",
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
        self._selected_provider: str = "anthropic"
        self._provider_step_skipped: bool = self._key_already_configured()
        self._temp_password: str = ""

    @staticmethod
    def _key_already_configured() -> bool:
        """Return True if a usable API key is already present in the environment."""
        return bool(
            os.environ.get("ANTHROPIC_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
        )

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="onboarding-panel"):
            yield Static("ART -- Agentic Resume Tailoring", id="onboarding-title")
            yield Static("", id="step-indicator")
            yield Static("", id="step-question")
            yield Static("", id="step-hint")
            with Horizontal(id="provider-row", classes="hidden"):
                yield Button("Anthropic", id="provider-anthropic-btn", variant="primary")
                yield Button("OpenAI", id="provider-openai-btn")
            with Horizontal(id="input-row"):
                yield Input(placeholder="", id="step-input")
                yield Button("Upload", id="upload-btn", classes="hidden")
            yield Static("", id="onboarding-status")
            with Horizontal(id="btn-row"):
                yield Button("Next", variant="primary", id="next-btn")
                yield Button("Skip", id="skip-btn", classes="hidden")
        yield Footer()

    def on_mount(self) -> None:
        if self._provider_step_skipped:
            self._step_index = 1  # jump straight to "username"
        self._render_step()

    def _render_step(self) -> None:
        step = self._current_step
        question, hint, placeholder, skippable = self._STEP_META[step]

        skipped = 1 if self._provider_step_skipped else 0
        total = len(_STEPS) - skipped
        current = self._step_index + 1 - skipped

        self.query_one("#step-indicator", Static).update(f"Step {current} of {total}")
        self.query_one("#step-question", Static).update(question)
        self.query_one("#step-hint", Static).update(hint)
        self.query_one("#onboarding-status", Static).update("")

        inp = self.query_one("#step-input", Input)
        inp.placeholder = placeholder
        inp.value = self._answers.get(step, "")

        upload_btn = self.query_one("#upload-btn", Button)
        skip_btn = self.query_one("#skip-btn", Button)
        provider_row = self.query_one("#provider-row")

        if step == "provider":
            provider_row.remove_class("hidden")
            inp.password = True
            inp.placeholder = "Paste your API key here"
            upload_btn.add_class("hidden")
        else:
            provider_row.add_class("hidden")
            inp.password = step in ("password", "confirm_password")

        if step == "resume":
            upload_btn.remove_class("hidden")
        else:
            upload_btn.add_class("hidden")

        if skippable:
            skip_btn.remove_class("hidden")
        else:
            skip_btn.add_class("hidden")

        inp.focus()

    @property
    def _current_step(self) -> str:
        return _STEPS[self._step_index]

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
        elif event.button.id == "provider-anthropic-btn":
            self._select_provider("anthropic")
        elif event.button.id == "provider-openai-btn":
            self._select_provider("openai")

    def _select_provider(self, provider: str) -> None:
        self._selected_provider = provider
        anthropic_btn = self.query_one("#provider-anthropic-btn", Button)
        openai_btn = self.query_one("#provider-openai-btn", Button)
        if provider == "anthropic":
            anthropic_btn.variant = "primary"
            openai_btn.variant = "default"
        else:
            anthropic_btn.variant = "default"
            openai_btn.variant = "primary"

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

        if not skip:
            if step == "provider":
                if not value:
                    self._set_status("API key is required.")
                    return
                from tui import services
                services.save_llm_config(self._selected_provider, value)
                self._answers["provider"] = self._selected_provider
                self._answers["provider_key"] = value
                self._step_index += 1
                self._render_step()
                return

            if step == "username":
                if not _USERNAME_RE.match(value):
                    self._set_status(
                        "Username must be 3–32 characters: letters, numbers, _ or -"
                    )
                    return
                from database.user_utils import get_user_by_username
                if get_user_by_username(value) is not None:
                    self._set_status("That username is already taken.")
                    return

            if step == "password":
                if len(value) < 8:
                    self._set_status("Password must be at least 8 characters.")
                    return
                self._temp_password = value

            if step == "confirm_password":
                if value != self._temp_password:
                    self._set_status("Passwords do not match.")
                    return

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
        username = self._answers.get("username", "")
        name = self._answers.get("name", "")
        email = f"{username}@art.local" if username else f"user.{__import__('uuid').uuid4().hex[:8]}@local"
        self.query_one("#next-btn", Button).disabled = True
        self._run_onboarding(
            name=name,
            email=email,
            username=username,
            password=self._temp_password,
            resume_path=self._answers.get("resume", ""),
            github_username=self._answers.get("github", ""),
            linkedin_url=self._answers.get("linkedin", ""),
        )

    @work(thread=True)
    def _run_onboarding(
        self,
        name: str,
        email: str,
        username: str,
        password: str,
        resume_path: str,
        github_username: str,
        linkedin_url: str,
    ) -> None:
        from database.auth import hash_password, supabase_sign_up
        from database.user_utils import create_profile
        from tui import services

        try:
            self.app.call_from_thread(self._set_status, "Creating account...")

            pw_hash = hash_password(password) if password else None

            # Attempt Supabase sign-up if configured; fall through gracefully if not.
            supabase_uid = None
            if username and password:
                supabase_uid = supabase_sign_up(username, password)

            user = create_profile(
                name=name,
                email=email,
                username=username or None,
                password_hash=pw_hash,
                github_username=github_username,
                linkedin_url=linkedin_url,
                supabase_uid=supabase_uid,
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
