# TUI — Local Guidelines (DEPRECATED)

> **The TUI is deprecated.** The web app (https://artie-resume-tailoring.fly.dev/) is the primary implementation. TUI code is retained for reference only — do not add new features, screens, or tests here. All new work goes in `web/`.

Use this file for work under `tui/` if reading legacy code.

## Scope and precedence

- Root `CLAUDE.md` still controls repo-wide workflow, testing, and delivery rules.
- This file adds only `tui/`-specific implementation guidance.
- Use the active PRD for task acceptance and sequencing.

## Service boundaries

- TUI widgets and screens should use `tui/services.py` for database access.
- Keep UI logic, DB access, and long-running ingestion or tailoring work separated.
- New screens belong in `tui/screens/` unless there is a strong reason to keep them inline.

## Interaction rules

- Preserve the contract that agents and services return plain strings for chat-visible results.
- Handle clearly local UI commands in the TUI layer instead of bouncing them through the agent.
- Keep layout or styling changes separate from workflow logic when practical.

## Textual testing

- Use `asyncio.run(_run())` with `app.run_test()` for async Textual tests.
- Read `Static` widget content with `str(widget._Static__content)`.
- Add mount tests for new screens and regression tests for validation or command-routing behavior.

Keep this file focused on UI and service-boundary work. Do not duplicate generic repo workflow rules here.