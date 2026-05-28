"""LoginScreen — username + password sign-in for returning users."""
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, Horizontal
from textual.screen import Screen
from textual.widgets import Header, Footer, Static, Input, Button
from textual import work


class LoginScreen(Screen):
    """Sign-in screen shown to returning users."""

    CSS = """
    LoginScreen {
        align: center middle;
    }

    #login-panel {
        width: 60;
        height: auto;
        border: solid $primary;
        padding: 2 4;
        background: $surface;
    }

    #login-title {
        text-style: bold;
        color: $accent;
        text-align: center;
        padding-bottom: 1;
    }

    #login-subtitle {
        color: $text-muted;
        text-align: center;
        padding-bottom: 2;
    }

    #login-username, #login-password {
        margin-bottom: 1;
    }

    #login-status {
        color: $error;
        text-align: center;
        height: 2;
        padding-top: 1;
    }

    #login-btn-row {
        height: auto;
        margin-top: 1;
    }

    #signin-btn {
        width: 1fr;
    }

    #newaccount-btn {
        width: 20;
        margin-left: 1;
    }
    """

    BINDINGS = [Binding("ctrl+q", "app.quit", "Quit")]

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="login-panel"):
            yield Static("ART -- Agentic Resume Tailoring", id="login-title")
            yield Static("Sign in to continue.", id="login-subtitle")
            yield Input(placeholder="Username", id="login-username")
            yield Input(placeholder="Password", password=True, id="login-password")
            yield Static("", id="login-status")
            with Horizontal(id="login-btn-row"):
                yield Button("Sign In", variant="primary", id="signin-btn")
                yield Button("New Account", id="newaccount-btn")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#login-username", Input).focus()

    def _set_status(self, msg: str) -> None:
        self.query_one("#login-status", Static).update(msg)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "login-username":
            self.query_one("#login-password", Input).focus()
        elif event.input.id == "login-password":
            self._do_signin()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "signin-btn":
            self._do_signin()
        elif event.button.id == "newaccount-btn":
            self.dismiss({"action": "new_account"})

    def _do_signin(self) -> None:
        username = self.query_one("#login-username", Input).value.strip()
        password = self.query_one("#login-password", Input).value
        if not username or not password:
            self._set_status("Please enter your username and password.")
            return
        self.query_one("#signin-btn", Button).disabled = True
        self._set_status("Signing in...")
        self._run_auth(username, password)

    @work(thread=True)
    def _run_auth(self, username: str, password: str) -> None:
        import os
        from database.user_utils import (
            authenticate_local,
            get_user_by_username,
            ART_DIR,
            ACTIVE_PROFILE_FILE,
        )

        try:
            if os.getenv("SUPABASE_URL"):
                from database.auth import supabase_sign_in
                from database.session_store import save_session
                user = get_user_by_username(username)
                if not user or not user.email:
                    self.app.call_from_thread(self._set_status, "Invalid username or password.")
                    return
                result = supabase_sign_in(user.email, password)
                if result:
                    if "access_token" in result:
                        save_session(
                            result["access_token"],
                            result["refresh_token"],
                            result["expires_at"],
                            result["supabase_uid"],
                        )
                    ART_DIR.mkdir(parents=True, exist_ok=True)
                    ACTIVE_PROFILE_FILE.write_text(str(user.user_id))
                    self.app.call_from_thread(
                        self.dismiss,
                        {"user_id": str(user.user_id), "name": user.name},
                    )
                    return
                self.app.call_from_thread(self._set_status, "Invalid username or password.")
            else:
                user = authenticate_local(username, password)
                if user:
                    ART_DIR.mkdir(parents=True, exist_ok=True)
                    ACTIVE_PROFILE_FILE.write_text(str(user.user_id))
                    self.app.call_from_thread(
                        self.dismiss,
                        {"user_id": str(user.user_id), "name": user.name},
                    )
                else:
                    self.app.call_from_thread(
                        self._set_status, "Invalid username or password."
                    )
        except Exception as e:
            self.app.call_from_thread(self._set_status, f"Error: {e}")
        finally:
            self.app.call_from_thread(
                lambda: setattr(self.query_one("#signin-btn", Button), "disabled", False)
            )
