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
- Preserve the public contract that `ChatAgent.chat()` returns a plain string for the web/API layer.

## Tests

- Routing changes need tests that prove whether the LLM was called or bypassed.
- Fast paths should be covered with monkeypatched `get_llm()` or wrapper functions so failures stay local.
- If you add new prompt contracts or routing envelopes, add fallback tests for malformed model output.

Keep this file focused on routing and tool-call work. Do not duplicate generic repo workflow rules here.

---

## Resume export pipeline

`agents/formatter.py` — `ResumeFormatterAgent` converts tailored JSON into finished resume files.

- Primary output path: **LaTeX → PDF** via `format_pdf()`, compiled by tectonic (preferred) or pdflatex fallback.
- `format_tex()` returns raw `.tex` source (Jake's Resume layout, MIT license).
- `format_docx()` returns `.docx` bytes mirroring the same layout via python-docx.
- `format_markdown()` is **deprecated** — emits `DeprecationWarning`. Do not use in new code.
- tectonic binary lives at `.venv/Scripts/tectonic.exe` (Windows) and is installed via the Dockerfile on Linux. The formatter resolves it automatically alongside `sys.executable`.
- The `.tex` preamble guards pdflatex-specific commands (`inputenc`, `glyphtounicode`, `pdfgentounicode`) with `\ifdefined\pdftexversion` so the source compiles under both pdflatex and XeTeX/tectonic.