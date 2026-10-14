"""The `art` console script (#194): one command for the tools and the editor.

    art --list                                   # the tool contract
    art kg_search --args '{"query": "python"}'   # any tool, JSON out
    art mcp                                      # the MCP server over stdio (what art-mcp runs)
    art ui --job <job_id>                        # the local editor (needs [ui])
    art hook user-prompt < hook.json             # Claude Code plugin hooks (#201)
    art hooks codex --write                      # the file fallback for Codex without plugin hooks (#203, #290)
    art jev status|export|import                 # the Jev decision cache (#193)
    art export --out backup.zip                  # back up a profile (#195)
    art import backup.zip                        # restore it into the local store
    art import --from-supabase --source-url ... --user-id ...   # one-time web-app migration

`art ui` hands off to `web.local_ui`, imported only when asked for, so the
tool path never loads the web stack. `art mcp` and the `art-mcp` script both run
`harness.mcp_server:main`; the plugin uses `art mcp` so it needs only one entry
point (#256).
"""

from __future__ import annotations

import sys
from typing import Optional, Sequence


def _ui_extra_hint() -> str:
    from harness.hooks import SOURCE
    return ("art ui needs the editor's server: install it with the [ui] extra, e.g.\n"
            f'    uvx --from "{SOURCE}" art ui')


def main(argv: Optional[Sequence[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["hook"]:
        # A hook must never block the user's message, so not even an import
        # failure may escape (#256).
        try:
            from harness.hooks import main as hook_main
            return hook_main(argv[1:])
        except BaseException as exc:                  # noqa: BLE001
            print(f"art hook: {exc}", file=sys.stderr)
            return 0
    if argv[:1] == ["mcp"]:
        from harness.mcp_server import main as mcp_main
        mcp_main(argv[1:])
        return 0
    if argv[:2] == ["hooks", "codex"]:
        from harness.hooks import codex_main
        return codex_main(argv[2:])
    if argv[:1] == ["jev"]:
        from harness.decisions.recordings import main as jev_main
        return jev_main(argv[1:])
    if argv[:1] in (["export"], ["import"]):
        from harness.export_import import main as port_main
        return port_main(argv)
    if argv[:1] == ["ui"]:
        try:
            import fastapi  # noqa: F401
            import uvicorn  # noqa: F401
        except ImportError:
            print(_ui_extra_hint(), file=sys.stderr)
            return 2
        from web.local_ui import main as ui_main
        return ui_main(argv[1:])
    from harness.cli import main as cli_main
    return cli_main(argv)


if __name__ == "__main__":
    sys.exit(main())
