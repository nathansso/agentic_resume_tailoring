---
allowed-tools: Bash(gh project *), Bash(gh api *), Bash(gh issue *)
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
gh issue view $ARGUMENTS --json number,title,body,url
```

Display:
```
Started #<number> — <title>
<url>
```

Then print the full issue body so the work scope is visible.
