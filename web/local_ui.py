"""`art ui` launcher (issue #204): the React editor as a local, login-free app.

    python -m web.local_ui [--job ID] [--port 8765] [--database-url URL]
                           [--user-id UUID] [--allow-writes] [--no-open]

It lives under `web/`, not `harness/`: the routers import the legacy model
clients, which the harness boundary forbids (#190). The database is pinned the
same way the harness entry points pin it (`harness.runtime`): local SQLite
(`~/.art/art.db`) unless `--database-url` says otherwise, a `.env`
`DATABASE_URL` is never picked up implicitly, and Postgres is read-only unless
`--allow-writes`. That pinning must happen before anything imports `config`,
`database` or `web.app`, so every such import below is deferred.

Until #194 packages an `art` console script, this module is the entry point.
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import webbrowser
from pathlib import Path
from typing import Optional, Sequence

STATIC_DIR = Path(__file__).resolve().parent / "static"
DEFAULT_PORT = 8765
HOST = "127.0.0.1"
BUILD_HINT = "npm --prefix web/frontend install && npm --prefix web/frontend run build"


def missing_static(static_dir: Path = STATIC_DIR) -> Optional[str]:
    """None when the built SPA is present, else the message to exit with."""
    if (static_dir / "index.html").is_file():
        return None
    return (f"art ui: the editor has not been built ({static_dir / 'index.html'} is missing).\n"
            f"Build it once with:\n    {BUILD_HINT}")


def editor_url(port: int, job_id: Optional[str] = None) -> str:
    return f"http://{HOST}:{port}/" + (f"?job={job_id}" if job_id else "")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m web.local_ui",
                                description="Open the ART editor locally, without a login.")
    p.add_argument("--job", help="job id to open")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--database-url", help="default: local SQLite (~/.art/art.db); "
                   "'dotenv' takes .env's DATABASE_URL explicitly")
    p.add_argument("--user-id", help="profile to open; default: the active profile")
    p.add_argument("--allow-writes", action="store_true",
                   help="allow writes to a remote or Postgres database")
    p.add_argument("--no-open", action="store_true", help="do not open a browser")
    return p


def _pin_database(database_url: Optional[str], allow_writes: bool) -> bool:
    """Pin DATABASE_URL before any config/database import. Returns writability."""
    from harness.runtime import resolve_database_url, writes_allowed

    url = resolve_database_url(database_url, os.environ, allow_writes=allow_writes)
    os.environ["DATABASE_URL"] = url
    if url.startswith("sqlite:///"):
        Path(url[len("sqlite:///"):]).parent.mkdir(parents=True, exist_ok=True)
    return writes_allowed(url, allow_writes)


def _resolve_user(user_id: Optional[str], writable: bool):
    """`--user-id`, else the active profile, else (writable stores only) the
    CLI's default profile. Returns the User or raises SystemExit."""
    from uuid import UUID

    from sqlmodel import Session

    import database.db as _db
    from database.models import User
    from database.user_utils import get_active_profile, get_or_create_cli_user

    if user_id:
        try:
            uid = UUID(user_id)
        except ValueError:
            raise SystemExit(f"art ui: --user-id is not a UUID: {user_id}")
        with Session(_db.engine) as session:
            user = session.get(User, uid)
        if user is None:
            raise SystemExit(f"art ui: no user {user_id} in this database")
        return user
    user = get_active_profile()
    if user is not None:
        return user
    if not writable:
        raise SystemExit("art ui: no active profile, and this database is read-only; "
                         "pass --user-id")
    return get_or_create_cli_user()


def main(argv: Optional[Sequence[str]] = None, static_dir: Path = STATIC_DIR) -> int:
    args = build_parser().parse_args(argv)
    problem = missing_static(static_dir)
    if problem:
        print(problem, file=sys.stderr)
        return 2

    writable = _pin_database(args.database_url, args.allow_writes)
    os.environ["ART_LOCAL_UI"] = "1"
    if not writable:
        os.environ["ART_LOCAL_UI_READ_ONLY"] = "1"

    if writable:
        from database.db import init_db
        init_db()  # the user lookup below needs the tables on a fresh store
    user = _resolve_user(args.user_id, writable)
    os.environ["ART_LOCAL_UI_USER_ID"] = str(user.user_id)

    import uvicorn

    from web.app import create_app

    app = create_app()
    url = editor_url(args.port, args.job)
    print(f"art ui: {url}  (user {user.name or user.email or user.user_id}; "
          f"{'writable' if writable else 'read-only'} store; Ctrl+C to stop)", flush=True)
    if not args.no_open:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    uvicorn.run(app, host=HOST, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
