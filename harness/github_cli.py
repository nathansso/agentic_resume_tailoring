"""`art github login|logout|status` (#353).

    art github login [--private]     # device flow; the token goes to the OS keychain
    art github status                # who is signed in, with which scopes; never the token
    art github logout                # removes the keychain entry

Like the other `art` subcommands it prints one JSON document on stdout. The
instructions for the user (the code to type and where) go to stderr, so a host
that runs it can read them without breaking the JSON. Run it in the user's own
terminal: the user has to open a page and type a code. The token is never printed.

Exit status: 0 on success, 1 on a failure (no client ID, no keychain, a refused
or expired code), 2 for bad arguments.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Callable, Optional, Sequence

from harness import github as gh
from harness.github import GitHubClient, GitHubError


def _emit(doc) -> None:
    sys.stdout.write(json.dumps(doc, sort_keys=True, ensure_ascii=False) + "\n")


def _fail(code: str, message: str) -> int:
    _emit({"ok": False, "error": {"code": code, "message": message}})
    return 1


def login(private: bool = False, client_id: Optional[str] = None, *,
          transport=None, sleep: Callable[[float], None] = time.sleep,
          clock: Callable[[], float] = time.monotonic, say=None) -> int:
    say = say or (lambda line: print(line, file=sys.stderr, flush=True))
    try:
        gh.require_client_id(client_id)
    except GitHubError as exc:
        return _fail(exc.code, exc.message)
    if not gh.keychain_available():
        # Checked before the flow starts, so a code is not spent on a token with nowhere to go.
        return _fail("no_keychain", (
            "ART keeps the GitHub token in the OS keychain through the optional `keyring` "
            "package, which is not available here. Install it next to ART (for example "
            "`uvx --with keyring ...`, or the art-mcp[keyring] extra) and run the login again, "
            f"or set {gh.TOKEN_ENV} to a personal access token instead."))
    try:
        code = gh.request_device_code(gh.scopes_for(private), client=client_id, transport=transport)
        say(f"To sign in, open {code.verification_uri} and enter the code {code.user_code}")
        say(f"The code expires in {max(code.expires_in // 60, 1)} minutes. Waiting for you...")
        granted = gh.poll_for_token(code, client=client_id, transport=transport, sleep=sleep,
                                    clock=clock)
        who = GitHubClient(granted.token, transport=transport).user()
    except GitHubError as exc:
        return _fail(exc.code, exc.message)
    if not gh.store_token(granted.token):
        return _fail("keychain_write_failed", "The keychain refused the token, so it was not "
                                              "saved. Nothing is signed in.")
    scopes = who["scopes"] or list(granted.scopes)
    _emit({"ok": True, "login": who["login"], "scopes": scopes,
           "private_scope": "repo" in scopes, "stored": "keychain"})
    return 0


def logout() -> int:
    removed = gh.delete_token()
    env = gh.token_source() == "env"
    note = None
    if env:
        note = (f"{gh.TOKEN_ENV} is set in the environment; unset it to finish signing out. "
                "GitHub keeps the authorisation until it is revoked at "
                "github.com/settings/applications.")
    elif removed:
        note = ("The token is removed from the keychain. GitHub keeps the authorisation until "
                "it is revoked at github.com/settings/applications.")
    _emit({"ok": True, "removed": removed, "still_set_in_env": env, "note": note})
    return 0


def status() -> int:
    _emit({"ok": True, **gh.login_status()})
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="art github", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    lp = sub.add_parser("login", help="Sign in with GitHub's device flow.")
    lp.add_argument("--private", action="store_true",
                    help="Also ask for the `repo` scope, so private repos can be listed.")
    lp.add_argument("--client-id", help=f"Override the OAuth App client ID (default: ${gh.CLIENT_ID_ENV}).")
    sub.add_parser("logout", help="Remove the stored token.")
    sub.add_parser("status", help="Show who is signed in (never the token).")
    args = p.parse_args(list(argv or []))
    if args.cmd == "login":
        return login(args.private, args.client_id)
    return logout() if args.cmd == "logout" else status()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
