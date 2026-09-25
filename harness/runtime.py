"""Process bootstrap shared by every harness entry point (#189, #191).

Two things must happen before anything imports `config` or `database`:
`DATABASE_URL` is pinned to the database this process may read, and only then
is the user resolved. `config.load_dotenv()` never overrides a variable that is
already set, so pinning first is what keeps a production `DATABASE_URL` in
`.env` from ever being picked up implicitly.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping, Optional
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent.parent
_READ_ONLY_OPTION = "-c default_transaction_read_only=on"


def _dotenv_database_url() -> Optional[str]:
    from dotenv import dotenv_values
    return dotenv_values(ROOT / ".env").get("DATABASE_URL")


def resolve_database_url(flag: Optional[str], env: Mapping[str, str],
                         dotenv_reader=_dotenv_database_url) -> str:
    """The URL this process reads from. Pure apart from the injectable `.env` reader.

    `env["DATABASE_URL"]` is deliberately ignored: only an explicit flag (or
    `ART_MCP_DATABASE_URL`) may point ART anywhere but local SQLite. The literal
    `dotenv` opts in to `.env`'s `DATABASE_URL` explicitly. Postgres sessions are
    forced read-only.
    """
    choice = flag or env.get("ART_MCP_DATABASE_URL")
    if choice == "dotenv":
        choice = dotenv_reader()
        if not choice:
            raise SystemExit("--database-url dotenv: no DATABASE_URL in .env")
    if not choice:
        data_dir = Path(env.get("ART_DATA_DIR") or Path.home() / ".art")
        return f"sqlite:///{data_dir / 'art.db'}"
    if choice.startswith(("postgres://", "postgresql://", "postgresql+")):
        return force_read_only(choice)
    return choice


def force_read_only(url: str) -> str:
    """Append `options=-c default_transaction_read_only=on` to a Postgres URL."""
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    opts = query.get("options", "")
    if "default_transaction_read_only" not in opts:
        query["options"] = f"{opts} {_READ_ONLY_OPTION}".strip()
    return urlunsplit(parts._replace(query=urlencode(query, quote_via=quote)))


def bootstrap(database_url: Optional[str], user_id: Optional[str]):
    """Pin the database, then resolve and bind the user. Returns the UUID or None.

    The user is `--user-id` / `ART_MCP_USER_ID`, else the `~/.art` pointer file
    read through `get_active_profile()`. Nothing here writes: no `init_db`, no
    `get_or_create_cli_user`.
    """
    os.environ["DATABASE_URL"] = resolve_database_url(database_url, os.environ)

    from uuid import UUID

    from database.user_utils import get_active_profile, set_request_user

    raw = user_id or os.environ.get("ART_MCP_USER_ID")
    uid = UUID(raw) if raw else getattr(get_active_profile(), "user_id", None)
    set_request_user(uid)
    return uid
