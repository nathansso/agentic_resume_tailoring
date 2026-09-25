"""The harness tool contract as a CLI with JSON output (issue #191).

    python -m harness.cli --list
    python -m harness.cli kg_search --args '{"query": "python"}'
    python -m harness.cli get_profile --user-id <uuid> --database-url dotenv

Prints exactly one JSON document on stdout: the tool's output model (errors
included, as the `error` field), or with `--list` the whole contract. Exit
status is 0 for any result the contract can express, 2 for an unknown tool or
arguments that fail validation.

This is a separate entry point from the legacy `cli.py` on purpose: `cli.py`
imports `config` at module level, which loads `.env` and would capture a
production `DATABASE_URL` before this adapter could pin the database.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harness.contract import BY_NAME, CONTRACT_VERSION, ValidationError, describe, invoke  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="art", description="ART harness tools, JSON out.")
    p.add_argument("tool", nargs="?", help="Tool name (see --list).")
    p.add_argument("--args", default="{}", help="Tool arguments as a JSON object.")
    p.add_argument("--list", action="store_true", help="Print the tool contract.")
    p.add_argument("--user-id", help="ART user id (default: ~/.art pointer file)")
    p.add_argument("--database-url", help="DB URL, or 'dotenv' to use .env's DATABASE_URL")
    return p


def _emit(doc) -> None:
    sys.stdout.write(json.dumps(doc, sort_keys=True, ensure_ascii=False) + "\n")


def run(argv, *, user_id=None) -> int:
    """Execute against an already-configured database. Tests call this directly."""
    args = _parser().parse_args(argv)
    if args.list:
        _emit({"contract_version": CONTRACT_VERSION, "tools": describe()})
        return 0
    if args.tool not in BY_NAME:
        _emit({"error": {"code": "unknown_tool", "message": f"No tool {args.tool!r}.",
                         "suggestions": sorted(BY_NAME)}})
        return 2
    try:
        tool_args = json.loads(args.args)
        if not isinstance(tool_args, dict):
            raise ValueError("--args must be a JSON object")
        if args.user_id:
            from uuid import UUID
            user_id = UUID(args.user_id)
        _emit(invoke(args.tool, user_id, tool_args))
        return 0
    except (ValueError, ValidationError) as exc:
        _emit({"error": {"code": "invalid_arguments", "message": str(exc),
                         "suggestions": []}})
        return 2


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    user_id = None
    if not args.list:
        from harness.runtime import bootstrap
        user_id = bootstrap(args.database_url, args.user_id)
    return run(argv, user_id=user_id)


if __name__ == "__main__":
    sys.exit(main())
