---
description: Show every version of a tailored resume and what changed
argument-hint: [job id, or company/role]
---

Show me the history of a tailored resume with ART.

1. Find the job: $ARGUMENTS. If that's empty, call `list_jobs` and use the job I most
   recently worked on.
2. Call `history(job_id)` and list the versions oldest first: sequence number,
   source (host, editor, pipeline, revert), note, date, and which one is HEAD (from
   `get_head`).
3. For each version after the first, call `diff_nodes(parent, node)` and summarize
   the changes in one line: items added or removed, bullets changed, skills, and
   hand edits to the .tex or the layout.

Offer `/art:revert` if I want an earlier version back.
