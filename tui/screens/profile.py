"""ProfileScreen — view and edit the active profile."""
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Header, Footer, Static, Input, Button, Label
from textual import work


class ProfileScreen(Screen):
    """Overlay for viewing and editing profile info. Dismissed on Close."""

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

    #profile-status {
        padding: 0 4;
        height: 2;
        color: $accent;
    }

    #profile-btn-row {
        padding: 1 4 2 4;
        height: auto;
    }

    #save-profile-btn {
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
                yield Label("LinkedIn URL", classes="field-label")
                yield Input(placeholder="https://linkedin.com/in/... (optional)", id="profile-linkedin-input", classes="field-input")
            yield Static("", id="profile-divider")
            yield Static("", id="profile-stats")
            yield Static("", id="profile-status")
            with Horizontal(id="profile-btn-row"):
                yield Button("Save Changes", variant="primary", id="save-profile-btn")
                yield Button("Close", id="close-profile-btn")

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

        sources = ", ".join(data["sources"]) if data["sources"] else "none"
        self.query_one("#profile-stats", Static).update(
            f"Skills: {data['skills']}  ·  Experiences: {data['experiences']}  ·  "
            f"Projects: {data['projects']}  ·  Sources: {sources}"
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save-profile-btn":
            self._save()
        elif event.button.id == "close-profile-btn":
            self.action_close()

    def _save(self) -> None:
        self.query_one("#save-profile-btn", Button).disabled = True
        self._run_save(
            name=self.query_one("#profile-name-input", Input).value.strip(),
            github=self.query_one("#profile-github-input", Input).value.strip(),
            linkedin=self.query_one("#profile-linkedin-input", Input).value.strip(),
        )

    @work(thread=True)
    def _run_save(self, name: str, github: str, linkedin: str) -> None:
        from tui import services
        data = services.get_profile_data()
        if not data:
            self.app.call_from_thread(
                self.query_one("#profile-status", Static).update, "No active profile."
            )
            return
        msg = services.update_profile(data["user_id"], name, github, linkedin)
        self.app.call_from_thread(self._after_save, name)

    def _after_save(self, name: str) -> None:
        initials = _initials(name)
        self.query_one("#profile-avatar-large", Static).update(initials)
        self.query_one("#profile-name-display", Static).update(name)
        self.query_one("#profile-status", Static).update("Saved.")
        self.query_one("#save-profile-btn", Button).disabled = False
        self.dismiss({"name": name})

    def action_close(self) -> None:
        self.dismiss(None)


def _initials(name: str) -> str:
    parts = name.strip().split()
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][0].upper()
    return (parts[0][0] + parts[-1][0]).upper()
