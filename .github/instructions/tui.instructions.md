---
description: "Use when editing Textual screens, slash commands, chat UI flow, or service boundaries under tui/**/*.py."
name: "ART TUI"
applyTo: "tui/**/*.py"
---
# TUI Guidelines

This file complements root and local `CLAUDE.md` guidance for VS Code tooling. Keep it short and scoped to `tui/` work.

- Keep database access in `tui/services.py`; widgets and screens should not query the DB directly.
- Move long-running or IO-heavy work off the main UI thread.
- Preserve the separation between local UI commands and agent-routed chat behavior.
- New screens should live in `tui/screens/` unless there is a strong reason not to.
- Textual tests should use `asyncio.run(_run())` with `app.run_test()`.
- Read `Static` content with `str(widget._Static__content)` in tests.