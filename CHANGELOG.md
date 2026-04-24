# Changelog

All completed PRD deliveries are recorded here. PRDs remain as pure forward-looking specs.

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
