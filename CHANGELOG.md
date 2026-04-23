# Changelog

All completed PRD deliveries are recorded here. PRDs remain as pure forward-looking specs.

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
