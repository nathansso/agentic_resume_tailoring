---
name: art-setup
description: Build or refresh the user's ART knowledge graph from their resume, LinkedIn export and local project repos, and set their resume header. Use on first use of ART, when the user says their experience, projects or contact details changed, or when a tailoring run finds a fact missing or stale.
---

# Set up ART

ART stores the facts every tailored resume is built from. You read the user's sources
and fill ART's schemas. ART validates, deduplicates and stores the result. Store only
what the sources say. **Never invent or embellish.**

The tools named below are the ART MCP server's tools.

## 1. Find the sources

Ask the user where their materials are:

- a resume file (PDF, DOCX, Markdown or LaTeX);
- a LinkedIn data export;
- the local folders of projects they want considered.

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
  - **Project:** name, description, repo_url. For a local repo, describe what the code
    shows, and check any claim against the files.
  - **Skill:** name, category and a 1–5 proficiency from the evidence. Only skills the
    sources show.
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
