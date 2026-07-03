# Project Guidelines

This file is a thin planning summary for VS Code tooling.

Authoritative guidance lives here:
- Root `CLAUDE.md` for repo-wide rules
- Local `agents/CLAUDE.md` and `web/CLAUDE.md` for folder-specific implementation rules
- Active PRDs in `docs/prd/` for task scope and acceptance

## Build and Test
- Activate `.venv` when present before running Python commands.
- Run `python run_tests.py` for behavior changes.
- Do not mark a task complete until the feature works, has a test, and the full test suite passes.

## Architecture
- Preserve the CLI surface in `cli.py` when changing the web app.
- Use `database/user_utils.py::get_active_profile()` for active-user lookups.
- Keep shared business logic in `services.py`; routers and agents call into it rather than querying the DB directly where a service already exists.
- Keep schema changes backward-compatible for existing local SQLite databases.

## Workflow
- Treat `docs/prd/` files as forward-looking specs; update only their `## Progress` sections while implementing.
- Add completed PRDs to the top of `CHANGELOG.md` immediately.
- Keep folder-specific detail in local `CLAUDE.md` files. Keep `.github/` instructions short and non-authoritative.

## Conventions
- Every behavior change needs a test in `test_smoke_formal.py`.
- Keep user-facing chat and tool errors in plain English.
- Do not teach prompts a capability that the codebase does not actually expose.