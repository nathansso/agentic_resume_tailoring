---
description: Make an earlier version of a tailored resume current again
argument-hint: <job id or company/role> [version node id]
---

Revert a tailored resume with ART.

1. Find the job and the version from: $ARGUMENTS. If no version is named, use the
   parent of the current HEAD (`get_head`). If the job is unclear, call `list_jobs`
   and ask me.
2. Show me what I'd get back: `diff_nodes(HEAD, target)`. Ask me to confirm.
3. Call `checkout(job_id, node_id)`. Nothing is deleted: the later versions stay in
   `/art:history`.
4. Offer to `render` the restored version.
