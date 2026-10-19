---
description: Show every version of a tailored resume and what changed
argument-hint: [company or role]
---

Show me the history of a tailored resume with ART, following the `artie-tailor` skill's
"Which job" step.

1. Find the job: call `list_jobs` with `query` set to "$ARGUMENTS". One clear match: name it
   and go on. Several or none: ask me, "Is this the <Company> <Role> role, or another?"
   Never guess.
   With no argument, call `list_jobs` with no query. If I have exactly one job, name it and
   go on. Otherwise name the most recently touched one, list the next one or two, and ask
   "Is this the <Company> <Role> role, or another?" Never take the most recent job just
   because it is the most recent.
2. Call `history(job_id)` and list the versions oldest first: sequence number, source
   (host, editor, pipeline, revert), note, date, and which one is HEAD (from `get_head`).
3. For each version after the first, call `diff_nodes(parent, node)` and summarize the
   changes in one line: items added or removed, bullets changed, skills, and hand edits to
   the .tex or the layout.

If I want an earlier version back, I only have to say so ("go back to the one before the
Kafka change"); you do it with `checkout` and tell me what came back.
