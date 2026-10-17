---
name: art-setup
description: Build or refresh the user's ART knowledge graph from their resume, LinkedIn export and local project repos, and set their resume header. Use on first use of ART, when the user says their experience, projects or contact details changed, when a tailoring run finds a fact missing or stale, or when ART's tools are missing or fail to start (it installs uv and the `art` command, with the user's consent).
---

# Set up ART

ART stores the facts every tailored resume is built from. You read the user's sources
and fill ART's schemas. ART validates, deduplicates and stores the result. Store only
what the sources say. **Never invent or embellish.**

The tools named below are the ART MCP server's tools.

## 0. Preflight: make sure ART runs

Call `get_profile` first. If it answers, ART is running: go to step 1.

If ART's tools are missing or fail to start, `uv` (which runs ART) is usually missing, or
not on the PATH the host started from. Diagnose before you change anything, and **never
install anything without the user's yes.**

1. **Find the OS.** Run `uname -s` in the shell. `Darwin` is macOS, `Linux` is Linux,
   and `MINGW…`, `MSYS…` or `CYGWIN…` is Windows (Git Bash). If `uname` is not a command,
   you are in PowerShell on Windows.
2. **Look for uv and ART.** Run `uv --version` and `art --list`. If `uv` is not found,
   look in uv's default folder: `~/.local/bin/uv` on macOS and Linux, and on Windows
   `%USERPROFILE%\.local\bin\uv.exe` (`"$HOME/.local/bin/uv.exe"` from Git Bash,
   `& "$env:USERPROFILE\.local\bin\uv.exe"` from PowerShell). Then:
   - `art --list` prints ART's tools as JSON: ART is installed and the server only needs
     to reconnect. Go to 5.
   - uv works, by name or by its full path, but `art` does not: go to 4.
   - No uv anywhere: go to 3.
3. **Install uv, with consent.** Tell the user you will run uv's official installer, show
   the exact command for their OS, and wait for a clear yes:
   - macOS and Linux:
     ```bash
     curl -LsSf https://astral.sh/uv/install.sh | sh
     ```
   - Windows:
     ```powershell
     powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
     ```
   It installs uv into the default folder above and adds that folder to PATH for new
   terminals. It needs no sudo, no administrator rights and no Homebrew; don't use them.
   If the user declines, give them the line for their OS to run themselves (uv's
   install page is https://docs.astral.sh/uv/getting-started/installation/), and stop.
   Once it finishes, your shell may still have the old PATH, so check with the full path:
   `~/.local/bin/uv --version` (macOS, Linux), `"$HOME/.local/bin/uv.exe" --version`
   (Git Bash) or `& "$env:USERPROFILE\.local\bin\uv.exe" --version` (PowerShell).
4. **Install ART as a command, with consent.** Show the user these two commands, with the
   same pinned release the plugin runs, and wait for a yes:
   ```bash
   uv tool install --python 3.12 "art-mcp[ui] @ https://github.com/nathansso/agentic_resume_tailoring/releases/download/v0.1.1/art_mcp-0.1.1-py3-none-any.whl"
   uv tool update-shell
   ```
   Call `uv` by its full path from 2 or 3 if it is not on your PATH yet. The first puts
   `art` in uv's tool folder (the same default folder), on Python 3.12, which the plugin's
   `uvx` also asks for, so it starts from that install without resolving it again. Keep
   `--python 3.12`: without it a newer Python uv finds later would stop that reuse. The second makes sure the folder is on
   PATH for new terminals and apps. Check it: `art --list` (or `~/.local/bin/art --list`,
   `"$HOME/.local/bin/art.exe" --list`, `& "$env:USERPROFILE\.local\bin\art.exe" --list`)
   prints ART's tools as JSON. If the user declines, give them the `uv tool install` line
   to run themselves, and stop. On Windows, an install that fails on a long path needs a
   short uv cache: with the user's yes, run
   `[Environment]::SetEnvironmentVariable("UV_CACHE_DIR", "C:\uvc", "User")` in
   PowerShell, open a new shell and install again.
5. **Restart once.** The host tried to start ART before uv was there, and doesn't retry on
   its own. Tell the user exactly what to do for their host:
   - **Claude Code in a terminal:** run `/mcp`, pick the `art` server and reconnect. If it
     still fails, the terminal predates the PATH change: quit Claude Code, open a new
     terminal and start it again.
   - **Claude desktop app:** quit it fully (not just its window) and open it again. On
     macOS an app opened from the Dock may not see a PATH change made in a shell profile;
     if the tools still don't answer, say so rather than editing the plugin's files.
   - **Codex:** quit Codex and start it again from a new terminal.

   Then ask them to run the setup again. It starts here, finds the tools, and carries on
   with step 1.

## 1. Find the sources

Ask the user where their materials are:

- a resume file (PDF, DOCX, Markdown or LaTeX);
- a LinkedIn data export;
- the local folders of projects they want considered;
- their GitHub repos. If they want these, they run `art github login` in their own terminal
  (`--private` adds private repos; `list_github_repos`'s `not_logged_in` error repeats the
  command). If `art` is not on their PATH, the same command is
  `uvx --python 3.12 --from "art-mcp[ui] @ https://github.com/nathansso/agentic_resume_tailoring/releases/download/v0.1.1/art_mcp-0.1.1-py3-none-any.whl" art github login`.
  Then you call `list_github_repos`, pick the repos worth
  reading with them, and clone one only after they agree. Store what you read with
  `source: "github:<repo>"` (each repo's `evidence_source`). Never ask for or handle a token.

Read them yourself. ART does not read files.

## 2. See what ART already holds

- Call `list_items` and `get_profile`.
- Items the user edited by hand are authoritative. ART will not change them, and you
  should not try.

## 3. Fill and store the records

- For each kind you have data for (`experience`, `education`, `project`, `skill`,
  `achievement`), call `ingest_schema(kind)` and fill one record per item. Use the
  fields exactly as named; unknown fields are rejected.
  - **Experience:** title, company, dates and bullets **verbatim**, one bullet per line
    of the source.
  - **Education:** `end_date` is the graduation date.
  - **Project:** name, a short `description` (what it is, one or two sentences; no
    tagline pasted in front of it), repo_url, and, separately, `blurbs`: one-line resume
    bullets, each saying what it does and the result or tech. The description renders
    verbatim as the project's only bullet when there are no blurbs, so write blurbs for
    anything meant to reach the page. For a local repo, describe what the code shows,
    and check any claim against the files. `get_item` marks a project with no blurbs and
    a description that is not one short sentence as `incomplete` (`missing: ["blurbs"]`):
    offer to write its bullets and store them with `upsert_items`.
  - **Skill:** name, category and a 1–5 proficiency from the evidence. Only skills the
    sources show. Always fill `category`, one of `Languages & Libraries`,
    `AI & Machine Learning`, `Data Engineering`, `Tools` or `Cloud`: the resume groups
    skills by it, and a skill without one lands under "Other". ART infers a missing
    category for common skills but not for the rest, and never replaces one you set.
- Call `upsert_items({records, source})` with `source` set to where the records came
  from (`resume.pdf`, `github:<repo>`, `linkedin`). Records are
  `{"kind": ..., "data": {...}}`.
- Read the results:
  - `created`, `merged` and `unchanged` are fine.
  - `invalid` shows its message. Fix the record and resend it.
  - `skipped_manual`: the user edited that item; leave it.
  - `skipped_tombstone`: the user deleted that item; don't re-add it unless they ask.
- **Stale facts.** When a stored fact is out of date (a role that ended but still reads
  "Present"), resend that record with `"correct": true` and only the corrected
  fields.

## 4. Say where each project was done

A project was done in a role, for a degree, or on its own. Tailoring weighs work
above coursework, so this matters.

- Call `suggest_project_contexts`. Each unreviewed project comes with the roles or
  degrees ART's rules point to (an employer's name in the repo name, a course code),
  or none.
- Show the user the list and ask them to confirm, correct or answer each one. Never
  store a suggestion they have not confirmed.
- Call `set_project_context({project, context})` for each answer: an `exp:` or `edu:`
  key from `list_items`, or `personal`.
- An award won for a project (a hackathon placing) is an achievement. Store it, then
  call `link_achievement({achievement, project})`.

## 5. Set the header

Call `update_profile({fields})` with the name, email, phone, location, linkedin_url,
github_username and portfolio_url the user gave you or that appear on their resume.

## 6. Job-scoped rules

If the user says a fact depends on the posting (for example "graduation is June 2027,
but Dec 2027 when a role needs me enrolled after the internship"), record a rule with
`upsert_items` and kind `rule`, using `ingest_schema("rule")`:

- `item_key` from `list_items`;
- `field` (for example `end_date`);
- a yes/no `question` about a posting;
- `value_if_yes`, and `value_if_no` if the value otherwise differs.

Tailoring then asks that question of every posting.

## 7. Their curated bullet library and track baselines

If the user keeps approved bullet wordings of their own (a library file, a doc, tailored
resumes they are proud of), import them. They wrote it, so it arrives approved and
tailoring starts from it instead of regenerating.

- Call `ingest_schema("variant")`, and fill one record per bullet with `upsert_items`, kind
  `variant`: `item_key` (the experience or project from `list_items`), `text` exactly as the
  user has it, and optionally `cites` (the item key, or `<key>#b<n>` for the source bullet it
  rewrites; omit it to cite the item) and `tags: {track}`.
- Read the results: `created`, `merged` and `unchanged` are fine. `invalid` names the problem,
  usually an `item_key` that is not in the graph yet; store the item first.
- Import only wording the user gave you. Never write a variant yourself; a bullet from a
  tailoring run becomes a variant only through `promote_bullet` and the user's approval.
- A resume the user wants as the starting point for a kind of role: find its version with
  `history`, then call `save_baseline(node_id, track)`. Name the track after the role family
  (`data_science`, `machine_learning`, `software_engineering`, ...). A new job then starts
  as a copy of the saved track that fits it: Jev chooses among the saved tracks by reading
  the posting, and the job's title is one of the things it sees about each track (the title
  of the job the baseline came from), so save a baseline from a job with a representative
  title. A `role_family` you give `open_job` that names a saved track exactly wins without
  asking Jev. Saving a track again replaces its baseline.

## 8. Import their standing preferences

ART remembers new statements on its own, but not the rules the user already follows. On a
first setup, or when they mention rules they apply to every resume, ask for them: things
to never write, things to always include, how they want dates or wording handled. Then:

- Call `art_pins` to see what ART already holds, so you don't record it twice.
- For each rule the user confirms, call `record_preference` with their own words as
  `text`, a `polarity` (`emphasize`, `suppress`, or `reframe` for a format rule), the
  `target` (a key from `list_items`, `section:<name>` or a topic), and a `strength`.
  Strength 5 is for rules they call non-negotiable ("never"); a 5 suppression becomes a
  negative pin that can never reach the page.
- Never record a rule the user did not state or confirm.

## 9. Confirm

Call `list_items` again and summarize what is stored by kind. Point out anything that
looks duplicated or thin, and ask the user whether to fix it.

End setup with one `render` of the user's baseline (a job with a saved track or committed version; skip it when there is none), so the LaTeX engine and its TeX files are fetched now rather than on the first tailoring.

## 10. Back up, or bring a web-app profile over

ART's data is local, with no server copy. These are shell commands, not tools, and you run
them only when the user asks. Neither writes to anything but a zip file (export) or the
local store (import).

- **Back up:** `art export --out <path>.zip` writes the profile and the `applications/`
  folder as one zip, with no keys or passwords. Suggest it before a restore, a machine
  change, or a risky edit. Add `--user-id <uuid>` if the user has more than one profile.
- **Restore:** `art import <path>.zip`. It refuses a profile that already has data; the
  user chooses `--merge` (add what is missing) or `--replace --confirm-replace` (delete
  first). Run it with `--dry-run` first and show the counts.
- **One-time migration from the hosted web app:** the user gives you their web-app
  profile id (the `user_id` of their row, not a Supabase Auth uid) and the web app's
  Postgres URL. Put the URL in `ART_IMPORT_SOURCE_URL` rather than on the command line,
  run `art import --from-supabase --user-id <uuid> --dry-run`, show the counts and
  warnings, and only then run it for real (add `--set-active` to make it the profile ART
  uses). Never guess the id, never use a `@local` or `user@example.com` profile, and never
  read a database URL from a `.env` file yourself.
