# PRD 05 — Desktop Productization, Cloud Models, And Data Security

> **Prerequisite:** PRDs 01–04 must be complete.
> **Prerequisite reading:** Read `config.py`, `database/db.py`, `requirements.txt`, and `database/user_utils.py` before starting.
> **Baseline test:** `python -m pytest test_smoke_formal.py -q` — all tests must pass before and after.
> **Goal:** Make ART installable from GitHub and launchable from the Windows desktop. Move secrets out of code. Validate configuration at startup. Make SQLite the explicit and clean default.

---

## Task Checklist

### 1 — Centralize app data directory
- [ ] Define `APP_DATA_DIR = Path.home() / ".art"` in `config.py`
- [ ] Define subdirectories: `APP_DATA_DIR / "exports"`, `APP_DATA_DIR / "uploads"`, `APP_DATA_DIR / "logs"`
- [ ] Add a `ensure_app_dirs()` function to `config.py` that creates all subdirectories if they don't exist
- [ ] Move the SQLite DB file from `{project_root}/art.db` to `APP_DATA_DIR / "art.db"` — update `DATABASE_URL` in `config.py`
- [ ] Update `~/.art/active_profile_id` path (already set in PRD 03 if that PRD used this location — confirm and consolidate)
- [ ] Call `ensure_app_dirs()` at startup in `tui/app.py` `on_mount` and in `cli.py` main entry

### 2 — Startup configuration validation
- [ ] Create `config_validator.py` in the project root
- [ ] `validate_config() -> list[str]` returns a list of human-readable error strings (empty list = all good)
- [ ] Checks to implement:
  - `LLM_PROVIDER` is `"openai"` or `"ollama"`
  - If `LLM_PROVIDER == "openai"`: `OPENAI_API_KEY` is set and non-empty
  - If `LLM_PROVIDER == "ollama"`: Ollama base URL is reachable (simple `requests.get` with 2s timeout, catch all exceptions)
  - `APP_DATA_DIR` is writable
  - `DATABASE_URL` path is reachable/creatable
- [ ] In `tui/app.py` `on_mount`, call `validate_config()` first. If errors exist, show them in the status panel as `"[CONFIG ERROR] {message}"` and disable ingestion/tailoring actions
- [ ] In `cli.py`, call `validate_config()` at the top of each command and print errors with `sys.exit(1)` if critical ones exist

### 3 — Move secrets to `.env` with a clear user-facing setup doc
- [ ] Audit every place in the codebase where secrets are used: `OPENAI_API_KEY`, `GITHUB_TOKEN`, `ANTHROPIC_API_KEY`
- [ ] Confirm none of these are ever logged at `INFO` or higher level — add a test for this
- [ ] Create `.env.example` in the project root with all recognized env vars and comments explaining each
- [ ] `config.py` must import only from `os.getenv` — no hardcoded secrets anywhere
- [ ] Add `.env` to `.gitignore` if not already present

### 4 — Create Windows desktop launcher
- [ ] Create `launch.bat` in the project root:
  ```batch
  @echo off
  cd /d "%~dp0"
  if exist .venv\Scripts\activate.bat (
      call .venv\Scripts\activate.bat
  )
  python -m tui.app
  pause
  ``
- [ ] Create `launch.ps1` in the project root as a PowerShell alternative:
  ```powershell
  Set-Location $PSScriptRoot
  if (Test-Path ".venv\Scripts\Activate.ps1") {
      & ".venv\Scripts\Activate.ps1"
  }
  python -m tui.app
  ``
- [ ] Both launchers must work from the project root with the `.venv` present
- [ ] Do not create a compiled executable or installer — these are script launchers only

### 5 — Create `INSTALL.md` for GitHub README
- [ ] Write `INSTALL.md` covering:
  - Requirements: Python 3.11+, Windows (primary), macOS/Linux (best effort)
  - Step 1: Clone repo
  - Step 2: `python -m venv .venv` and activate
  - Step 3: `pip install -r requirements.txt`
  - Step 4: Copy `.env.example` to `.env` and fill in `OPENAI_API_KEY`
  - Step 5: Run `launch.bat` (Windows) or `python -m tui.app`
  - Troubleshooting: common errors and their fixes
- [ ] Keep it under 60 lines — clarity over completeness

### 6 — DB path migration for existing users
- [ ] If `{project_root}/art.db` exists but `~/.art/art.db` does not, automatically copy it on first startup and log a notice: `"Migrated art.db to ~/.art/art.db"`
- [ ] Implement this in `database/db.py` before creating the engine
- [ ] After migration, the old `art.db` in the project root can remain as a backup — do not delete it automatically

### 7 — Add tests
- [ ] Add to `test_smoke_formal.py`:
  - `test_validate_config_catches_missing_api_key` — monkeypatch `OPENAI_API_KEY` to empty string, verify `validate_config()` returns a non-empty error list
  - `test_ensure_app_dirs_creates_directories` — call `ensure_app_dirs()` with a tmp path, verify subdirs exist
  - `test_no_secrets_in_logs` — capture log output during `get_llm()` initialization and assert `OPENAI_API_KEY` value is not in log output

---

## Key Files

| File | Role |
|---|---|
| `config.py` | Add `APP_DATA_DIR`, `ensure_app_dirs()`, update `DATABASE_URL`, add `ANTHROPIC_API_KEY` stub |
| `config_validator.py` | **Create this** — `validate_config()` function |
| `database/db.py` | Update engine to use new `DATABASE_URL`; add migration logic |
| `tui/app.py` | Call `ensure_app_dirs()` and `validate_config()` in `on_mount` |
| `cli.py` | Call `validate_config()` at entry of each command |
| `launch.bat` | **Create this** — Windows launcher |
| `launch.ps1` | **Create this** — PowerShell launcher |
| `.env.example` | **Create this** — template for user secrets |
| `INSTALL.md` | **Create this** — install guide |
| `test_smoke_formal.py` | Add new validation tests |

---

## Do Not Touch

- `agents/` — no agent changes
- `graph/pipeline.py` — no pipeline changes
- `database/models.py` — no schema changes
- `ingestion/` — no ingestion changes
- Any file under `tui/screens/` — no TUI screen changes

---

## Constraints

1. Do not add Supabase — SQLite is the correct data store for a local-first v1 product
2. Do not build a compiled `.exe` — script launchers are sufficient
3. Do not use OS keychain libraries (keyring, etc.) — `.env` file is the acceptable secrets approach for this product stage
4. Do not add new pip dependencies unless one is strictly required for `config_validator.py` (it should not need any)
5. The DB migration is one-directional and non-destructive — old file is preserved
6. `INSTALL.md` must be accurate — test every step before marking this task done

---

## Verification

```bash
python -m pytest test_smoke_formal.py -q
```

Then verify the launcher:

```powershell
# From the project root in PowerShell
.\launch.ps1
```

TUI should open. Check `~/.art/` directory exists with `art.db`, `exports/`, `uploads/`, `logs/` subdirectories.

Verify `.env.example` has all recognized keys:

```bash
python -c "import config; print('ok')"
```

Should print `ok` with no errors even if `.env` does not exist.

---

## Background Context

<details>
<summary>SQLite vs Supabase decision</summary>

For a local-first downloadable TUI, SQLite is the correct choice at this stage. Supabase is not a drop-in improvement — it is a different architecture that introduces a hosted dependency, a network requirement, and a data-leak surface. Add Supabase only if the product later needs cross-device sync, hosted auth, or a web companion. At that point: use Supabase Auth, apply row-level security to every user-owned table, and keep resume files in private storage buckets.

**What to switch to from Ollama:** Cloud-first OpenAI as the default provider (done in PRD 02). Ollama remains as an advanced local option. Do not make Anthropic the first integration unless writing quality is the top priority — OpenAI is faster to integrate and easier for users to configure.
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