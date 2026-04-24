# ART — Agentic Resume Tailoring

## Project overview
A local-first TUI application that ingests a user's resume, GitHub profile, and LinkedIn data into a knowledge graph, then tailors resumes to job descriptions via a chat interface.

**Stack:** Python, Textual (TUI), SQLite via SQLModel, LangGraph, LangChain, OpenAI / Anthropic.

**Entry points:**
- `python cli.py tui` or `python tui/app.py` — launch the TUI
- `python cli.py <command>` — CLI surface (do not break CLI when making TUI changes)

**Activate venv before running anything:**
```bash
source .venv/Scripts/activate   # bash
.venv\Scripts\Activate.ps1      # PowerShell
```

---

## Changelog and commit practices

These must be followed every time a PRD is completed or significant work lands.

### CHANGELOG.md
- Every completed PRD gets an entry at the **top** of `CHANGELOG.md` (above prior entries).
- Format mirrors existing entries exactly:
  ```
  ## PRD NN — Title
  **Status:** complete | **Tests:** N pass

  One-sentence summary of what the PRD solved.

  ### What shipped
  - `file/path.py` — what changed and why

  ### Deviations from spec
  - Any intentional divergences from the PRD, with the reason
  ```
- PRD files in `docs/prd/` are forward-looking specs — do not edit their task checklists retroactively; update only the `## Progress` section inside the PRD file itself.
- Update CHANGELOG immediately when a PRD is marked complete — do not batch.

### Commit messages
- Format: `type(scope): short description`
- Common types: `feat`, `fix`, `test`, `docs`, `refactor`
- Scope matches the PRD or subsystem: `prd-03`, `tui`, `chat`, `db`, `llm`
- Examples from this repo:
  - `feat(prd-03): onboarding screen, active profile, replace limit(1) user queries`
  - `test(chat): PRD-02.5 fast-path and service error handling tests`
  - `docs: mark PRD 02.5 complete`
- One commit per logical unit of work — do not bundle unrelated changes.
- Always run `python -m pytest test_smoke_formal.py -q` and confirm all tests pass before committing a PRD completion.

---

## Architecture notes

- `database/user_utils.py` — active profile stored at `~/.art/active_profile_id` (plain-text UUID). Use `get_active_profile()` everywhere; `get_or_create_default_user()` is a backward-compat wrapper only.
- `tui/services.py` — all DB access from the TUI goes through this module; widgets do not query the DB directly.
- `tui/screens/` — each major TUI screen is its own file.
- `agents/chat.py` — fast-path routing before LLM; check `SHORTCUTS` and `COMMAND_PHRASES` before adding new commands.
- Schema changes must be backward-compatible: new columns need defaults so existing `art.db` files continue to load.

---

## Testing

```bash
python -m pytest test_smoke_formal.py -q
```

All tests must pass before and after every change. Do not commit work that breaks the baseline.

### Test coverage requirement

**Every feature addition or behaviour change must ship with at least one new test.** This is not optional.

| Change type | Required test |
|---|---|
| New service function (`tui/services.py`) | Test the function directly with the `isolated_engine` fixture |
| New screen or screen redesign (`tui/screens/`) | Test that it mounts without error; test any validation logic |
| New chat routing / fast-path | Test that the correct response is returned without calling the LLM |
| New slash command | Test that it is handled locally (agent never called) and produces the expected side effect |
| Bug fix | Test that reproduces the bug before the fix (regression test) |
| DB model change | Test that old DBs load (backward-compat) and new fields are readable |

### How to write tests here

- All tests live in `test_smoke_formal.py`.
- Use the `isolated_engine` fixture for any test that touches the DB — it spins up a fresh in-memory SQLite DB and patches all module-level `engine` references.
- Access Static widget text via `str(widget._Static__content)` (not `.renderable` — that attribute does not exist in this Textual version).
- Wrap async Textual tests in a sync `asyncio.run(_run())` pattern, as all existing tests do.
- Mark slow/network tests with `@pytest.mark.integration` and `@pytest.mark.slow` so they are excluded from the default run.

### What counts as done

A task is not complete until:
1. The feature works.
2. At least one test covers the new behaviour.
3. `python -m pytest test_smoke_formal.py -q` passes in full.
