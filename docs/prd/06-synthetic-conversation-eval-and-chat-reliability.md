# PRD 06 - Synthetic Conversation Evaluation And Chat Reliability

> **Prerequisite:** PRDs 01 through 03 should be complete. PRD 04 is recommended but not required.
> **Sequencing note:** This PRD is safe to run before PRD 04 and PRD 05. If PRD 04 is not complete, defer any active-job or job-lifecycle-specific eval scenarios and traces. PRD 05 is not required for this work.
> **Repository guidance:** Use `CLAUDE.md` for repo-wide workflow rules, `agents/CLAUDE.md` for routing work, and `tui/CLAUDE.md` for UI/service-boundary work. Treat `.github/` instruction files as thin tooling mirrors, not the authoritative policy surface.
> **Prerequisite reading:** Read `agents/chat.py`, `tui/app.py`, `tui/services.py`, `llm.py`, `cli.py`, and `test_smoke_formal.py` before starting.
> **Baseline test:** `python -m pytest test_smoke_formal.py -q` - all tests must pass before and after.
> **Core problem:** ART's TUI chat already mixes deterministic fast-path routing, tool wrappers, and an LLM fallback. What it does not have is a reproducible way to measure whether plain-English user requests are routed correctly. Manual testing is too sparse, and raw chat logs alone are not enough because they do not tell you whether the failure came from routing, argument extraction, tool execution, or response wording. This PRD adds a synthetic-user evaluation loop that generates scenario-grounded chats, replays them against the app, captures structured traces, scores outcomes automatically, and writes a Claude-ready handoff report.

---

## Design Rules

1. Do **not** start with a freeform simulator that chats randomly. First define scenario contracts with expected tools, expected outcomes, and forbidden behaviors.
2. Do **not** drive the full Textual app first. Start by evaluating `ChatAgent.chat()` directly, because that is the narrowest control point for routing and tool selection.
3. Keep real user transcript logging **opt-in**. ART handles resume, GitHub, and LinkedIn data, so synthetic evals should be the default source of improvement data.
4. Separate **routing correctness** from **tool side effects**. Default eval runs should stub network and file-heavy operations so failures point at the chat layer first.

---

## Task Checklist

### 1 - Add structured trace capture to `agents/chat.py`
- [ ] Add a `ChatTurnTrace` typed dict or dataclass that captures at least:
  - `session_id`
  - `turn_index`
  - `user_message`
  - `normalized_message`
  - `route_kind` (`pending_option`, `fast_path`, `llm`, `tool_call`, `error`)
  - `matched_fast_path`
  - `tool_calls_requested`
  - `tool_calls_executed`
  - `response_text`
  - `duration_ms`
  - `llm_provider`
  - `llm_role`
  - `error`
- [ ] Extend `ChatAgent.__init__` to accept optional `trace_sink` and `session_id` arguments
- [ ] Preserve the public `chat(user_message: str) -> str` API used by the TUI
- [ ] Record a trace for every path:
  - pending numbered option resolution
  - direct fast-path match
  - token-combo ingestion routing
  - LLM response with no tool call
  - LLM response that resolves one or more `TOOL_CALL`s
  - LLM/tool failure
- [ ] Refactor `_resolve_tool_calls` so it returns both the rendered text and structured tool metadata instead of only a final string
- [ ] Store the most recent trace on the agent, for example `self.last_trace`, so tests and runners can inspect it cheaply

### 2 - Add an eval artifact store and optional transcript logger
- [ ] Create a new verification module, for example under `verification/chat_eval/`, to write eval artifacts to:
  - `Path.home() / ".art" / "evals" / <run_id> / transcript.jsonl`
  - `Path.home() / ".art" / "evals" / <run_id> / summary.json`
  - `Path.home() / ".art" / "evals" / <run_id> / claude_handoff.md`
- [ ] Add an artifact writer that appends one JSON object per turn, using the trace structure from task 1 plus scenario metadata
- [ ] Add a markdown report builder that can summarize one run without requiring the reader to open raw JSONL files
- [ ] Add an opt-in TUI logging path in `tui/app.py` so debug sessions can persist traces from real usage when a flag like `ART_LOG_CHAT_EVAL=1` is enabled
- [ ] Keep real-session logging disabled by default
- [ ] Redact obvious secrets in real-session logs:
  - token-looking strings
  - absolute local file paths when possible
  - raw environment variable values

### 3 - Define a scenario contract format and seed fixtures
- [ ] Create `verification/chat_eval/scenarios/` with JSON or YAML scenario files
- [ ] Each scenario must define:
  - `scenario_id`
  - `description`
  - `tags`
  - `profile_fixture`
  - `job_fixture` or `job_state` when relevant
  - `initial_chat_history` when relevant
  - `canonical_turns` or a user goal description
  - `max_turns`
  - `must_call_tools`
  - `forbidden_tools`
  - `required_response_substrings`
  - `success_conditions`
  - `failure_labels` for reporting
- [ ] Add seed helpers that create the active profile, skills, jobs, and prior results using the same DB patterns already used by `test_smoke_formal.py`
- [ ] Start with a focused scenario set that matches the current product surface:
  - plain-English GitHub ingestion when the profile already has a username
  - plain-English GitHub ingestion when the username is missing
  - plain-English resume ingestion with a file path
  - plain-English LinkedIn PDF ingestion with a file path
  - tailoring request stated as prose instead of the exact `tailor ...` command
  - data-query request that must **not** trigger ingestion
  - help request phrased conversationally
  - follow-up reply after the bot asked a question

### 4 - Build a synthetic user generator instead of a random chatter
- [ ] Create a `SyntheticUserAgent` under `verification/chat_eval/` that generates user utterances from a scenario contract
- [ ] Add at least two modes:
  - `canonical` - replay exact hand-authored turns
  - `synthetic` - generate paraphrases and follow-ups from an eval model
- [ ] Prefer adding a separate model role in `llm.py`, such as `eval` and optionally `review`, instead of overloading the production chat role
- [ ] Add config knobs for eval generation, for example `EVAL_MODEL`, while keeping the existing runtime roles intact
- [ ] The synthetic user must be constrained by the scenario:
  - plain English only unless the scenario explicitly allows slash commands
  - no direct mention of internal tool names
  - no cheating by copying the exact supported command unless the scenario calls for it
- [ ] For the first implementation, prioritize paraphrase generation over fully open-ended autonomous conversations
- [ ] Provide a deterministic fallback when no eval model is configured, so the harness can still run with hand-authored turn variants

### 5 - Add a runner and CLI entry point
- [ ] Add a CLI command such as `python cli.py chat-eval` without breaking existing commands
- [ ] Support arguments like:
  - `--scenario <id>` to run one scenario
  - `--variants <n>` to generate multiple paraphrases
  - `--mode canonical|synthetic|mixed`
  - `--stubbed` and `--live`
  - `--output-dir <path>` as an override for local artifacts
- [ ] The default runner path should instantiate `ChatAgent` directly with a trace sink, not the full Textual app
- [ ] Add a second-phase runner mode that can optionally exercise `ArtApp._handle_chat_input(...)` for end-to-end coverage after the direct harness is stable
- [ ] In `--stubbed` mode, monkeypatch or inject deterministic fakes for side-effect-heavy operations such as:
  - `services.ingest_github`
  - `services.ingest_resume_file`
  - `services.ingest_linkedin_pdf`
  - `run_tailor`
- [ ] In `--live` mode, limit scenarios to a small explicit allowlist so the harness does not accidentally run network-heavy flows during normal development

### 6 - Score each run and generate a Claude-ready handoff
- [ ] Add objective scoring for each scenario:
  - success or failure
  - tool selection accuracy
  - argument extraction accuracy
  - number of turns to success
  - route distribution (`fast_path` vs `llm` vs `tool_call`)
  - forbidden behavior count
- [ ] Add failure classification labels such as:
  - `missing_fast_path`
  - `wrong_tool`
  - `argument_parse_failure`
  - `llm_prompt_gap`
  - `tool_wrapper_failure`
  - `response_clarity_failure`
- [ ] Add an optional review pass that uses a stronger model to summarize failures, but keep the final score grounded in deterministic checks from traces and scenario contracts
- [ ] Write `claude_handoff.md` with:
  - the failing scenarios first
  - the expected behavior
  - the actual transcript
  - the trace metadata
  - likely owning files
  - minimal reproduction commands
  - a short prioritized fix list
- [ ] The handoff report must be good enough to paste directly into Claude Code as the next implementation prompt

### 7 - Add focused regression tests
- [ ] Add tests to `test_smoke_formal.py` for trace capture on the fast path
- [ ] Add tests to `test_smoke_formal.py` for trace capture on an LLM `TOOL_CALL` path
- [ ] Add tests for scenario loading and seed setup using `isolated_engine`
- [ ] Add tests for stubbed eval execution so a scenario can pass without network access
- [ ] Add tests for scoring so expected tool mismatches are classified correctly
- [ ] Add tests for markdown handoff generation so the failure report includes expected headings and transcript snippets
- [ ] If TUI opt-in logging is added, include one test that verifies the logger is only active when the flag is enabled

---

## Key Files

| File | Role |
|---|---|
| `agents/chat.py` | Add structured trace capture without changing the public chat API |
| `tui/app.py` | Optional opt-in session logging and later end-to-end replay path |
| `llm.py` | Add `eval` and optional `review` roles for synthetic-user generation and run review |
| `cli.py` | Add `chat-eval` command |
| `verification/chat_eval/` | New harness, scenario loader, runner, scorer, and reporters |
| `test_smoke_formal.py` | Add all new tests; continue using `isolated_engine` |
| `CLAUDE.md` | Reference for testing and delivery constraints; do not weaken them |

---

## Do Not Touch

- `database/models.py` - no schema changes are needed for this PRD
- `graph/pipeline.py` node logic - keep eval focus on the chat layer first
- `ingestion/github.py`, `ingestion/resume.py`, `ingestion/linkedin.py` internals - stub them in eval mode instead of rewriting them preemptively
- Existing CLI commands - `ingest-*`, `tailor`, and `status` must keep working as they do now

---

## Constraints

1. `ChatAgent.chat()` must continue to return a plain string so the TUI contract stays stable.
2. Default eval runs must be local-first and cheap. Use stubbed side effects unless the caller explicitly requests `--live`.
3. Eval artifacts belong under `~/.art/evals/` by default, not in the git repo.
4. Logging real user chats must be opt-in because ART handles sensitive professional data.
5. Synthetic transcripts should be strong enough to expose routing bugs, not just exact command matches.
6. All new tests must live in `test_smoke_formal.py` and use existing fixtures and patterns from this repo.
7. The markdown handoff must stand on its own; Claude should not need to parse raw JSONL to understand failures.
8. Do not add new pip dependencies unless absolutely necessary. Prefer stdlib, existing LangChain integration, and repo-native patterns.
9. If PRD 04 is not implemented yet, do not block this PRD on active-job context. Treat job-aware traces and scenarios as optional follow-up coverage.

---

## Verification

```bash
python -m pytest test_smoke_formal.py -q
python cli.py chat-eval --mode canonical --variants 1 --stubbed
python cli.py chat-eval --scenario github_plain_english_existing_profile --mode mixed --variants 5 --stubbed
```

Then manually inspect the newest run under `~/.art/evals/` and verify that:

1. `transcript.jsonl` contains one entry per turn with route metadata.
2. `summary.json` contains scenario-level pass and fail metrics.
3. `claude_handoff.md` lists failures first and points to likely code surfaces such as `agents/chat.py` and `tui/app.py`.

---

## Background Context

<details>
<summary>Why this repo needs a scenario-based eval loop</summary>

The current `agents/chat.py` already has meaningful behavior: exact shortcuts, numbered pending options, regex argument fast-paths for ingestion and tailoring, token-combo routing for plain-English ingestion intent, and an LLM fallback that resolves `TOOL_CALL:` lines. That means ART does not need a generic chatbot benchmark. It needs a routing benchmark tied to ART's own commands and workflows.

The TUI in `tui/app.py` currently shows a `Thinking...` placeholder and sends text to the chat agent, but it does not record structured traces or generate transcripts that explain what the agent actually did. The current tests in `test_smoke_formal.py` cover important point cases, but they do not generate broader plain-English variants or produce a failure bundle that can be handed to Claude Code.

The `verification/` directory is currently empty, which makes it the right place to add an isolated eval harness. The app is also local-first and privacy-sensitive, so synthetic or stubbed chats should be the default feedback source rather than always recording real user sessions.
</details>

---

## Progress

> Claude Code: update this section as you work. Do not delete unchecked items.

**Status:** `complete`

### Files Modified
- `agents/chat.py` — `ChatTurnTrace` TypedDict; `ChatAgent.__init__` extended with `trace_sink`/`session_id`; `_emit_trace`, `_infer_fast_path` methods added; `chat()` emits traces on all paths; `_resolve_tool_calls` refactored to return `(text, requested, executed)` tuple
- `tui/app.py` — `_get_agent()` reads `ART_LOG_CHAT_EVAL=1` and wires live session sink
- `cli.py` — `chat-eval` subcommand added (`cmd_chat_eval`)
- `llm.py` — `eval`/`review` roles added to `ModelRole` and `_ROLE_MODELS`
- `config.py` — `EVAL_MODEL`, `REVIEW_MODEL` env-driven config
- `test_smoke_formal.py` — 8 new regression tests (PRD 06 Task 7); 56 total passing

### Files Created
- `verification/__init__.py`
- `verification/chat_eval/__init__.py`
- `verification/chat_eval/traces.py` — re-exports `ChatTurnTrace`
- `verification/chat_eval/artifacts.py` — `append_turn`, `write_summary`, `write_handoff`, `build_handoff_markdown`, `make_live_session_sink`
- `verification/chat_eval/scenario_loader.py` — `load_scenario`, `load_all_scenarios`, `seed_profile`, `seed_scenario_db`
- `verification/chat_eval/synthetic_user.py` — `SyntheticUserAgent` (canonical / synthetic / mixed modes)
- `verification/chat_eval/scorer.py` — `score_scenario_result` (deterministic, no LLM)
- `verification/chat_eval/runner.py` — `EvalRunner` context manager
- `verification/chat_eval/scenarios/github_plain_english_existing_profile.json`
- `verification/chat_eval/scenarios/github_missing_username.json`
- `verification/chat_eval/scenarios/resume_ingestion.json`
- `verification/chat_eval/scenarios/linkedin_ingestion.json`
- `verification/chat_eval/scenarios/tailoring_prose.json`
- `verification/chat_eval/scenarios/data_query_no_ingest.json`
- `verification/chat_eval/scenarios/help_conversational.json`
- `verification/chat_eval/scenarios/followup_after_question.json`

### Completed Tasks
- Task 1: Structured trace capture in `agents/chat.py` ✓
- Task 2: Eval artifact store and optional transcript logger ✓
- Task 3: Scenario contract format (8 JSON files) ✓
- Task 4: `SyntheticUserAgent` (canonical + synthetic modes) ✓
- Task 5: `EvalRunner` context manager with stubbing ✓
- Task 6: `chat-eval` CLI command ✓
- Task 7: 8 regression tests — all 56 tests pass ✓

### Notes / Deviations
- PRD 04 active-job context deferred; `get_active_profile()` returns `None` in eval mode, which is handled gracefully throughout.
- Fast-path label inference uses a post-hoc `_infer_fast_path()` method rather than inserting trace hooks inside `_semantic_command_match`, keeping the routing method clean.
- `EvalRunner` stub mode patches `tui.services` and `agents.chat.run_tailor`; LLM is not stubbed (scenarios that fast-path avoid LLM calls entirely in stub mode).
- CLI `cmd_chat_eval` mirrors the `isolated_engine` fixture pattern: creates a temp SQLite DB and patches all module-level engine references.