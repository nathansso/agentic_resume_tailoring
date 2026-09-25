"""`art-mcp`: the stdio MCP server over the harness tool contract (#189, #191).

Run it as `python -m harness.mcp_server` from the repo root, or by absolute
script path from anywhere (the repo root is put on `sys.path`):

    claude mcp add art -- <repo>/.venv/Scripts/python.exe <repo>/harness/mcp_server.py

Every tool comes from `harness.contract.TOOLS`; this module only adapts them to
MCP, so the server publishes each tool's input *and* output JSON schema and
validates every result against it. Database and user resolution live in
`harness.runtime` (shared with `harness.cli`): local SQLite unless
`--database-url` says otherwise, `.env` never read implicitly, Postgres forced
read-only.

stdout carries the protocol, so logging goes to stderr.
"""

from __future__ import annotations

import argparse
import inspect
import logging
import sys
from pathlib import Path
from typing import Annotated

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harness.contract import CONTRACT_VERSION, TOOLS, ToolSpec, invoke  # noqa: E402
from harness.runtime import bootstrap, force_read_only, resolve_database_url  # noqa: E402,F401

TOOL_NAMES = tuple(t.name for t in TOOLS)

log = logging.getLogger("art-mcp")


def _adapter(spec: ToolSpec, user_id):
    """An MCP-shaped function for one contract tool.

    MCP derives the input schema from the function signature and the output
    schema from the return annotation, so both are built from the contract's
    models rather than written a second time.
    """
    def call(**kwargs):
        return spec.output_model.model_validate(invoke(spec.name, user_id, kwargs))

    params = []
    for name, field in spec.input_model.model_fields.items():
        annotation = Annotated[field.annotation, field]
        default = inspect.Parameter.empty if field.is_required() else field.get_default(
            call_default_factory=True)
        params.append(inspect.Parameter(name, inspect.Parameter.KEYWORD_ONLY,
                                        default=default, annotation=annotation))
    call.__signature__ = inspect.Signature(params, return_annotation=spec.output_model)
    call.__annotations__ = {p.name: p.annotation for p in params}
    call.__annotations__["return"] = spec.output_model
    call.__name__ = spec.name
    call.__doc__ = spec.description
    return call


def build_server(user_id=None):
    """Register every contract tool. `user_id` None → tools return `no_user`."""
    from mcp.server.mcpserver import MCPServer
    from mcp.types import ToolAnnotations

    server = MCPServer(
        name="art",
        version=CONTRACT_VERSION,
        instructions=(
            "ART's knowledge graph. Call art_briefing first and honour its pins verbatim; "
            "use get_profile for the header, list_items or kg_search to find items, and "
            "get_item for full records. Never state a fact about the candidate that these "
            "tools do not return."
        ),
    )
    for spec in TOOLS:
        server.add_tool(
            _adapter(spec, user_id), name=spec.name, description=spec.description,
            annotations=ToolAnnotations(read_only_hint=spec.read_only,
                                        destructive_hint=False,
                                        idempotent_hint=spec.read_only),
            structured_output=True)
    return server


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="art-mcp", description=__doc__.splitlines()[0])
    parser.add_argument("--database-url", help="DB URL, or 'dotenv' to use .env's DATABASE_URL")
    parser.add_argument("--user-id", help="ART user id (default: ~/.art pointer file)")
    args = parser.parse_args(argv)

    logging.basicConfig(stream=sys.stderr, level=logging.INFO,
                        format="art-mcp %(levelname)s %(message)s")
    user_id = bootstrap(args.database_url, args.user_id)
    import os
    scheme = os.environ["DATABASE_URL"].split(":", 1)[0]
    log.info("contract v%s: serving %s (%s), user %s", CONTRACT_VERSION,
             ", ".join(TOOL_NAMES), scheme, user_id or "UNBOUND")
    build_server(user_id).run("stdio")


if __name__ == "__main__":
    main()
