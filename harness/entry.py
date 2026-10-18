"""The `artie` console script (#194): one command for the tools and the editor.

    artie --list                                   # the tool contract
    artie kg_search --args '{"query": "python"}'   # any tool, JSON out
    artie mcp                                      # the MCP server over stdio (what artie-mcp runs)
    artie ui --job <job_id>                        # the local editor (needs [ui])
    artie hook user-prompt < hook.json             # Claude Code plugin hooks (#201)
    artie hooks codex --write                      # the file fallback for Codex without plugin hooks (#203, #290)
    artie jev status|export|import                 # the Jev decision cache (#193)
    artie github login|logout|status               # GitHub device-flow sign-in (#353)
    artie export --out backup.zip                  # back up a profile (#195)
    artie import backup.zip                        # restore it into the local store
    artie migrate preferences [--dry-run]          # anchor old preference rows; section rows become rules (#300)
    artie import --from-supabase --source-url ... --user-id ...   # one-time web-app migration

`artie ui` hands off to `web.local_ui`, imported only when asked for, so the
tool path never loads the web stack. `artie mcp` and the `artie-mcp` script both run
`harness.mcp_server:main`; the plugin uses `artie mcp` so it needs only one entry
point (#256).

Bare `artie` (or `artie --help`) prints `HELP`: what to ask the agent, then the few
commands a user runs themselves (#395). The plumbing (`mcp`, `hook`, `hooks`, `jev`,
`migrate`) is left out of it on purpose, and still works.
"""

from __future__ import annotations

import sys
from typing import Optional, Sequence

# Every example request must work with today's tools (root CLAUDE.md: don't teach a capability
# the code doesn't expose). "Save the PDF to my Downloads" comes back with #394 (`save_to`).
HELP = """\
artie tailors your resume to a job posting from your own knowledge graph.
Your coding agent (Claude Code, Codex) does the work; just ask it.

Things to ask your agent:
  "Tailor my resume to this posting: <paste the posting or a link>"
  "Revise the second bullet under my latest job to lead with what I built"
  "What's in my knowledge graph about Python?"
  "Show my jobs"
  "Go back to the previous version of my resume for <company>"

First time? In Claude Code run /artie:setup (in Codex, $artie-setup) to build your graph.

Commands you run yourself:
  artie ui [--job <job_id>]            open the local editor in your browser
  artie github login|status|logout     sign in to GitHub so your agent can read your repos
  artie export --out <file>.zip        back up your profile
  artie import <file>.zip              restore a backup
  artie --list                         the tools your agent can call, as JSON
"""

_HELP_ARGS = ([], ["--help"], ["-h"], ["help"])


def _ui_extra_hint() -> str:
    from harness.hooks import UVX
    return ("artie ui needs the editor's server: install it with the [ui] extra, e.g.\n"
            f"    {UVX} artie ui")


def main(argv: Optional[Sequence[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv in _HELP_ARGS:
        sys.stdout.write(HELP)
        return 0
    if argv[:1] == ["hook"]:
        # A hook must never block the user's message, so not even an import
        # failure may escape (#256).
        try:
            from harness.hooks import main as hook_main
            return hook_main(argv[1:])
        except BaseException as exc:                  # noqa: BLE001
            print(f"artie hook: {exc}", file=sys.stderr)
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
    if argv[:1] == ["migrate"]:
        from harness.migrations import main as migrate_main
        return migrate_main(argv[1:])
    if argv[:1] == ["github"]:
        from harness.github_cli import main as github_main
        return github_main(argv[1:])
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
