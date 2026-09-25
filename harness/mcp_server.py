"""`art-mcp`: the read-only stdio MCP server for the H0 spike (issue #189).

Run it as `python -m harness.mcp_server` from the repo root, or by absolute
script path from anywhere (the repo root is put on `sys.path`):

    claude mcp add art -- <repo>/.venv/Scripts/python.exe <repo>/harness/mcp_server.py

**Which database.** Local SQLite (`$ART_DATA_DIR/art.db`, default `~/.art/art.db`)
unless `--database-url` / `ART_MCP_DATABASE_URL` says otherwise. The URL is
resolved and written to `DATABASE_URL` *before* anything imports `config` or
`database`, so a `DATABASE_URL` sitting in `.env` can never be picked up
implicitly — `load_dotenv()` does not override a variable that is already set.
The literal value `dotenv` opts in to `.env`'s `DATABASE_URL` explicitly, which
keeps the secret out of the command line. Postgres sessions are forced
read-only by the server itself (`default_transaction_read_only`).

**Which user.** `--user-id` / `ART_MCP_USER_ID`, else the `~/.art` pointer file
read through `get_active_profile()`. The server never calls `init_db` or
`get_or_create_cli_user`, both of which write.

stdout carries the protocol, so logging goes to stderr.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Mapping, Optional
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TOOL_NAMES = ("art_briefing", "kg_search", "get_item")
_READ_ONLY_OPTION = "-c default_transaction_read_only=on"

log = logging.getLogger("art-mcp")


def _dotenv_database_url() -> Optional[str]:
    from dotenv import dotenv_values
    return dotenv_values(ROOT / ".env").get("DATABASE_URL")


def resolve_database_url(flag: Optional[str], env: Mapping[str, str],
                         dotenv_reader=_dotenv_database_url) -> str:
    """The URL this server reads from. Pure apart from the injectable `.env` reader.

    `env["DATABASE_URL"]` is deliberately ignored: only an explicit flag (or
    `ART_MCP_DATABASE_URL`) may point the server anywhere but local SQLite.
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
        return _force_read_only(choice)
    return choice


def _force_read_only(url: str) -> str:
    """Append `options=-c default_transaction_read_only=on` to a Postgres URL."""
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    opts = query.get("options", "")
    if "default_transaction_read_only" not in opts:
        query["options"] = f"{opts} {_READ_ONLY_OPTION}".strip()
    return urlunsplit(parts._replace(query=urlencode(query, quote_via=quote)))


def _jsonable(value: Any) -> Any:
    """Round-trip through JSON so datetimes/UUIDs/sets never break a response."""
    return json.loads(json.dumps(value, default=str))


def build_server(user_id=None):
    """Register the three read tools. `user_id` None → tools return an error."""
    from mcp.server.mcpserver import MCPServer
    from mcp.types import ToolAnnotations

    from harness import tools

    server = MCPServer(
        name="art",
        instructions=(
            "ART's knowledge graph, read-only. Call art_briefing first and honour its "
            "pins verbatim; use kg_search to find items and get_item for full records. "
            "Never state a fact about the candidate that these tools do not return."
        ),
    )
    ro = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True)
    missing = {"error": "no_user", "message": "No ART user is bound. Pass --user-id "
               "or set ART_MCP_USER_ID when starting art-mcp."}

    @server.tool(annotations=ro)
    def art_briefing(role_family: Optional[str] = None) -> dict:
        """Pinned preferences (verbatim), scoped preferences, persona traits and
        prior-job cards for this candidate. Call before planning any resume."""
        return missing if user_id is None else _jsonable(tools.art_briefing(user_id, role_family))

    @server.tool(annotations=ro)
    def kg_search(query: str, kinds: Optional[list[str]] = None, limit: int = 10) -> dict:
        """Search the candidate's knowledge graph. kinds ⊆ skill, experience,
        project, education, achievement. Returns stable keys for get_item."""
        if user_id is None:
            return missing
        return {"results": _jsonable(tools.kg_search(user_id, query, kinds, limit))}

    @server.tool(annotations=ro)
    def get_item(key: str) -> dict:
        """Full record for one key from kg_search (e.g. 'exp:<title>|<company>',
        'proj:<name>', 'skill:<name>'). Unknown keys return suggestions."""
        return missing if user_id is None else _jsonable(tools.get_item(user_id, key))

    return server


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="art-mcp", description=__doc__.splitlines()[0])
    parser.add_argument("--database-url", help="DB URL, or 'dotenv' to use .env's DATABASE_URL")
    parser.add_argument("--user-id", help="ART user id to read (default: ~/.art pointer file)")
    args = parser.parse_args(argv)

    logging.basicConfig(stream=sys.stderr, level=logging.INFO,
                        format="art-mcp %(levelname)s %(message)s")
    os.environ["DATABASE_URL"] = resolve_database_url(args.database_url, os.environ)

    from uuid import UUID

    from database.user_utils import get_active_profile, set_request_user

    raw = args.user_id or os.environ.get("ART_MCP_USER_ID")
    user_id = UUID(raw) if raw else getattr(get_active_profile(), "user_id", None)
    set_request_user(user_id)
    scheme = os.environ["DATABASE_URL"].split(":", 1)[0]
    log.info("serving %s (%s), user %s", ", ".join(TOOL_NAMES), scheme, user_id or "UNBOUND")
    build_server(user_id).run("stdio")


if __name__ == "__main__":
    main()
