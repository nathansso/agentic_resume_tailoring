# ART — Repository Guidelines

## Project overview
ART is a local-first TUI that ingests resume, GitHub, and LinkedIn data into a knowledge graph, then tailors resumes to job descriptions through chat and workflow tools.

**Stack:** Python, Textual, SQLite via SQLModel, LangGraph, LangChain, OpenAI / Anthropic.

**Entry points:**
- `python cli.py tui` or `python tui/app.py` — launch the TUI
- `python cli.py <command>` — CLI surface

**Environment:**
```bash
source .venv/Scripts/activate   # bash
.venv\Scripts\Activate.ps1      # PowerShell
```

Do not break the CLI when making TUI changes.

---

## Always-on rules

- Keep changes scoped to the requested task. Avoid unrelated refactors.
- Preserve backward compatibility for user data and the SQLite schema.
- Every behavior change must ship with at least one test.
- Generated artifacts like `tailored_output.json` and `tailored_resume.md` are outputs, not source files.
- Keep root guidance stable. Folder-specific implementation rules belong in local `CLAUDE.md` files.

---

## Guidance hierarchy

Use the repo guidance in this order:

1. The active PRD in `docs/prd/` defines task scope, sequencing, and acceptance criteria.
2. This root `CLAUDE.md` defines stable repo-wide workflow, testing, and architecture rules.
3. Local `CLAUDE.md` files add folder-specific implementation constraints without replacing the root rules.
4. `.github/` Copilot instruction files are thin planning aids and should not become a second policy system.

Keep these roles separate:
- PRDs are task specs.
- Root `CLAUDE.md` is the repo policy surface.
- Local `CLAUDE.md` files are implementation-local supplements.
- `.github/` instruction files mirror the essentials for VS Code tooling.

---

## Work tracking

- Use the PRDs in `docs/prd/` as forward-looking specs.
- Do not retroactively edit PRD task checklists. Update only the `## Progress` section while work is in flight.
- When a PRD is complete, add a new top entry to `CHANGELOG.md` immediately.
- Keep one logical unit of work per commit.

**Commit format:** `type(scope): short description`

Examples:
- `feat(prd-03): onboarding screen and active profile flow`
- `test(chat): fast-path routing regression coverage`
- `docs: mark PRD 02.5 complete`

---

## Architecture invariants

- Use `database/user_utils.py::get_active_profile()` for active user lookups. `get_or_create_default_user()` exists only as backward-compat support.
- All TUI database access goes through `tui/services.py`. Widgets and screens should not query the database directly.
- Keep schema changes backward-compatible with defaults so existing local DBs still load.
- Keep chat/router changes aligned with actual product capabilities. Do not teach prompts a capability that code does not expose.
- Prefer adding focused local guidance instead of expanding this file with volatile implementation details.

For folder-specific rules:
- See `agents/CLAUDE.md` for chat routing, prompt, and tool-calling work.
- See `tui/CLAUDE.md` for Textual UI, screen, and service-boundary work.

When adding more guidance, prefer updating one of those local files over expanding this file with transient implementation detail.

---

## Testing

Run this before and after behavior changes:

```bash
python -m pytest test_smoke_formal.py -q
```

Testing conventions:
- All tests live in `test_smoke_formal.py`.
- Use the `isolated_engine` fixture for DB-related tests.
- Wrap async Textual tests in `asyncio.run(_run())`.
- Access Textual `Static` content with `str(widget._Static__content)`.
- Mark slow or network-dependent tests with `@pytest.mark.integration` and `@pytest.mark.slow`.

Definition of done:
1. The feature works.
2. At least one test covers the new behavior.
3. `python -m pytest test_smoke_formal.py -q` passes in full.
