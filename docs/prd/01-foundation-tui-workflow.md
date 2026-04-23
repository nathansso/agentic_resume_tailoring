# PRD 01 — TUI Stabilization And Workflow Foundation

> **Prerequisite reading:** Read `tui/app.py` in full before touching anything.
> **Baseline test:** `python -m pytest test_smoke_formal.py -q` — all 3 must pass before and after your changes.
> **Scope:** The workflow is tool-centric. Make it user-centric. No rewrites, no new providers, no auth.

---

## Task Checklist

Work top to bottom. Mark each item `[x]` when done and record changed files in `## Progress`.

### 1 — Create `tui/services.py`
- [x] Extract all direct `Session(engine)` calls out of `tui/app.py` widget methods into service functions in a new file `tui/services.py`
- [x] Affected methods in `tui/app.py`: `_load_skills_table`, `_load_exp_table`, `_load_proj_table`, `_load_jobs_sidebar`, `_show_job_details`, `_load_graph_view`
- [x] Each service function takes a `user_id: UUID | None` and returns plain data (lists/dicts), not widgets
- [x] Widget methods call service functions and only handle rendering

### 2 — Add app state model
- [x] Add a `AppState` enum or string constant set to `tui/app.py` with values: `setup`, `profile_ready`, `job_selected`, `tailoring_complete`
- [x] `ArtApp` must track `self.app_state: str` initialized from DB on `on_mount`
- [x] Add a `_refresh_app_state()` method that reads DB and updates `self.app_state`
- [x] Call `_refresh_app_state()` after any ingestion, job creation, or tailoring action

### 3 — Add a status/dashboard panel
- [x] Add a `StatusBar` widget or a `Static` panel below the Header that shows the current state in plain English
- [x] Examples: `"No profile yet — press F1 to ingest your resume"`, `"Profile ready — select a job or press Ctrl+N to create one"`, `"Job selected: Senior Engineer @ Acme — press F3 to tailor"`
- [x] The panel must update when `self.app_state` changes
- [x] Do NOT remove the existing Header or Footer

### 4 — Fix empty states in data tables
- [x] In `_load_skills_table`, `_load_exp_table`, `_load_proj_table`: when no rows exist, add a placeholder row that says what's missing and what action to take (e.g. `"No skills found — ingest a resume via F1"`)
- [x] These messages must not look like errors

### 5 — Fix the `_load_graph_view` blocking call
- [x] `_load_graph_view` currently calls `SkillGraphBuilder().build_graph()` synchronously on the main thread during `on_mount`
- [x] Move it to a `@work(thread=True)` worker like `_run_chat` already does
- [x] Show a loading message in the graph log while it runs, replace with results on completion

### 6 — Make long-running actions report status
- [x] The `_run_chat` worker already uses `"Thinking..."` — extend this pattern
- [x] Any F-key action that triggers a background task must mount a `Static("Working...", classes="system-msg")` and remove it on completion
- [x] Failures must show user-language messages, not raw exception text

### 7 — Add tests
- [x] Add tests to `test_smoke_formal.py` for:
  - first-run empty state: skills/exp/proj tables show placeholder rows when DB is empty
  - `_refresh_app_state()` returns `"setup"` on empty DB and `"profile_ready"` when a user with skills exists
  - status panel text changes when state changes
- [x] All existing tests must still pass

---

## Key Files

| File | Role |
|---|---|
| `tui/app.py` | Main TUI — primary target for all changes |
| `tui/services.py` | **Create this** — DB queries extracted from widgets |
| `tui/widgets/__init__.py` | Currently empty — can add reusable widgets here if needed |
| `knowledge_graph/builder.py` | `SkillGraphBuilder.build_graph()` — read before threading it |
| `database/models.py` | Reference only — do not modify schema in this PRD |
| `test_smoke_formal.py` | Add new tests here |

---

## Do Not Touch

- `agents/` — no changes to any agent in this PRD
- `graph/pipeline.py` — no changes to the LangGraph pipeline
- `database/models.py` — no schema changes
- `config.py` — no provider changes
- `llm.py` — no provider changes
- `cli.py` — no CLI changes

---

## Constraints

1. Do not rewrite `tui/app.py` from scratch — make targeted edits
2. Do not change the visual layout (tab structure, sidebar, footer) — only add to it
3. The `@work(thread=True)` pattern is already used for `_run_chat` — follow that same pattern for any new background work
4. Windows terminal safety: use only ASCII in any new `print()` statements; rich markup in `RichLog` is fine
5. Do not add new pip dependencies
6. Keep each method under ~40 lines — if a method grows past that, extract a helper

---

## Verification

When all tasks are checked off, run:

```bash
python -m pytest test_smoke_formal.py -q
```

All tests must pass. If new tests are failing, fix them before marking this PRD complete.

Also manually launch and confirm the status panel shows the correct message for an empty-DB state:

```bash
python -m tui.app
```

---

## Background Context

<details>
<summary>Why this comes first</summary>

The repo already has a TUI, chat path, data tables, job sidebar, and visualization tab. The problem is not missing UI primitives — the workflow is tool-centric instead of user-centric. The app currently requires knowing hidden chat commands to accomplish basic tasks. Every later feature will compound this confusion if not fixed first.

Intended user flow after this PRD:
1. Open app → see clear profile status
2. If no profile: obvious call to action
3. Ingest data → status updates
4. Inspect skills and graph
5. Create/select job → tailoring visible from sidebar
6. Tailor → review matches and gaps
7. Refine via chat
</details>

