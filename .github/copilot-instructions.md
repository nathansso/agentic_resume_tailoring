# Project Guidelines

This file is a thin planning summary for VS Code tooling.

Authoritative guidance lives here:
- Root `CLAUDE.md` for repo-wide rules
- Local `agents/CLAUDE.md` and `tui/CLAUDE.md` for folder-specific implementation rules
- Active PRDs in `docs/prd/` for task scope and acceptance

## Build and Test
- Activate `.venv` when present before running Python commands.
- Run `python -m pytest test_smoke_formal.py -q` for behavior changes.
- Do not mark a task complete until the feature works, has a test, and the full smoke test file passes.

## Architecture
- Preserve the CLI surface in `cli.py` when changing the TUI.
- Use `database/user_utils.py::get_active_profile()` for active-user lookups.
- Keep TUI database access in `tui/services.py`; widgets and screens should not query the DB directly.
- Keep schema changes backward-compatible for existing local SQLite databases.

## Workflow
- Treat `docs/prd/` files as forward-looking specs; update only their `## Progress` sections while implementing.
- Add completed PRDs to the top of `CHANGELOG.md` immediately.
- Keep folder-specific detail in local `CLAUDE.md` files. Keep `.github/` instructions short and non-authoritative.

## Conventions
- Every behavior change needs a test in `test_smoke_formal.py`.
- Keep user-facing chat and tool errors in plain English.
- Do not teach prompts a capability that the codebase does not actually expose.