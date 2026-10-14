# ART for Codex

Codex uses the same plugin directory as Claude Code (`plugin/`, with a
`.codex-plugin/plugin.json` manifest): the `art` MCP server and the `art-tailor` and
`art-setup` skills. You need [uv](https://docs.astral.sh/uv/), plus
[tectonic](https://tectonic-typesetting.github.io) or pdflatex for PDFs.

## Install

```bash
codex plugin marketplace add nathansso/agentic_resume_tailoring
codex plugin add art@art
```

Or register only the server:

```bash
codex mcp add art -- uvx --from "art-mcp[ui] @ https://github.com/nathansso/agentic_resume_tailoring/releases/download/v0.1.0/art_mcp-0.1.0-py3-none-any.whl" art mcp
```

The plugin doesn't install hooks, because Codex plugins can't ship them. Install them
into `~/.codex/hooks.json`, or into `.codex/hooks.json` with `--project`:

```bash
uvx --from "art-mcp[ui] @ https://github.com/nathansso/agentic_resume_tailoring/releases/download/v0.1.0/art_mcp-0.1.0-py3-none-any.whl" art hooks codex --write
```

Then run `/hooks` in Codex to review and trust them.

Run the same command again after you upgrade ART. It replaces ART's hooks from an earlier
install (a `git+` source, or an older release wheel) with the current ones, prints each one it
replaced, and leaves every other hook alone. A second run changes nothing.

- **Windows.** Codex runs a hook with the shell of the session. On Windows that is PowerShell
  (`powershell -NoProfile -Command`), which runs the hook line as written. Only if Codex has no
  session shell does it fall back to `%COMSPEC%` (`cmd.exe`), where `;` is not a separator, so
  the hook line is not supported there: `art hook` is handed `user-prompt;` and exits 0 without
  doing anything. Use a PowerShell or bash session shell.

- **Where hooks are loaded from.** Codex loads user-level hooks (`~/.codex/hooks.json`)
  everywhere. A project's `.codex/hooks.json` loads only in a project Codex trusts.
- **Approving writes.** ART's write tools (`upsert_items`, `open_job`, `execute_plan`,
  `patch_plan`, `render`, and so on) ask for approval under Codex's default policy. Approve
  them, or choose to always allow the `art` server.

The hooks do two things:

- **Before every message**, they tell Codex what you changed in the editor since your
  last message.
- **After compaction**, they restore your pinned preferences word for word.

Without the hooks, the skills still re-read your pins and the current version at the
start of each job.

Optionally, paste [`AGENTS.md`](AGENTS.md) into your repo's `AGENTS.md`.

## Use

- Run `$art-setup` once to build your knowledge graph.
- Run `$art-tailor` with a job posting. Codex also picks either skill up when you ask in
  plain words.
