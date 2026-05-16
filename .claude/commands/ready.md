---
allowed-tools: Bash(gh project *), Bash(gh api *), Bash(gh issue *)
---

Show all issues with **Ready** status on the ART Development Plan project (number 2, owner `nathansso`).

**Step 1 — Fetch project items**

Run:
```
gh project item-list 2 --owner nathansso --format json
```

**Step 2 — Filter to Ready**

From the JSON, collect items where `status` equals `"Ready"`. For each item that has a `content` field:
- Extract the issue number and URL from `content`
- Run `gh issue view <number> --json title,body,labels` to get the title and body

**Step 3 — Display**

Show a header: **Ready to work on** (N issues)

For each ready issue, show:
```
#<number> — <title>
<url>
```

Then extract and show the `## Dependencies` section from the issue body (if present) as a one-liner beneath the title, e.g. `Dependencies: Blocked by #14` or `Dependencies: None`.

If no issues are Ready, say so and suggest running `/projects` to see the full board.
