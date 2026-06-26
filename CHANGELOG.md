# Changelog

All completed PRD deliveries are recorded here. PRDs remain as pure forward-looking specs.

---

## Issues 54 & 58 — Skills Section Tailoring + Best-of-N Attempt Selection
**Status:** complete | **Tests:** 381 pass (38 new)

Reworked how the Technical Skills section is built and how the tailoring loop selects its final output. Previously the skills section rendered the full skill list under static alphabetical categories (never consulting the job description), and the generate→evaluate loop shipped whatever the last retry produced. Now skills are JD-relevance ranked, capped, and role-aware ordered with a semantic signal and persistent pinned "core" skills, and the loop ships the best-scoring attempt rather than the last. Delivered as #54 Phases 1–4 plus the #58 follow-up.

### What shipped
- **Phase 1 (#55) — JD-relevance ranking, cap, role-aware ordering.** `agents/skill_scorer.py` (**created**) — pure-function scorer (`score_skills`, `select_skills`, `rank_and_select_skills`) blending TF-IDF (with IDF over the JD corpus), JD weight, match confidence, proficiency, and evidence; dynamic cap via drop-off + min/max bounds. `agents/tailor.py` — `_rank_skills()` persists `tailored_content["skills_ranked"]`. `agents/formatter.py` — renders the JD-ranked list in relevance order (falls back to the full DB list). `agents/ats_scorer.py` — skills flattening prefers `skills_ranked`.
- **Phase 2 (#56) — persistent embeddings + semantic component.** `agents/skill_embeddings.py` (**created**) — shared MiniLM cache (`ensure_skill_embeddings`, `load_skill_vectors`, `ensure_job_embedding`), degrades gracefully when the model is unavailable. `database/models.py` + `database/db.py` — `Skill.embedding/embedding_model` and `JobDescription.embedding/embedding_model` columns + backward-compatible ALTER migrations. Reingest hooks recompute embeddings (`agents/parser.py`, `tui/services.py`); `agents/chat.py` invalidates the cached JD embedding on job re-analysis. `scripts/backfill_skill_embeddings.py` (**created**).
- **Phase 3 (#57) — pinned core skills.** `database/models.py` — `UserSkill.is_core` (+ migration). Pinned skills always render and seed a relevance floor. Surfaced end-to-end: `web/routers/profile_router.py` (`POST /api/profile/skills/core`), web frontend ★ pin toggle in the Skills tab, `cli.py` `pin-skill` command, and `tui/services.py` `set_skill_core`.
- **Phase 4 (#54) — tunable weights + offline tuning harness.** `agents/skill_scorer.py` — all weights and cap bounds env-overridable (`SKILL_W_*`, `SKILL_MIN/MAX`, etc.) with unchanged defaults, plus per-call `weights`/`bounds` overrides and a `selection_recall` metric. `eval/skill_selection_eval.py` (**created**) — LLM-free harness over checked-in fixtures comparing weight presets by recall + rendered count.
- **Issue 58 — best-of-N attempt selection.** `agents/tailor.py` — the generate→evaluate loop tracks the highest-scoring attempt by algorithmic composite and ships the argmax (falling back to the last content only when no attempt scored), runs the full `MAX_RETRIES` budget by default, and early-exits only above a high "great" bar. Budget and great-bar thresholds are env-overridable (`TAILOR_MAX_RETRIES`, `TAILOR_GREAT_SKILL_COVERAGE`, `TAILOR_GREAT_KW_COVERAGE`).
- **Tests** — `tests/test_skill_scorer.py` (12), `tests/test_skill_embeddings.py` (8), `tests/test_skill_pinning.py` (7, incl. web `TestClient`), `tests/test_skill_tuning.py` (7), and 4 best-of-N tests in `tests/test_prd04.py`.

### Deviations from spec
- Kept the local MiniLM embedding model rather than adding a provider-swappable embedding config — semantic scoring degrades to lexical + metadata signals when the model is unavailable.
- Final calibration of the skill-scoring weights and the #58 great-bar thresholds is deferred to the #51 ATS efficacy benchmark; this arc ships the tunable mechanism with sensible (un-calibrated) defaults.

---

## Issue 13 — LinkedIn Ingestion via Bright Data
**Status:** complete | **Tests:** 332 pass (10 new)

Replaced the Playwright LinkedIn scraper with Bright Data's Web Scraper API and surfaced LinkedIn ingestion in the web app for the first time. The scrape auto-triggers when a user sets/changes their LinkedIn URL (the "initialize/update knowledge graph" moment) and runs in the background; PDF upload remains as a fallback.

### What shipped
- `ingestion/linkedin.py` — new `ingest_brightdata()` (trigger → poll `/progress` → download `/snapshot`) + `_brightdata_to_text()` flattener; removed `ingest_web` and the Playwright/bs4 scraping path. `ingest_pdf` fallback retained.
- `config.py` — `BRIGHTDATA_API_KEY` (platform-wide) and `BRIGHTDATA_LINKEDIN_DATASET_ID` (default `gd_l1viktl72bvl7bjuj0`).
- `database/models.py` + `database/db.py` — `User.linkedin_ingested_url/linkedin_ingest_status/linkedin_ingest_error/linkedin_ingested_at` columns + backward-compatible ALTER migrations.
- `tui/services.py` — `ingest_linkedin(url, user_id)` records the importing/done/failed lifecycle; never raises.
- `web/routers/ingest_router.py` — `POST /api/ingest/linkedin` and `/linkedin/pdf`.
- `web/routers/profile_router.py` — `PATCH /api/profile` schedules a background ingest when the URL changes; GET exposes ingest status.
- Frontend — LinkedIn tab in `IngestPanel` (URL + PDF fallback) and a live import-status indicator in `ProfilePanel`.
- `cli.py` — `ingest-linkedin` rewired to Bright Data.
- Deps — removed `playwright` from `requirements*.txt`, lockfile, and generator; repointed `test_deps_split.py` heavyweight checks to `sentence-transformers`.

### Deviations from spec
- Issue framed an optional per-user key with a "users without API access" fallback; shipped a platform-wide key (hosted SaaS) with PDF upload as the fallback when the key is unset.

---

## PRD 10 — Persistent Per-Job Chat Memory
**Status:** complete | **Tests:** 108 pass (6 new)

Every chat message is now written to SQLite on each turn. On app restart, selecting a job replays prior messages in the scroll and restores `ChatAgent.history` so the AI retains full context.

### What shipped
- `database/models.py` — `ChatMessage` table (`message_id`, `job_id`, `role`, `content`, `created_at`); auto-created by `SQLModel.metadata.create_all` on next startup; no manual migration needed
- `tui/services.py` — `save_chat_message(job_id, role, content)` and `load_chat_history(job_id, limit=20)`; both non-raising with try/except; `job_id=None` represents landing context
- `agents/chat.py` — `chat()` lazily imports `tui.services` and calls `save_chat_message` after every append to `self.history` (fast-path, tool-call, and clarify/response paths); `set_active_job()` loads DB history on cold start and sets `self.history` if non-empty
- `tui/app.py` — `_show_job_details()` calls `services.load_chat_history()` on first visit and reconstructs the scroll before the job detail card; populates `_job_chat_cache` from DB result
- `tests/test_services.py` — 3 new tests: round-trip for job, landing context (`job_id=None`), limit behavior
- `tests/test_chat.py` — 3 new tests: persistence of user + assistant rows, history restore via `set_active_job`, DB write failure does not affect response

### Deviations from spec
- None

---

## PRD 05 — Desktop Productization, Cloud Models, And Data Security
**Status:** complete | **Tests:** 64 pass

Moved app data to `~/.art/`, wired startup config validation, added Windows launchers, and documented the install process.

### What shipped
- `config.py` — `APP_DATA_DIR = Path.home() / ".art"`, `EXPORTS_DIR`, `UPLOADS_DIR`, `LOGS_DIR`; `DATABASE_URL` moved from `{root}/art.db` to `~/.art/art.db`; `ensure_app_dirs(base_dir=None)` for idempotent directory creation
- `config_validator.py` — **created**; `validate_config() -> list[str]` checks `LLM_PROVIDER` value, API key presence for chosen provider, Ollama reachability, and `APP_DATA_DIR` writability
- `database/db.py` — `_migrate_db_location()` runs before engine creation; copies `{project_root}/art.db` → `~/.art/art.db` once, non-destructively, and logs the migration
- `tui/app.py` — `on_mount` calls `ensure_app_dirs()` then `validate_config()`; config errors shown in status bar as `[CONFIG ERROR] ...`
- `cli.py` — `_check_config()` helper added; called at top of every command; prints errors and exits with code 1 on failure
- `launch.bat` — **created**; Windows CMD launcher
- `launch.ps1` — **created**; PowerShell launcher
- `.env.example` — **created**; all recognized env vars with comments
- `INSTALL.md` — **created**; install guide covering clone → venv → pip → `.env` → launch
- `tests/test_prd05.py` — 5 tests: missing API key (OpenAI), missing API key (Anthropic), unknown provider, `ensure_app_dirs` subdirectory creation, no secrets in logs

### Deviations from spec
- `validate_config()` accepts `"anthropic"` as a valid `LLM_PROVIDER` (not listed in the PRD spec but is the current default)
- `ensure_app_dirs()` accepts an optional `base_dir` parameter to keep tests hermetic
- Disabling ingestion/tailoring buttons on config error deferred — status bar error message is the signal; button disabling requires TUI-layer changes beyond PRD 05 scope

---

## Session — Chat Routing Overhaul, TUI Polish, Profile Overlay
**Status:** complete | **Tests:** 38 pass

Rewrote the chat fast-path to be context-aware and far less aggressive, added a numbered-options system for ingestion, replaced flat skill queries with a job-match view, and shipped several TUI quality-of-life fixes.

### What shipped
- `agents/chat.py` — removed `COMMAND_PHRASES` and all fuzzy/token-based data-query routing; only ingestion entry-points and help bypass the LLM now; added `_pending_options` dict and `_last_bot_asked_question()` so short replies after a bot question reach the LLM; added `_ingest_github_with_options()` for numbered GitHub ingestion flow; added step 1c token-combo detection (catches "ingest skill from my github" etc.); removed short-message guard; added `query_skills_vs_jobs()` (shows ATS score + matched/missing per job — preferred over raw skill dump in chat)
- `tui/screens/onboarding.py` — redesigned as a 4-step sequential flow (name → resume → GitHub → LinkedIn) with a file-upload button via tkinter and skip options for optional steps
- `tui/screens/profile.py` — **created**; `ProfileScreen` overlay with avatar initials, editable name/GitHub/LinkedIn fields, and live stats; dismissed via Escape or Save
- `tui/app.py` — status bar avatar button opens ProfileScreen; `/copy` slash command strips Rich markup and pipes to `clip.exe`; `ctrl+c` bound to noop; SIGINT suppressed at process level; F1–F4 replaced with slash commands
- `tui/services.py` — added `get_profile_data()` and `update_profile()`
- `CLAUDE.md` — **created**; project conventions, test coverage requirements, commit/changelog practices
- `.gitignore` — `tailored_output.json` and `tailored_resume.md` untracked (generated artifacts)
- `test_smoke_formal.py` — 9 new tests covering: onboarding step validation, slash-command routing, profile services, ProfileScreen mount, ctrl+c binding, clipboard, pending-option resolution, token-combo routing for all three ingestion sources, `query_skills_vs_jobs`

### Deviations from spec
- Short-message fast-path removed entirely rather than just relaxed — LLM is fast enough to handle short queries gracefully
- `query_skills()` retained in `TOOL_MAP` for cases where the LLM explicitly wants a raw skill dump; `query_skills_vs_jobs()` is the preferred tool surfaced in the system prompt

---

## PRD 03 — Onboarding, Profile Ingestion, And Knowledge Graph UX
**Status:** complete | **Tests:** 16 pass

Replaced the hardcoded single-user prototype with an explicit local active profile. Added a first-run onboarding screen to the TUI, structured the knowledge graph view, and wired GitHub ingestion as a post-onboarding option.

### What shipped
- `database/models.py` — added `onboarding_complete` (bool) and `onboarding_steps` (JSON) columns to `User`
- `database/db.py` — added `_migrate_db()` for backward-compatible SQLite column additions on existing DBs
- `database/user_utils.py` — replaced `get_or_create_default_user()` with `get_active_profile() -> User | None` and `create_profile(name, email, github_username, linkedin_url) -> User`; active profile persisted at `~/.art/active_profile_id`; old function kept as backward-compat wrapper
- `tui/screens/onboarding.py` — **created**; `OnboardingScreen` collects name/email/resume path/GitHub/LinkedIn, validates inputs, runs ingestion in a background worker with progress messages, then dismisses back to main app
- `tui/app.py` — `on_mount` calls `get_active_profile()` and pushes `OnboardingScreen` if `None`; `_on_onboarding_done` callback refreshes state and offers GitHub ingestion button
- `tui/services.py` — `get_first_user_id()` updated to use `get_active_profile()`; added `get_graph_summary()` and `ingest_github_for_profile()`
- `agents/chat.py` — all `select(User).limit(1)` calls replaced with `get_active_profile()`
- `graph/pipeline.py` — `ingest_resume_node` prefers `get_active_profile()`, falls back to wrapper
- `test_smoke_formal.py` — fixture patches `user_utils`; `_seed_user_and_skill` writes profile pointer; 4 new tests added

### Deviations from spec
- `get_or_create_default_user()` retained as backward-compat wrapper rather than removed — pipeline and CLI paths still reference it
- `on_mount` loads data tables before pushing onboarding so empty-state placeholders render correctly if user dismisses the screen
- `ACTIVE_PROFILE_FILE` exposed as module-level var in `user_utils` to allow monkeypatching in tests
- `test_onboarding_screen_mounts` wraps `OnboardingScreen` in a minimal `App` because `run_test()` is only available on `App`

---

## PRD 02.5 — Chat-Triggered Ingestion And Tailoring
**Status:** complete | **Tests:** 12 pass

Wired the ingestion and tailoring pipelines directly into the TUI chat agent. Users can now type `ingest resume <path>`, `ingest github`, or `tailor <job>` in the chat box and get results without the LLM being invoked — all handled by argument-parsing fast-paths.

### What shipped
- `tui/services.py` — `ingest_resume_file`, `ingest_github`, `ingest_linkedin_pdf` service functions (no exceptions escape to caller)
- `agents/chat.py` — four tool functions (`run_ingest_resume`, `run_ingest_github`, `run_ingest_linkedin_pdf`, `run_tailor`), registered in `TOOL_MAP`; regex fast-paths added to `_semantic_command_match`; `ingest github` shortcut added
- `test_smoke_formal.py` — 4 new fast-path and error-handling tests

### Deviations from spec
- Fast-path regexes match against the raw (un-normalized) message so file paths with dots/slashes are preserved — the normalizer would strip them
- `run_tailor` monkeypatched at the `chat_module` level in tests since it's defined in the same module as the agent
- LinkedIn web scraping (Playwright) intentionally excluded — too heavy for a chat thread

---

## PRD 02 — Chat Latency Reduction And Model Routing
**Status:** complete | **Tests:** 8 pass

Expanded the fast-path routing in the chat agent and replaced the brittle two-provider LLM factory with a role-based abstraction. OpenAI is now the default provider.

### What shipped
- `config.py` — added `CHAT_MODEL`, `EXTRACT_MODEL`, `TAILOR_MODEL` env vars (default `gpt-4o-mini`); `ANTHROPIC_API_KEY` stub; changed default `LLM_PROVIDER` from `ollama` to `openai`
- `llm.py` — rewritten with `ModelRole` constants and role-aware `get_llm(role, temperature)`
- `agents/chat.py` — expanded `SHORTCUTS` and `COMMAND_PHRASES` (help, job, graph commands); `get_help_text()` added; short-message fast-path for < 4 unrecognized tokens; latency logging at DEBUG level
- `agents/parser.py`, `job_analyzer.py` — role=`"extract"`
- `agents/tailor.py` — role=`"tailor"`
- `agents/enhancer.py` — role=`"chat"`
- `test_smoke_formal.py` — 3 new tests; monkeypatch lambda updated for role kwarg

### Deviations from spec
- Short-message fast-path triggers on messages with < 4 tokens that don't match any shortcut or phrase (exact spec wording)
- `test_get_llm_roles` mocks `ChatOpenAI` at the `langchain_openai` module level to avoid needing a real API key in CI

---

## PRD 01 — TUI Stabilization And Workflow Foundation
**Status:** complete | **Tests:** 5 pass

Separated DB queries from widget rendering, added explicit app state tracking, and made the TUI guide users through the workflow instead of requiring knowledge of hidden commands.

### What shipped
- `tui/services.py` — created; DB query functions extracted from widget methods, each returning plain data (lists/dicts)
- `tui/app.py` — `AppState` constants; `_refresh_app_state()`; status bar below header; empty-state placeholder rows in all three data tables; `_load_graph_view` moved to `@work(thread=True)`
- `test_smoke_formal.py` — 3 new tests (empty-state tables, `_refresh_app_state` return values, status bar text); `tui.services.engine` patched in fixture

### Deviations from spec
- `_load_viz` retains direct DB access — its multi-query charting logic was out of scope for task 1
- Status bar uses `--` instead of `—` (em-dash) for Windows ASCII terminal safety
- `_refresh_app_state()` returns the state string rather than relying on callers reading `app.app_state`, enabling cleaner testability
