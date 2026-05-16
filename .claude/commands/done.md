---
allowed-tools: Bash(gh project *), Bash(gh api *), Bash(gh issue *)
---

Mark an issue as **Done** on the ART Development Plan project board and unblock any issues that were waiting on it. `$ARGUMENTS` is the issue number.

If `$ARGUMENTS` is empty, output "Usage: /done <issue-number>" and stop.

**Step 1 — Fetch all project items**

Run:
```
gh project item-list 2 --owner nathansso --format json
```

Save the full list.

**Step 2 — Move the target issue to Done**

Find the item whose `content.number` matches `$ARGUMENTS`. Extract its `id`.

Run the GraphQL mutation:
```
gh api graphql -f query='mutation {
  updateProjectV2ItemFieldValue(input: {
    projectId: "PVT_kwHOCpdM7s4BXnLT"
    itemId: "<ITEM_ID>"
    fieldId: "PVTSSF_lAHOCpdM7s4BXnLTzhSy32k"
    value: { singleSelectOptionId: "98236657" }
  }) { projectV2Item { id } }
}'
```

**Step 3 — Find newly unblocked issues**

For each Backlog item (status = "Backlog"), run:
```
gh issue view <number> --json body
```

Parse the `## Dependencies` section of the body. If it lists `Blocked by #<N>` and `<N>` equals `$ARGUMENTS`, that issue is now potentially unblocked.

For each potentially unblocked issue, also check whether any of its *other* blockers are still open (status not Done). Only move it to Ready if all its blockers are Done.

**Step 4 — Move newly unblocked issues to Ready**

For each confirmed-unblocked issue, run the GraphQL mutation with option `e18bf179` (Ready).

**Step 5 — Display summary**

```
Done: #<number> — <title>

Newly unblocked:
  #<n> — <title>  → Ready
  (none if no issues unblocked)
```
