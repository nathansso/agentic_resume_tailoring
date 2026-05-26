---
allowed-tools: Bash(gh project *), Bash(gh api *), Bash(gh issue *), EnterPlanMode, ExitPlanMode
---

Move an issue to **In Progress** on the ART Development Plan project board. `$ARGUMENTS` is the issue number.

If `$ARGUMENTS` is empty, output "Usage: /start <issue-number>" and stop.

**Step 1 — Find the item ID**

Run:
```
gh project item-list 2 --owner nathansso --format json
```

Find the item whose `content.number` matches `$ARGUMENTS`. Extract its `id` (the item ID, not the issue number).

**Step 2 — Move to In Progress**

Run the GraphQL mutation:
```
gh api graphql -f query='mutation {
  updateProjectV2ItemFieldValue(input: {
    projectId: "PVT_kwHOCpdM7s4BXnLT"
    itemId: "<ITEM_ID>"
    fieldId: "PVTSSF_lAHOCpdM7s4BXnLTzhSy32k"
    value: { singleSelectOptionId: "47fc9ee4" }
  }) { projectV2Item { id } }
}'
```

**Step 3 — Show the issue**

Run:
```
gh issue view $ARGUMENTS --json number,title,body,comments,url
```

Display:
```
Started #<number> — <title>
<url>
```

Then print the full issue body so the work scope is visible.

**Step 4 — Check for an existing implementation plan**

Scan the issue's `comments` for one whose body starts with `## Implementation Plan`. If one exists:

- Display it prominently:
  ```
  ─────────────────────────────────────
  Existing Implementation Plan found:
  <plan comment body>
  ─────────────────────────────────────
  ```
- Ask the user: "An implementation plan already exists for this issue. Continue with this plan, or enter plan mode to re-plan?"
- If the user says **continue**: skip Step 5 entirely and summarise the next implementation steps from the existing plan.
- If the user says **re-plan**: proceed to Step 5.

If no plan comment exists, proceed directly to Step 5.

**Step 5 — Brainstorm implementation in plan mode**

Call `EnterPlanMode` to switch into plan mode, then brainstorm a concrete implementation plan for the issue: key files to touch, approach, open questions, and any risks.

When the plan is ready and the user approves it via `ExitPlanMode`, immediately post the plan file contents as a comment on the issue:

```
gh issue comment $ARGUMENTS --body "## Implementation Plan

<contents of the plan .md file>"
```

Display the comment URL so the user can verify it was posted.
