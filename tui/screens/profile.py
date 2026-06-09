"""ProfileScreen — view and edit the active profile."""
import os
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, Horizontal
from textual.screen import Screen
from textual.widgets import Static, Input, Button, Label
from textual import work

_TOKEN_MASK = "••••••••"


def _oauth_enabled() -> bool:
    """True when GITHUB_CLIENT_ID is configured (device flow available)."""
    from config import GITHUB_CLIENT_ID
    return bool(GITHUB_CLIENT_ID)


class ProfileScreen(Screen):
    """Overlay for viewing and editing profile info. Dismissed on Back to Chat."""

    CSS = """
    ProfileScreen {
        align: center middle;
    }

    #profile-panel {
        width: 72;
        height: auto;
        max-height: 90vh;
        border: solid $primary;
        background: $surface;
    }

    #profile-header-row {
        height: auto;
        padding: 2 4 0 4;
        align: left middle;
    }

    #profile-avatar-large {
        width: 7;
        height: 3;
        background: $accent;
        color: $background;
        text-style: bold;
        content-align: center middle;
        margin-right: 2;
    }

    #profile-title-block {
        height: auto;
    }

    #profile-name-display {
        text-style: bold;
        color: $accent;
    }

    #profile-subtitle {
        color: $text-muted;
    }

    #profile-form {
        padding: 1 4;
        height: auto;
    }

    .field-label {
        color: $text-muted;
        padding-top: 1;
    }

    .field-input {
        margin-bottom: 0;
    }

    #profile-divider {
        border-bottom: solid $primary;
        height: 1;
        margin: 1 4;
    }

    #profile-stats {
        padding: 0 4 1 4;
        color: $text-muted;
        height: auto;
    }

    #resume-section {
        padding: 0 4 1 4;
        height: auto;
    }

    #resume-btn-row {
        height: auto;
        padding-top: 1;
    }

    #resume-btn-row Button {
        margin-right: 1;
    }

    #resume-upload-area {
        display: none;
        height: auto;
        padding-top: 1;
    }

    #resume-upload-area Button {
        margin-right: 1;
        margin-top: 1;
    }

    #profile-status {
        padding: 0 4;
        height: 2;
        color: $accent;
    }

    #delete-confirm-row {
        display: none;
        padding: 0 4;
        height: 2;
        color: $accent;
    }

    #delete-confirm-row Button {
        margin-left: 1;
        min-width: 10;
    }

    #github-oauth-section {
        padding: 0 4 1 4;
        height: auto;
    }

    #github-oauth-btn-row {
        height: auto;
        padding-top: 1;
        padding-bottom: 1;
    }

    #github-oauth-btn-row Button {
        margin-right: 1;
    }

    #github-device-info {
        color: $accent;
        padding-top: 1;
        height: auto;
    }

    #llm-section {
        padding: 0 4 1 4;
        height: auto;
    }

    #llm-provider-row {
        height: auto;
        padding-top: 1;
        padding-bottom: 1;
    }

    #llm-provider-row Button {
        margin-right: 1;
        min-width: 14;
    }

    #profile-btn-row {
        padding: 1 4 2 4;
        height: auto;
    }

    #update-profile-btn {
        width: 1fr;
        margin-right: 1;
    }

    #close-profile-btn {
        width: 16;
    }
    """

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("ctrl+q", "app.quit", "Quit"),
    ]

    def __init__(self):
        super().__init__()
        self._llm_provider: str = "anthropic"  # tracks which provider button is active
        self._device_code: str = ""
        self._device_interval: int = 5

    def compose(self) -> ComposeResult:
        with Vertical(id="profile-panel"):
            with Horizontal(id="profile-header-row"):
                yield Static("?", id="profile-avatar-large")
                with Vertical(id="profile-title-block"):
                    yield Static("", id="profile-name-display")
                    yield Static("Local profile", id="profile-subtitle")
            with Vertical(id="profile-form"):
                yield Label("Name", classes="field-label")
                yield Input(placeholder="Your full name", id="profile-name-input", classes="field-input")
                yield Label("GitHub username", classes="field-label")
                yield Input(placeholder="github-username (optional)", id="profile-github-input", classes="field-input")
                yield Label("GitHub Token", classes="field-label")
                yield Input(password=True, placeholder="ghp_... (optional)", id="profile-token-input")
                yield Label("LinkedIn URL", classes="field-label")
                yield Input(placeholder="https://linkedin.com/in/... (optional)", id="profile-linkedin-input", classes="field-input")
                yield Label("Email", classes="field-label")
                yield Input(placeholder="you@example.com (optional)", id="profile-email-input", classes="field-input")
                yield Label("Phone", classes="field-label")
                yield Input(placeholder="+1 (555) 000-0000 (optional)", id="profile-phone-input", classes="field-input")
                yield Label("Location", classes="field-label")
                yield Input(placeholder="City, ST (optional)", id="profile-location-input", classes="field-input")
            with Vertical(id="github-oauth-section"):
                yield Label("GitHub OAuth", classes="field-label")
                with Horizontal(id="github-oauth-btn-row"):
                    yield Button("Connect via GitHub", id="github-connect-btn", variant="primary")
                    yield Button("Disconnect GitHub", id="github-disconnect-btn", variant="error")
                yield Static("", id="github-device-info")
            yield Static("", id="profile-divider")
            yield Static("", id="profile-stats")
            with Vertical(id="llm-section"):
                yield Label("AI Provider", classes="field-label")
                with Horizontal(id="llm-provider-row"):
                    yield Button("Anthropic", id="llm-anthropic-btn", variant="primary")
                    yield Button("OpenAI", id="llm-openai-btn")
                yield Label("API Key", classes="field-label")
                yield Input(password=True, placeholder="Paste your API key", id="llm-key-input")
            with Vertical(id="resume-section"):
                yield Label("Base Resume: none", id="resume-label")
                with Horizontal(id="resume-btn-row"):
                    yield Button("Delete Resume", id="delete-resume-btn", disabled=True)
                    yield Button("Upload New Resume", id="upload-resume-btn")
                with Vertical(id="resume-upload-area"):
                    yield Input(placeholder="Absolute path to resume file", id="resume-path-input")
                    yield Button("Confirm Upload", variant="primary", id="confirm-upload-btn")
                    yield Button("Cancel", id="cancel-upload-btn")
            yield Static("", id="profile-status")
            with Horizontal(id="delete-confirm-row"):
                yield Static("Delete resume path? Skills and experience data will be kept. ")
                yield Button("Confirm", id="confirm-delete-resume-btn", variant="error")
                yield Button("Cancel", id="cancel-delete-resume-btn")
            with Horizontal(id="profile-btn-row"):
                yield Button("Update", variant="primary", id="update-profile-btn")
                yield Button("Back", id="close-profile-btn")

    def on_mount(self) -> None:
        self._load_profile()

    def _load_profile(self) -> None:
        from tui import services
        data = services.get_profile_data()
        if not data:
            self.query_one("#profile-name-display", Static).update("No active profile")
            return

        name = data["name"]
        initials = _initials(name)

        self.query_one("#profile-avatar-large", Static).update(initials)
        self.query_one("#profile-name-display", Static).update(name)
        self.query_one("#profile-name-input", Input).value = name
        self.query_one("#profile-github-input", Input).value = data["github_username"]
        self.query_one("#profile-linkedin-input", Input).value = data["linkedin_url"]
        self.query_one("#profile-email-input", Input).value = data.get("email", "")
        self.query_one("#profile-phone-input", Input).value = data.get("phone", "")
        self.query_one("#profile-location-input", Input).value = data.get("location", "")

        # GitHub token — show mask if a token is stored, never the real value
        token = services.get_github_token()
        if token:
            self.query_one("#profile-token-input", Input).value = _TOKEN_MASK

        # GitHub OAuth section — show only when OAuth is configured
        oauth = _oauth_enabled()
        self.query_one("#github-oauth-section").display = oauth
        if oauth:
            connected = bool(token)
            self.query_one("#github-connect-btn", Button).display = not connected
            self.query_one("#github-disconnect-btn", Button).display = connected
            self.query_one("#github-device-info", Static).update(
                "Connected" if connected else ""
            )

        # LLM provider + API key
        provider, has_key = services.get_llm_config()
        self._llm_provider = provider
        self._update_provider_buttons(provider)
        if has_key:
            self.query_one("#llm-key-input", Input).value = _TOKEN_MASK

        sources = ", ".join(data["sources"]) if data["sources"] else "none"
        self.query_one("#profile-stats", Static).update(
            f"Skills: {data['skills']}  ·  Experiences: {data['experiences']}  ·  "
            f"Projects: {data['projects']}  ·  Sources: {sources}"
        )

        # Resume path
        resume_path = services.get_resume_path(data["user_id"])
        if resume_path:
            self.query_one("#resume-label", Label).update(f"Base Resume: {Path(resume_path).name}")
            self.query_one("#delete-resume-btn", Button).disabled = False

    def _update_provider_buttons(self, provider: str) -> None:
        """Highlight the active provider button."""
        anthropic_btn = self.query_one("#llm-anthropic-btn", Button)
        openai_btn = self.query_one("#llm-openai-btn", Button)
        if provider == "anthropic":
            anthropic_btn.variant = "primary"
            openai_btn.variant = "default"
        else:
            anthropic_btn.variant = "default"
            openai_btn.variant = "primary"

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn = event.button.id
        if btn == "github-connect-btn":
            self._start_device_flow()
            return
        elif btn == "github-disconnect-btn":
            from tui import services
            services.save_github_token("")
            self.query_one("#profile-token-input", Input).value = ""
            self.query_one("#github-connect-btn", Button).display = True
            self.query_one("#github-disconnect-btn", Button).display = False
            self.query_one("#github-device-info", Static).update("")
            self.app.notify("GitHub disconnected.", severity="information", timeout=3)
            return
        if btn == "llm-anthropic-btn":
            self._llm_provider = "anthropic"
            self._update_provider_buttons("anthropic")
            return
        elif btn == "llm-openai-btn":
            self._llm_provider = "openai"
            self._update_provider_buttons("openai")
            return
        elif btn == "update-profile-btn":
            self._save()
        elif btn == "close-profile-btn":
            self.action_close()
        elif btn == "upload-resume-btn":
            self.query_one("#resume-upload-area").display = True
            self.query_one("#resume-path-input", Input).focus()
        elif btn == "cancel-upload-btn":
            self.query_one("#resume-upload-area").display = False
        elif btn == "confirm-upload-btn":
            path = self.query_one("#resume-path-input", Input).value.strip()
            if path:
                self.query_one("#profile-status", Static).update("Ingesting resume...")
                self._run_ingest_resume(path)
        elif btn == "delete-resume-btn":
            self.query_one("#profile-status").display = False
            self.query_one("#delete-confirm-row").display = True
        elif btn == "confirm-delete-resume-btn":
            self._confirm_delete_resume()
        elif btn == "cancel-delete-resume-btn":
            self.query_one("#delete-confirm-row").display = False
            self.query_one("#profile-status").display = True

    def _save(self) -> None:
        self.query_one("#update-profile-btn", Button).disabled = True

        from tui import services

        # GitHub token — only write if the user changed it from the mask
        token_value = self.query_one("#profile-token-input", Input).value
        if token_value != _TOKEN_MASK:
            services.save_github_token(token_value)

        # LLM config — save provider+key when a real key is entered; provider-only otherwise
        llm_key_value = self.query_one("#llm-key-input", Input).value
        if llm_key_value and llm_key_value != _TOKEN_MASK:
            # New key entered — persist both provider and key
            services.save_llm_config(self._llm_provider, llm_key_value)
            self.query_one("#llm-key-input", Input).value = _TOKEN_MASK
        else:
            # Provider button may have changed without a new key — persist just the provider
            services.save_llm_provider_only(self._llm_provider)

        self._run_save(
            name=self.query_one("#profile-name-input", Input).value.strip(),
            github=self.query_one("#profile-github-input", Input).value.strip(),
            linkedin=self.query_one("#profile-linkedin-input", Input).value.strip(),
            email=self.query_one("#profile-email-input", Input).value.strip(),
            phone=self.query_one("#profile-phone-input", Input).value.strip(),
            location=self.query_one("#profile-location-input", Input).value.strip(),
        )

    @work(thread=True)
    def _run_save(self, name: str, github: str, linkedin: str,
                  email: str = "", phone: str = "", location: str = "") -> None:
        from tui import services
        data = services.get_profile_data()
        if not data:
            self.app.call_from_thread(
                self.query_one("#profile-status", Static).update, "No active profile."
            )
            self.app.call_from_thread(
                setattr, self.query_one("#update-profile-btn", Button), "disabled", False
            )
            return
        services.update_profile(data["user_id"], name, github, linkedin,
                                 phone=phone, email=email, location=location)
        self.app.call_from_thread(self._after_save, name)

    def _after_save(self, name: str) -> None:
        initials = _initials(name)
        self.query_one("#profile-avatar-large", Static).update(initials)
        self.query_one("#profile-name-display", Static).update(name)
        self.query_one("#update-profile-btn", Button).disabled = False
        self.app.notify("Profile saved.", severity="information", timeout=3)

    @work(thread=True)
    def _run_ingest_resume(self, path: str) -> None:
        from tui import services
        result = services.ingest_resume_file(path)
        is_error = result.startswith("File not found:") or result.startswith("Ingestion failed:")
        if not is_error:
            data = services.get_profile_data()
            if data:
                services.update_resume_path(data["user_id"], path)
        self.app.call_from_thread(self._after_ingest_resume, path, result, not is_error)

    def _after_ingest_resume(self, path: str, message: str, success: bool) -> None:
        if success:
            self.query_one("#resume-label", Label).update(f"Base Resume: {Path(path).name}")
            self.query_one("#resume-upload-area").display = False
            self.query_one("#delete-resume-btn", Button).disabled = False
            self.query_one("#profile-status", Static).update("Resume ingested.")
        else:
            self.query_one("#profile-status", Static).update(message)

    def _confirm_delete_resume(self) -> None:
        from tui import services
        data = services.get_profile_data()
        if data:
            services.delete_resume(data["user_id"])
        self.query_one("#resume-label", Label).update("Base Resume: none")
        self.query_one("#delete-resume-btn", Button).disabled = True
        self.query_one("#delete-confirm-row").display = False
        self.query_one("#profile-status").display = True

    @work(thread=True)
    def _start_device_flow(self) -> None:
        from tui import services
        try:
            flow = services.start_github_device_flow()
        except Exception as e:
            self.app.call_from_thread(
                self.query_one("#github-device-info", Static).update,
                f"Error: {e}",
            )
            return
        self._device_code = flow["device_code"]
        self._device_interval = flow["interval"]
        msg = (
            f"Go to: {flow['verification_uri']}\n"
            f"Enter code: {flow['user_code']}\n"
            "Waiting for authorization…"
        )
        self.app.call_from_thread(
            self.query_one("#github-device-info", Static).update, msg
        )
        self.app.call_from_thread(
            setattr, self.query_one("#github-connect-btn", Button), "disabled", True
        )
        self._poll_device_flow()

    @work(thread=True)
    def _poll_device_flow(self) -> None:
        from tui import services
        expires = 900
        elapsed = 0
        while elapsed < expires:
            try:
                token = services.poll_github_device_flow(self._device_code, self._device_interval)
            except Exception as e:
                self.app.call_from_thread(
                    self.query_one("#github-device-info", Static).update, f"Error: {e}"
                )
                self.app.call_from_thread(
                    setattr, self.query_one("#github-connect-btn", Button), "disabled", False
                )
                return
            if token:
                services.save_github_token(token)
                self.app.call_from_thread(self._after_github_connect)
                return
            elapsed += self._device_interval

        self.app.call_from_thread(
            self.query_one("#github-device-info", Static).update, "Authorization timed out. Try again."
        )
        self.app.call_from_thread(
            setattr, self.query_one("#github-connect-btn", Button), "disabled", False
        )

    def _after_github_connect(self) -> None:
        self.query_one("#profile-token-input", Input).value = _TOKEN_MASK
        self.query_one("#github-connect-btn", Button).display = False
        self.query_one("#github-connect-btn", Button).disabled = False
        self.query_one("#github-disconnect-btn", Button).display = True
        self.query_one("#github-device-info", Static).update("Connected")
        self.app.notify("GitHub connected.", severity="information", timeout=3)

    def action_close(self) -> None:
        self.dismiss(None)


def _initials(name: str) -> str:
    parts = name.strip().split()
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][0].upper()
    return (parts[0][0] + parts[-1][0]).upper()
