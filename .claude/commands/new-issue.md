---
allowed-tools: Bash(gh project *), Bash(gh api *), Bash(gh issue *)
---

Create a new GitHub issue, add it to the ART Development Plan project board, assess its dependencies, and set its initial status. `$ARGUMENTS` is the issue title (required).

If `$ARGUMENTS` is empty, output "Usage: /new-issue <title>" and stop.

**Step 1 — Fetch all open issues for dependency analysis**

Run:
```
gh issue list --state open --json number,title,body --limit 50
```

**Step 2 — Assess dependencies**

Review the open issue list and determine which issues (if any) the new issue depends on. An issue is a blocker if the new work cannot be started or completed without it. Be conservative — only list genuine blockers, not loose relations.

Compose a `## Dependencies` section:
- If blockers exist: `Blocked by #N, #M` (comma-separated)
- If none: `None`

**Step 3 — Create the issue**

Build a full issue body using this template:
```
## Summary
<1–2 sentence description derived from the title>

## Goals
- <goal 1>
- <goal 2>

## Acceptance criteria
- [ ] <criterion 1>
- [ ] <criterion 2>

## Dependencies
<from Step 2>
```

Run:
```
gh issue create --title "$ARGUMENTS" --body "<body>"
```

Capture the returned issue URL and extract the issue number from it.

**Step 4 — Add to project board**

Run:
```
gh project item-add 2 --owner nathansso --url <issue-url>
```

**Step 5 — Set initial status**

- If the issue has no blockers → status **Ready** (option `e18bf179`)
- If it has blockers → status **Backlog** (option `f75ad846`)

Look up the new item's ID from the project board:
```
gh project item-list 2 --owner nathansso --format json
```

Find the item whose `content.number` matches the new issue number, extract its `id`, then run:
```
gh api graphql -f query='mutation {
  updateProjectV2ItemFieldValue(input: {
    projectId: "PVT_kwHOCpdM7s4BXnLT"
    itemId: "<ITEM_ID>"
    fieldId: "PVTSSF_lAHOCpdM7s4BXnLTzhSy32k"
    value: { singleSelectOptionId: "<OPTION_ID>" }
  }) { projectV2Item { id } }
}'
```

**Step 6 — Display summary**

```
Created #<number> — <title>
Status: <Ready | Backlog>
URL: <url>
Dependencies: <None | Blocked by #N, #M>
```
