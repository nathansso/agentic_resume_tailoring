# ART for Claude Code

This plugin lets Claude Code tailor your resume to a job posting from your own knowledge
graph. Claude does the reading and writing; ART keeps your facts, your pinned
preferences, and every version, and it checks each change before committing it.

## Install

```
/plugin marketplace add nathansso/agentic_resume_tailoring
/plugin install artie@artie
```

It needs [uv](https://docs.astral.sh/uv/). If uv or the `artie` command is missing, `/artie:setup` installs them with your permission (uv's official installer, then `uv tool install`) and tells you how to restart ART once. The plugin runs ART with `uvx` from one pinned
release wheel (a GitHub Release asset, `v<version>`), which uv downloads on first use and
then keeps in its cache, so later starts don't contact GitHub and the editor (`artie ui`) is
already in it. The first start needs the network. On Windows, see "Offline starts, and
Windows" in [INSTALL.md](../INSTALL.md) for a short `UV_CACHE_DIR`.

You don't need to install LaTeX. The first time ART renders a PDF without a LaTeX engine, it
downloads a pinned [Tectonic](https://tectonic-typesetting.github.io) (0.16.9, checksum
verified) to `~/.art/bin`, and Tectonic fetches its TeX files on that first compile. A
tectonic or pdflatex already on your PATH is used instead. Set `ART_NO_DOWNLOAD=1` to turn
the download off; offline, or with it off, you get the `.tex` file.

Your data lives in `~/.art/art.db`, or `$ART_DATA_DIR`. Rendered resumes go to
`~/.art/applications/<Company>_<Role>/`.

## Use

| Command | What it does |
|---|---|
| `/artie:setup [files]` | Build your knowledge graph from your resume, LinkedIn export and project folders, and set your header |
| `/artie:tailor <posting or URL>` | Plan a tailored resume, wait for your approval, commit it and render the PDF |
| `/artie:open [job]` | Print the command that opens the local LaTeX editor (`artie ui`, from the release wheel: no build step) |
| `/artie:history [job]` | List every version and what changed |
| `/artie:revert <job> [version]` | Make an earlier version current again |
| `/artie:prefs` | Show your pinned rules, preferences and job-scoped rules |

The two skills behind these, `artie-tailor` and `artie-setup`, also trigger on their own
when you ask Claude to tailor a resume or update your experience.

## GitHub repos and LinkedIn (optional)

To connect your GitHub repos and save your LinkedIn URL, run this once in your own terminal
(add `--private` for private repos). It is safe to run again; `artie github login` is the
GitHub sign-in on its own:

```bash
artie connect
```

If `artie` is not on your PATH (you have not run `/artie:setup`'s install step), the same command is:

```bash
uvx --python 3.12 --from "art-mcp[ui] @ https://github.com/nathansso/agentic_resume_tailoring/releases/download/v0.2.0/art_mcp-0.2.0-py3-none-any.whl" artie connect
```

Claude can then call `list_github_repos`, pick the repos
worth reading, and clone one only after you agree. The token goes to your OS keychain, never
the ART database or an export; see "GitHub repos" in [INSTALL.md](../INSTALL.md) for the
`keyring` requirement and the commands.

## Hooks

- **Before every message:** if you edited a resume in the editor since your last
  message, Claude is told what you changed (the bullets you reworded, added or
  removed), so it builds on your edits. If a later tailoring run would drop a hand edit
  to the `.tex`, ART refuses it and Claude has to either carry your edit over or ask you
  before discarding it.
- **Also before every message:** ART's memory gate reads your message. If it may state a
  lasting preference ("never mention Kafka"), Claude is asked to confirm it with you and
  record it. A rule you call absolute ("never", a negative pin) is never saved without
  your confirmation. This works without a key: the gate only flags messages for Claude
  to confirm and saves nothing on its own. **Optional upgrade:** with a TypeSafe key
  (`TYPESAFE_API_KEY`), the gate asks TypeSafe's Jev, and a clear, low-stakes preference
  ("lead with Python") ART saves itself and Claude tells you (your message leaves your
  machine in that request). With a key, a message that leans on Claude's last
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
