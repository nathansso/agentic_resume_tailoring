---
description: Build or refresh your ART knowledge graph from your resume, LinkedIn export and repos
argument-hint: [paths to your resume, LinkedIn export or project folders]
---

Set up or refresh my ART knowledge graph, following the `art-setup` skill: read my
sources, store the records with `upsert_items`, set my resume header with
`update_profile`, record any job-scoped rules I state, and confirm what is stored.

Sources: $ARGUMENTS

**First, the preflight (the skill's step 0).** Call `get_profile`. If ART's tools are
missing or fail to start, find out why before anything else: detect my OS with `uname -s`,
then check `uv --version` and `art --list`, looking for uv in its default folder
(`~/.local/bin`, or `%USERPROFILE%\.local\bin` on Windows) if it is not on PATH. Never
install anything without my yes: name the exact command and wait.

- No uv: offer uv's official installer. macOS and Linux:

  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ```

  Windows:

  ```powershell
  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
  ```

- No `art`: offer to install it from the pinned release, then put it on PATH:

  ```bash
  uv tool install "art-mcp[ui] @ https://github.com/nathansso/agentic_resume_tailoring/releases/download/v0.1.1/art_mcp-0.1.1-py3-none-any.whl"
  uv tool update-shell
  ```

- Check with `uv --version` and `art --list` (by full path if this shell has the old PATH).
  Then tell me how to restart ART once: `/mcp` and reconnect `art` in the Claude Code
  terminal (or start Claude Code again from a new terminal), quit and reopen the desktop
  app, or restart Codex. Then I run `/art:setup` again and it carries on from the sources.
- If I decline, print the one-line command for me to run myself, and stop.

If no sources are given above, ask me where they are.

If I want my GitHub repos included, tell me to run this in my own terminal (add `--private`
for private repos), then call `list_github_repos`:

```bash
art github login
```

If `art` is not on my PATH, the same command is:

```bash
uvx --from "art-mcp[ui] @ https://github.com/nathansso/agentic_resume_tailoring/releases/download/v0.1.1/art_mcp-0.1.1-py3-none-any.whl" art github login
```
