# ART for Claude Code

This plugin lets Claude Code tailor your resume to a job posting from your own knowledge
graph. Claude does the reading and writing; ART keeps your facts, your pinned
preferences, and every version, and it checks each change before committing it.

## Install

```
/plugin marketplace add nathansso/agentic_resume_tailoring
/plugin install art@art
```

It needs [uv](https://docs.astral.sh/uv/). The plugin runs ART with `uvx` from one pinned
release wheel (a GitHub Release asset, `v<version>`), which uv downloads on first use and
then keeps in its cache, so later starts don't contact GitHub and the editor (`art ui`) is
already in it. The first start needs the network. On Windows, see "Offline starts, and
Windows" in [INSTALL.md](../INSTALL.md) for a short `UV_CACHE_DIR`. For PDFs you also need [tectonic](https://tectonic-typesetting.github.io)
or pdflatex on your PATH. Without either, you get the `.tex` file.

Your data lives in `~/.art/art.db`, or `$ART_DATA_DIR`. Rendered resumes go to
`~/.art/applications/<Company>_<Role>/`.

## Use

| Command | What it does |
|---|---|
| `/art:setup [files]` | Build your knowledge graph from your resume, LinkedIn export and project folders, and set your header |
| `/art:tailor <posting or URL>` | Plan a tailored resume, wait for your approval, commit it and render the PDF |
| `/art:open [job]` | Print the command that opens the local LaTeX editor (`art ui`, from the release wheel: no build step) |
| `/art:history [job]` | List every version and what changed |
| `/art:revert <job> [version]` | Make an earlier version current again |
| `/art:prefs` | Show your pinned rules, preferences and job-scoped rules |

The two skills behind these, `art-tailor` and `art-setup`, also trigger on their own
when you ask Claude to tailor a resume or update your experience.

## Hooks

- **Before every message:** if you edited a resume in the editor since your last
  message, Claude is told what you changed (the bullets you reworded, added or
  removed), so it builds on your edits. If a later tailoring run would drop a hand edit
  to the `.tex`, ART refuses it and Claude has to either carry your edit over or ask you
  before discarding it.
- **Also before every message:** ART's memory gate reads your message. If it may state a
  lasting preference ("never mention Kafka"), Claude is asked to confirm it with you and
  record it. A clear, low-stakes one ("lead with Python") ART saves itself and Claude
  tells you. A rule you call absolute ("never", a negative pin) is never saved without
  your confirmation. The gate asks TypeSafe's Jev when `TYPESAFE_API_KEY` is set (your
  message leaves your machine in that request); without a key it only flags messages for
  Claude to confirm and saves nothing on its own. A message that leans on Claude's last
  reply ("never list that again") is sent with the end of that reply (at most 600
  characters of its text, read from the transcript Claude Code passes the hook), so ART can
  tell what "that" is. No other message carries a reply. To set a key, see what Jev is sent, or
  switch it off (`ART_JEV_MODE=off`), see "Jev (optional)" in [INSTALL.md](../INSTALL.md).
- **After compaction:** your pinned preferences are re-injected word for word, along
  with the job you were working on.

The hooks run offline (`uvx --offline`, from what the MCP server's start cached) and end
in `; exit 0`. If uvx or the cache is missing, or anything else fails, a hook exits 0 and
says nothing rather than blocking your message; until the server has started once, they do
nothing.

## Not yet

Scoring a result as feedback (`record_feedback`) is planned and not part of this version.
