---
allowed-tools: Bash(gh project *), Bash(gh api *), Bash(gh issue *), Bash(git *)
---

Mark an issue as **Done** on the ART Development Plan project board, close the GitHub issue, and unblock any issues that were waiting on it. `$ARGUMENTS` is the issue number.

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

**Step 3 — Close the GitHub issue**

Run:
```
gh issue close $ARGUMENTS
```

**Step 4 — Find newly unblocked issues**

For each Backlog item (status = "Backlog"), run:
```
gh issue view <number> --json body
```

Parse the `## Dependencies` section of the body. If it lists `Blocked by #<N>` and `<N>` equals `$ARGUMENTS`, that issue is now potentially unblocked.

For each potentially unblocked issue, also check whether any of its *other* blockers are still open (status not Done). Only move it to Ready if all its blockers are Done.

**Step 5 — Move newly unblocked issues to Ready**

For each confirmed-unblocked issue, run the GraphQL mutation with option `e18bf179` (Ready).

**Step 6 — Tear down the issue's worktree (if one exists)**

Issues started with `/work` have a sibling worktree named `art-issue-<number>`.
Check for it:
```
git worktree list
```
If a worktree whose path ends in `art-issue-$ARGUMENTS` is present, the work is
merged, so remove it and delete its local branch:
```
git worktree remove ../art-issue-$ARGUMENTS
git branch -D issue-$ARGUMENTS-<slug>
```
If `git worktree remove` reports the tree is dirty (e.g. its private `.artdata/`
SQLite DB), confirm with the user before re-running with `--force`. If no
matching worktree exists, skip this step silently — the issue was worked without
one.

**Step 7 — Display summary**

```
Done: #<number> — <title>
Worktree: removed (or "none")

Newly unblocked:
  #<n> — <title>  → Ready
  (none if no issues unblocked)
```
