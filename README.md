# ARTie — Agentic Resume Tailoring (ART)

**Tailor your resume to a job posting with the coding agent you already use.**

## What it does

ART keeps your experience as a knowledge graph: your roles, projects and skills, each skill
tied to the place you actually showed it. When you find a job, you hand the posting to your
coding agent (Claude Code or Codex), and it tailors your resume to that posting from the
graph. It shows you a plan first; once you approve it, ART checks every change, renders a
one-page PDF, and keeps each version, so you can see what changed and go back to an earlier
one.

Everything runs on your machine. Your data stays in a local database (`~/.art/art.db`), and
ART itself calls no AI model to write anything.

## Installation

In Claude Code, add the plugin:

```
/plugin marketplace add nathansso/agentic_resume_tailoring
/plugin install artie@artie
```

In Codex, the same plugin:

```bash
codex plugin marketplace add nathansso/agentic_resume_tailoring
codex plugin add artie@artie
```

Then run the setup:

```
/artie:setup
```

If `uv` or the `artie` command is missing, the setup installs them, asking before each
command, and tells you how to restart ART once. It then builds your knowledge graph from
your resume, LinkedIn export and project folders. (In Codex the setup is the `$artie-setup`
skill.)

To bring in your GitHub repos, sign in once in your own terminal (add `--private` for
private repos):

```bash
artie github login
```

You don't need to install LaTeX: the first time ART renders a PDF, it downloads its own
pinned engine (Tectonic). Then ask your agent in plain words:

> Tailor my resume to this posting: <paste the posting or a link>

It shows you a plan, with a reason for every change, and waits for your OK before anything is
saved. "Go back to the last version" and "save it to my Downloads" work the same way.

[`INSTALL.md`](INSTALL.md) covers registering the MCP server by hand, Codex hooks, the
editor, backups, building from source, and troubleshooting (offline starts and long paths
on Windows). The plugin's commands are listed in [`plugin/README.md`](plugin/README.md).

## How it works

ART splits the work between your agent and itself.

**Your agent reads and writes.** It reads your resume, your projects and the posting, and
it drafts the edits. That is the open-ended part, and the model you already use is good at
it.

**ART keeps what a model should not own:**

- **The facts.** The knowledge graph of your experience, with the evidence behind each
  skill.
- **Your approved bullets.** A library of bullet points you have signed off on, which the
  agent reuses instead of rewriting from scratch.
- **Your preferences and page rules.** Standing choices like "never mention Kafka" or "lead
  with Python", and the one-page limit.
- **The gates that enforce them.** Before an edit is saved, ART checks that each bullet
  cites real evidence from your graph, that numbers and names match it, that banned topics
  stay out, and that the page still fits. An edit that fails a check is refused.
- **The history.** Every tailored version is saved, so you can list what changed and revert.

The split keeps the agent's writing honest: it can phrase things well, but it cannot claim
something your graph doesn't support or drop a rule you set.

**Jev is optional.** For a few narrow yes-or-no questions (does this bullet say more than
its evidence, is this message a lasting preference), ART can ask TypeSafe's Jev if you give
it a key. Without one, every check runs on plain rules. What is sent, and how to turn it
off, is in [INSTALL.md § Jev](INSTALL.md#jev-optional-strengthen-the-checks).

**`artie ui` is the local editor.** It opens any version of a tailored resume in your browser
for hand edits, and your agent is told about those edits on your next message.

## Links

[`INSTALL.md`](INSTALL.md) · [`docs/harness.md`](docs/harness.md) (design) ·
[`docs/architecture.md`](docs/architecture.md) (how the code works today) ·
[`CHANGELOG.md`](CHANGELOG.md) · site: https://artie-sand.vercel.app

## License

Proprietary (`license = { text = "Proprietary" }` in `pyproject.toml`). All rights
reserved by the author; no license file is provided and no open-source license is granted.
