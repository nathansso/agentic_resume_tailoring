<!-- ART for Codex (#203). Paste this into your repo's AGENTS.md or ~/.codex/AGENTS.md. -->

## Resumes: use ART

When I ask you to tailor my resume, update my experience, or look at past versions, use the
ART tools (the `art` MCP server) and the `art-tailor` / `art-setup` skills.

- Every fact on my resume must come from an ART tool result: `list_items`, `get_item`,
  `kg_search` or `get_profile`. Never invent a metric, date, employer, tool or outcome. If
  something is missing, ask me, then store it with `upsert_items`.
- At the start of every job, call `art_briefing`. Its pins are hard rules; honour them
  verbatim.
- Before building on a job's resume, call `get_head`. Keep any `editor_edits`: they are my
  hand edits.
- Show me the plan before `execute_plan` unless I said to skip approval. Render with
  `render`, and tell me where the PDF is.
