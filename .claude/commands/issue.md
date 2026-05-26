---
allowed-tools: Bash(gh issue *), Bash(gh project *), Bash(gh api *)
---

Show full details for a GitHub issue. `$ARGUMENTS` is the issue number.

If `$ARGUMENTS` is empty, output "Usage: /issue <number>" and stop.

**Step 1 — Fetch issue details and comments**

Run:
```
gh issue view $ARGUMENTS --json number,title,body,comments,labels,assignees,state,url
```

**Step 2 — Fetch project board status**

Run:
```
gh project item-list 2 --owner nathansso --format json
```

Find the item whose `content.number` matches `$ARGUMENTS`. Extract its `status`.

**Step 3 — Display**

Show the header first:
```
#<number> — <title>
Status: <project board status>   State: <open/closed>
URL: <url>
Labels: <comma-separated or none>
```

Then check `comments` for one whose body starts with `## Implementation Plan`. If found, show it prominently **before** the issue body:

```
─────────────────────────────────────
Implementation Plan:
<plan comment body>
─────────────────────────────────────
```

Then show the full issue body as-is (preserve markdown formatting).

If there is no implementation plan comment, show the issue body directly with no separator.
