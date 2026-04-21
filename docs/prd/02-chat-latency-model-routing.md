# PRD 02 — Chat Latency Reduction And Model Routing

> **Prerequisite:** PRD 01 must be complete.
> **Prerequisite reading:** Read `llm.py`, `config.py`, and `agents/chat.py` in full before touching anything.
> **Baseline test:** `python -m pytest test_smoke_formal.py -q` — all tests must pass before and after.
> **Scope:** Fast-path routing already exists but is incomplete. Make it comprehensive. Replace the brittle two-provider LLM factory with a role-based abstraction. OpenAI is the primary cloud provider.

---

## Task Checklist

### 1 — Expand fast-path routing in `agents/chat.py`
- [ ] Read the existing `_semantic_command_match` method in `agents/chat.py` — it already handles exact shortcuts and fuzzy matching
- [ ] Extend `SHORTCUTS` dict to cover these additional patterns (currently missing or incomplete):
  - `"ingest"`, `"what can you do"`, `"help"` → return a concise help string listing available commands
  - `"job"`, `"current job"`, `"active job"` → call `list_jobs()`
  - `"graph"`, `"knowledge graph"`, `"my graph"` → call `query_graph_stats()`
- [ ] Extend `COMMAND_PHRASES` similarly so fuzzy matching covers the above
- [ ] Add an `"ingest"` handler to `TOOL_MAP` that returns a static help string (no LLM call)
- [ ] Add a fast path for short messages under 4 tokens that are not recognized — return a clarification prompt immediately instead of hitting the LLM

### 2 — Refactor `llm.py` into a role-based provider layer
- [ ] Rename `llm.py` → keep the file but replace its content with a role-aware factory
- [ ] Define a `ModelRole` enum or string constants: `CHAT`, `EXTRACT`, `TAILOR`
- [ ] `get_llm(role: str = "chat", temperature: float = 0.0) -> BaseChatModel`
- [ ] Each role maps to its own model name read from `config.py` env vars:
  - `CHAT_MODEL` (default: `gpt-4o-mini`)
  - `EXTRACT_MODEL` (default: `gpt-4o-mini`)
  - `TAILOR_MODEL` (default: `gpt-4o-mini`)
- [ ] Provider selection remains via `LLM_PROVIDER` env var (`"openai"` or `"ollama"`)
- [ ] Ollama stays supported but is not the default — default `LLM_PROVIDER` must be changed to `"openai"` in `config.py`
- [ ] All existing callers of `get_llm()` (grep for `from llm import get_llm` and `get_llm(`) must be updated to pass an explicit role or accept the `"chat"` default

### 3 — Update `config.py`
- [ ] Change `LLM_PROVIDER` default from `"ollama"` to `"openai"`
- [ ] Add `CHAT_MODEL`, `EXTRACT_MODEL`, `TAILOR_MODEL` env var reads with `gpt-4o-mini` as defaults
- [ ] Add `ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")` as a stub (unused for now, but present for PRD 05)
- [ ] Do not remove `OLLAMA_MODEL` or `OLLAMA_BASE_URL` — Ollama remains a fallback option

### 4 — Add latency logging
- [ ] In `agents/chat.py`, in the `chat()` method, wrap the LLM call with `time.perf_counter()` before and after
- [ ] Log at `DEBUG` level: `"[chat] path=llm provider={provider} role=chat duration={ms}ms"`
- [ ] For fast-path hits, log: `"[chat] path=fast_path duration={ms}ms"`
- [ ] Do not log message content — only routing metadata

### 5 — Add tests
- [ ] In `test_smoke_formal.py`, add:
  - `test_fast_path_help_command` — `agent.chat("help")` returns without calling LLM and contains command list
  - `test_fast_path_short_unrecognized` — `agent.chat("hmm")` returns clarification text without calling LLM
  - `test_get_llm_roles` — calling `get_llm("chat")`, `get_llm("extract")`, `get_llm("tailor")` each returns a `BaseChatModel` without error (mock the provider init)
- [ ] Existing `test_chat_semantic_routing_uses_tool_and_is_fast` must still pass

---

## Key Files

| File | Role |
|---|---|
| `agents/chat.py` | `SHORTCUTS`, `COMMAND_PHRASES`, `TOOL_MAP`, `_semantic_command_match`, `chat()` |
| `llm.py` | Rewrite `get_llm()` to be role-aware |
| `config.py` | Add `CHAT_MODEL`, `EXTRACT_MODEL`, `TAILOR_MODEL`; change default provider |
| `agents/parser.py` | Calls `get_llm()` — update to pass role `"extract"` |
| `agents/job_analyzer.py` | Calls `get_llm()` — update to pass role `"extract"` |
| `agents/tailor.py` | Calls `get_llm()` — update to pass role `"tailor"` |
| `agents/matcher.py` | Calls `get_llm()` — check if it does, update to pass role `"extract"` if so |
| `agents/enhancer.py` | Calls `get_llm()` — update to pass role `"chat"` |
| `test_smoke_formal.py` | Add new fast-path and role tests |

---

## Do Not Touch

- `tui/app.py` — no TUI changes in this PRD
- `graph/pipeline.py` — no pipeline changes
- `database/models.py` — no schema changes
- `ingestion/` — no ingestion changes

---

## Constraints

1. The existing fast-path routing test (`test_chat_semantic_routing_uses_tool_and_is_fast`) is a guard — it must keep passing
2. Do not add streaming in this PRD — keep `llm.invoke()` as the call pattern
3. Do not add Anthropic as an active provider yet — stub the config key only
4. Do not add new pip dependencies unless absolutely required for the provider abstraction
5. Do not change the public signature of `get_llm()` in a way that breaks existing callers — add the `role` parameter with a default
6. Keep Ollama fully functional — it is the fallback for users who want a local option

---

## Verification

```bash
python -m pytest test_smoke_formal.py -q
```

All tests must pass. Then verify role-based config works:

```bash
python -c "from llm import get_llm; m = get_llm('chat'); print(type(m).__name__)"
```

Should print a model class name without error when `OPENAI_API_KEY` is set.

---

## Background Context

<details>
<summary>Current state of the LLM layer</summary>

`llm.py` contains a single `get_llm(temperature)` function with a simple `if LLM_PROVIDER == "ollama" / "openai"` branch. The default provider is `"ollama"`, which means users without a local Ollama instance get an immediate failure. There are no role distinctions — every use of the LLM (chat, extraction, tailoring) uses the same model.

`agents/chat.py` already has a well-structured `_semantic_command_match` method that handles direct routing for known commands without the LLM. The test `test_chat_semantic_routing_uses_tool_and_is_fast` verifies this path and currently passes. The goal here is to expand coverage of that fast path and clean up the model layer, not redesign the chat agent.
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