# PRD 03 — Onboarding, Profile Ingestion, And Knowledge Graph UX

> **Prerequisite:** PRDs 01 and 02 must be complete.
> **Prerequisite reading:** Read `database/user_utils.py`, `database/models.py`, `tui/app.py`, `agents/parser.py`, `ingestion/resume.py`, and `ingestion/github.py` before starting.
> **Baseline test:** `python -m pytest test_smoke_formal.py -q` — all tests must pass before and after.
> **Core problem:** `database/user_utils.py::get_or_create_default_user()` creates a hardcoded default user with `email="user@example.com"`. Every `select(User).limit(1)` in the codebase is a single-user assumption. This PRD replaces that with an explicit active profile.

---

## Task Checklist

### 1 — Introduce explicit active profile model
- [ ] Add an `OnboardingStatus` column or separate `ProfileConfig` table to track which setup steps are done: `resume_ingested`, `github_ingested`, `linkedin_ingested`
- [ ] The simplest approach: add a `onboarding_complete: bool` and `onboarding_steps: dict` JSON column to the existing `User` model in `database/models.py`
- [ ] Add a `alembic`-free migration: drop and recreate the DB on first run OR use `SQLModel.metadata.create_all` with `checkfirst=True` (already used) — add the new columns as `Optional` with defaults so existing DBs don't break
- [ ] Update `database/user_utils.py`: replace `get_or_create_default_user()` with `get_active_profile() -> User | None` and `create_profile(name, email, github_username, linkedin_url) -> User`
- [ ] `get_active_profile()` reads from a local config file at `~/.art/active_profile_id` (plain text UUID). If the file does not exist or the UUID is not in DB, return `None`
- [ ] `create_profile(...)` creates the `User` row and writes the UUID to `~/.art/active_profile_id`

### 2 — Replace all `select(User).limit(1)` calls
- [ ] Search codebase: `grep -r "select(User).limit" .` — every hit must be replaced with `get_active_profile()`
- [ ] Affected files include: `tui/app.py` (via `tui/services.py` from PRD 01), `agents/chat.py` (all tool functions), `graph/pipeline.py` (`get_or_create_default_user`)
- [ ] Each caller must handle `None` gracefully — if no active profile, return a user-facing message like `"No active profile. Complete onboarding first."`

### 3 — Build onboarding screen in TUI
- [ ] Create `tui/screens/onboarding.py` with a Textual `Screen` subclass `OnboardingScreen`
- [ ] The screen must collect in a single form: `Name`, `Email`, `Resume file path` (required), `GitHub username` (optional), `LinkedIn URL` (optional)
- [ ] On submit: validate name/email/resume path not empty, validate resume file exists
- [ ] On valid submit: call `create_profile(...)` then trigger resume ingestion as a background worker
- [ ] Show progress during ingestion: `"Parsing resume..."` → `"Extracting skills..."` → `"Done — profile created"`
- [ ] On completion: push back to the main `ArtApp` screen and call `_refresh_app_state()`
- [ ] `ArtApp.on_mount` must call `get_active_profile()` and if `None`, push `OnboardingScreen`

### 4 — GitHub ingestion from onboarding
- [ ] If GitHub username was provided in onboarding, offer a button `"Ingest GitHub repos"` on the main screen after resume ingestion completes (not blocking onboarding)
- [ ] Clicking it runs `ingestion/github.py` ingestion as a background worker with progress messages
- [ ] Reuse `cmd_ingest_github` logic from `cli.py` — extract it into `tui/services.py` as `ingest_github_for_profile(user_id, username)`

### 5 — Improve knowledge graph view
- [ ] In `tui/app.py` `_load_graph_view` (already moved to a background worker in PRD 01), replace the raw output with a structured summary:
  - Section: `Top Skills` — top 10 by edge count (number of projects/experiences that use them)
  - Section: `By Category` — count skills per category
  - Section: `Evidence` — for each of top 5 skills, list which projects/experiences reference them
- [ ] The output must read like a summary report, not a raw node/edge dump
- [ ] Add a `get_graph_summary(user_id) -> dict` function to `tui/services.py` that calls `SkillGraphBuilder` and returns structured data

### 6 — Add tests
- [ ] Add to `test_smoke_formal.py`:
  - `test_get_active_profile_returns_none_on_empty_db`
  - `test_create_profile_persists_and_loads` — create, then re-load via `get_active_profile()`
  - `test_onboarding_screen_mounts` — `OnboardingScreen` composes without error using `run_test()`
  - `test_graph_summary_returns_structure` — `get_graph_summary(user_id)` returns dict with `top_skills`, `by_category`, `evidence` keys

---

## Key Files

| File | Role |
|---|---|
| `database/user_utils.py` | Replace `get_or_create_default_user` with `get_active_profile` / `create_profile` |
| `database/models.py` | Add `onboarding_complete` and `onboarding_steps` to `User` |
| `tui/screens/onboarding.py` | **Create this** — onboarding `Screen` |
| `tui/app.py` | Call `get_active_profile()` on mount; push onboarding screen if `None` |
| `tui/services.py` | Add `ingest_github_for_profile`, `get_graph_summary` |
| `agents/chat.py` | All tool functions that call `select(User).limit(1)` |
| `graph/pipeline.py` | `get_or_create_default_user` call |
| `ingestion/github.py` | Referenced by new service function — read before wrapping |
| `test_smoke_formal.py` | Add new tests |

---

## Do Not Touch

- `ingestion/linkedin.py` — LinkedIn ingestion is optional in this PRD; expose it as a post-onboarding option only
- `agents/tailor.py` — no tailoring changes
- `graph/pipeline.py` node implementations — only update the `get_or_create_default_user` call
- `llm.py` — no model changes
- `cli.py` — no CLI changes (the CLI is a separate surface)

---

## Constraints

1. Do not require internet access during onboarding — resume ingestion must work offline
2. GitHub ingestion is optional — onboarding must complete successfully without it
3. The `~/.art/` directory is the active profile store — use `pathlib.Path.home() / ".art"` to construct paths
4. Do not break the existing CLI — `cli.py` commands must still work (they will call `get_active_profile()` instead of `get_or_create_default_user()` and handle `None` gracefully)
5. Do not add new pip dependencies
6. Schema changes must be backward-compatible: new columns must have defaults so an existing `art.db` continues to load

---

## Verification

```bash
python -m pytest test_smoke_formal.py -q
```

Then do a manual first-run test:

```bash
# Delete the active profile pointer if it exists
rm -f ~/.art/active_profile_id
# Launch TUI — should show OnboardingScreen
python -m tui.app
```

Complete onboarding with the resume file `"Nathaniel Oliver Resume - 3_27_6.md"`. Verify the graph view shows a structured summary after completion.

---

## Background Context

<details>
<summary>The single-user problem</summary>

The entire codebase currently behaves as a single-user prototype. `database/user_utils.py::get_or_create_default_user()` creates a record with `email="user@example.com"` on first call. Every query that says `select(User).limit(1)` returns whoever was inserted first. This is not a multi-user problem yet — it is a "no user identity" problem. The fix here is not to build auth; it is to introduce an explicit local profile that persists between sessions.
</details>

---

## Progress

> Claude Code: update this section as you work. Do not delete unchecked items.

**Status:** `not started`

### Files Modified
_None yet_

### Files Created
_None yet_

### Completed Tasks
_None yet_

### Notes / Deviations
_Any decisions made that differ from the spec above_