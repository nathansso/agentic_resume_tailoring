"""The `art` console script (#194): one command for the tools and the editor.

    art --list                                   # the tool contract
    art kg_search --args '{"query": "python"}'   # any tool, JSON out
    art ui --job <job_id>                        # the local editor (needs [ui])
    art hook user-prompt < hook.json             # Claude Code plugin hooks (#201)

`art ui` hands off to `web.local_ui`, imported only when asked for, so the
tool path never loads the web stack. `art-mcp` is `harness.mcp_server:main`.
"""

from __future__ import annotations

import sys
from typing import Optional, Sequence

UI_EXTRA_HINT = ("art ui needs the editor's server: install it with the [ui] extra, "
                 "e.g. uvx --from 'art-mcp[ui]' art ui")


def main(argv: Optional[Sequence[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["hook"]:
        from harness.hooks import main as hook_main
        return hook_main(argv[1:])
    if argv[:1] == ["ui"]:
        try:
            import fastapi  # noqa: F401
            import uvicorn  # noqa: F401
        except ImportError:
            print(UI_EXTRA_HINT, file=sys.stderr)
            return 2
        from web.local_ui import main as ui_main
        return ui_main(argv[1:])
    from harness.cli import main as cli_main
    return cli_main(argv)


if __name__ == "__main__":
    sys.exit(main())
