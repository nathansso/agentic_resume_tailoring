# Agents — Local Guidelines

Use this file for work under `agents/`.

## Scope and precedence

- Root `CLAUDE.md` still controls repo-wide workflow, testing, and delivery rules.
- This file adds only `agents/`-specific implementation guidance.
- Use the active PRD for task acceptance and sequencing.

## Routing and prompts

- Prefer deterministic parsing over LLM routing when intent is cheap and safe to detect locally.
- Do not rely on prompt wording to simulate a missing capability. Add or extend the tool surface first.
- Keep router outputs machine-readable when they are intended for code to parse.
- Inject runtime state into prompts when state changes behavior. Do not hardcode stale assumptions into a global prompt.
- Keep routing prompts short. Favor action rules and a few high-value examples over long prose.

## Tool wrappers

- Tool-facing functions should return plain-English result strings.
- Do not leak raw tracebacks to the user from chat or tool wrappers.
- Keep account-level and repo-level GitHub ingestion as separate capabilities if both exist.
- Preserve the public contract that `ChatAgent.chat()` returns a plain string for the TUI.

## Tests

- Routing changes need tests that prove whether the LLM was called or bypassed.
- Fast paths should be covered with monkeypatched `get_llm()` or wrapper functions so failures stay local.
- If you add new prompt contracts or routing envelopes, add fallback tests for malformed model output.

Keep this file focused on routing and tool-call work. Do not duplicate generic repo workflow rules here.