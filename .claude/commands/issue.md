---
allowed-tools: Bash(gh issue *), Bash(gh project *), Bash(gh api *)
---

Show full details for a GitHub issue. `$ARGUMENTS` is the issue number.

**Step 1 — Fetch issue details**

Run:
```
gh issue view $ARGUMENTS --json number,title,body,labels,assignees,state,url
```

**Step 2 — Fetch project board status**

Run:
```
gh project item-list 2 --owner nathansso --format json
```

Find the item whose `content.number` matches `$ARGUMENTS`. Extract its `status`.

**Step 3 — Display**

Show:
```
#<number> — <title>
Status: <project board status>   State: <open/closed>
URL: <url>
Labels: <comma-separated>
```

Then show the full issue body as-is (preserve markdown formatting).

If `$ARGUMENTS` is empty, prompt: "Usage: /issue <number>"
