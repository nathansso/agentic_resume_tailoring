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

## 7. Confirm

Call `list_items` again and summarize what is stored by kind. Point out anything that
looks duplicated or thin, and ask the user whether to fix it.
