---
description: Build or refresh your ART knowledge graph from your resume, LinkedIn export and repos
argument-hint: [paths to your resume, LinkedIn export or project folders]
---

Set up or refresh my ART knowledge graph, following the `art-setup` skill: read my
sources, store the records with `upsert_items`, set my resume header with
`update_profile`, record any job-scoped rules I state, and confirm what is stored.

Sources: $ARGUMENTS

If no sources are given above, ask me where they are.

If I want my GitHub repos included, tell me to run this in my own terminal (add `--private`
for private repos), then call `list_github_repos`:

```bash
uvx --from "art-mcp[ui] @ https://github.com/nathansso/agentic_resume_tailoring/releases/download/v0.1.1/art_mcp-0.1.1-py3-none-any.whl" art github login
```
