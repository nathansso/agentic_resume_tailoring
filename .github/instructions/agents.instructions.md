---
description: "Use when changing chat routing, prompt engineering, tool-calling, ingestion intent parsing, or model-role behavior in agents/**/*.py or llm.py."
name: "ART Agent Routing"
applyTo:
  - "agents/**/*.py"
  - "llm.py"
---
# Agent Routing Guidelines

This file complements root and local `CLAUDE.md` guidance for VS Code tooling. Keep it short and scoped to `agents/` work.

- Prefer deterministic parsing over LLM routing when the intent is cheap and safe to identify locally.
- Do not use prompt engineering as a substitute for missing product capability; add the tool or service first.
- Keep router outputs machine-readable when downstream code needs to parse them.
- Preserve the public `ChatAgent.chat()` contract as a plain string return value.
- Tool wrappers should return plain-English results and avoid leaking tracebacks.
- Routing changes must include tests that prove whether the LLM was called or bypassed.