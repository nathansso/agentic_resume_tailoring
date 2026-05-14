---
allowed-tools: Bash(gh project *), Bash(gh api *)
---

Follow these steps to display GitHub project items.

**Step 1 — Resolve the target project**

Run `gh project list` to get all projects.

- If `$ARGUMENTS` is non-empty, match it against the project list by number or by name (case-insensitive substring). Use the first match.
- If `$ARGUMENTS` is empty, use the first project in the list.

**Step 2 — Fetch the owner**

Run `gh api graphql -f query='{ viewer { login } }' --jq '.data.viewer.login'` to get the authenticated GitHub username.

**Step 3 — Fetch items**

Run `gh project item-list <PROJECT_NUMBER> --owner <OWNER> --format json` on the resolved project.

**Step 4 — Display**

Show:
1. A header line: **Project: <name>** (project number in parentheses)
2. Items grouped by `status`, each item formatted as:
   - `[#<number>](<url>) — <title>` where number and url come from the item's `content` field

If an item has no linked content (e.g. a draft note), show `(draft) — <title>` with no link.

Show the status groups in this order: In Progress → Todo → Backlog → Done → everything else.
