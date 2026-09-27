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
codex mcp add art -- uvx --from git+https://github.com/nathansso/agentic_resume_tailoring art-mcp
```

The plugin doesn't install hooks, because Codex plugins can't ship them. Install them
into `~/.codex/hooks.json`, or into `.codex/hooks.json` with `--project`:

```bash
uvx --from git+https://github.com/nathansso/agentic_resume_tailoring art hooks codex --write
```

Then run `/hooks` in Codex to review and trust them.

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
