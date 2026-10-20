---
description: Build or refresh your ART knowledge graph from your resume, LinkedIn profile or export, and repos
argument-hint: [paths to your resume, LinkedIn export or project folders, or your LinkedIn URL]
---

Set up or refresh my ART knowledge graph, following the `artie-setup` skill: read my
sources, store the records with `upsert_items`, set my resume header with
`update_profile`, record any job-scoped rules I state, and confirm what is stored.

Sources: $ARGUMENTS

**First, the preflight (the skill's step 0).** Call `get_profile`. If ART's tools are
missing or fail to start, find out why before anything else: detect my OS with `uname -s`,
then check `uv --version` and `artie --list`, looking for uv in its default folder
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

- No `artie`: offer to install it from the pinned release, then put it on PATH:

  ```bash
  uv tool install --python 3.12 "art-mcp[ui] @ https://github.com/nathansso/agentic_resume_tailoring/releases/download/v0.2.0/art_mcp-0.2.0-py3-none-any.whl"
  uv tool update-shell
  ```

- Check with `uv --version` and `artie --list` (by full path if this shell has the old PATH).
  Then tell me how to restart ART once: `/mcp` and reconnect `artie` in the Claude Code
  terminal (or start Claude Code again from a new terminal), quit and reopen the desktop
  app, or restart Codex. Then I run `/artie:setup` again and it carries on from the sources.
- If I decline, print the one-line command for me to run myself, and stop.

If no sources are given above, ask me where they are.

**My LinkedIn profile.** Ask me for my LinkedIn profile URL (`linkedin.com/in/<name>`), unless
`get_profile` already shows a `linkedin_url` or I gave one above. Then call
`ingest_linkedin` (with `url`, or with none to use the saved one) and tell me what was added,
what was skipped and which sections came back empty. Ask me about each of its `candidates`
(posts, courses, links) before storing anything from them. It needs the GitHub sign-in below,
it can take a few minutes, and it allows two profiles a day. If it returns an error, tell me
its message and offer the LinkedIn data export instead; if ART has no `ingest_linkedin`
tool (an older install), use the export.

If I want my GitHub repos or my LinkedIn profile included, tell me to run this in my own
terminal (add `--private` for private repos; it also asks for my LinkedIn URL, and
`artie github login` is the GitHub sign-in alone), then call `list_github_repos` and
`ingest_linkedin`:

```bash
artie connect
```

If `artie` is not on my PATH, the same command is:

```bash
uvx --python 3.12 --from "art-mcp[ui] @ https://github.com/nathansso/agentic_resume_tailoring/releases/download/v0.2.0/art_mcp-0.2.0-py3-none-any.whl" artie connect
```
