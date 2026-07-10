---
allowed-tools: Bash(gh project *), Bash(gh api *), Bash(gh issue *), Bash(git *), Bash(./scripts/new-agent-worktree.ps1 *), Bash(pwsh *), Bash(powershell *)
---

Start working on a GitHub issue in its own isolated git worktree, and move it to
**In Progress** on the ART Development Plan project board. `$ARGUMENTS` is the
issue number, optionally followed by flags to pass through to the worktree
script (e.g. `/work 95 -Frontend -Launch`).

Run this from the **primary checkout** (your dispatcher session). It sets up a
sibling worktree so a *separate* Claude session can drive the issue in its own
terminal. This command does not write code — it only provisions the worktree and
syncs the board.

If `$ARGUMENTS` is empty, output "Usage: /work <issue-number> [-Frontend] [-Launch]" and stop.

**Step 1 — Read the issue**

Parse the first token of `$ARGUMENTS` as the issue number; treat any remaining
tokens as pass-through flags for the script (default: none).

Run:
```
gh issue view <number> --json number,title,url,state
```

If the issue is already `CLOSED`, warn the user and ask whether to continue.

Derive a short kebab-case slug from the title: lowercase, spaces → `-`, strip
anything that is not `a-z 0-9 -`, collapse repeats, trim to ~5 words.
Example: "Fix PDF export on Safari" → `fix-pdf-export-on-safari`.

**Step 2 — Make sure main is current**

The worktree branches from `main`, so refresh it first:
```
git fetch origin
```
Use `origin/main` as the base if local `main` is behind (pass `-BaseRef origin/main`).

**Step 3 — Create the worktree**

Run the bootstrap script (via PowerShell on Windows). Name and branch both
encode the issue number so git and the board stay in sync:
```
pwsh -File ./scripts/new-agent-worktree.ps1 -Name issue-<number> -Branch issue-<number>-<slug> <pass-through-flags>
```
If `pwsh` is unavailable, use `powershell -File` instead. The script prints the
worktree path, assigned ports, and the launch recipe — relay that output to the
user verbatim.

If the script fails because the worktree or branch already exists, tell the user
it looks like work on this issue is already provisioned, show `git worktree list`,
and stop rather than clobbering it.

**Step 4 — Move the board card to In Progress**

Find the item whose `content.number` matches the issue number:
```
gh project item-list 2 --owner nathansso --format json
```
Then run the GraphQL mutation (option `47fc9ee4` = In Progress):
```
gh api graphql -f query='mutation($proj:ID!,$item:ID!,$field:ID!,$opt:String!){
  updateProjectV2ItemFieldValue(input:{projectId:$proj,itemId:$item,fieldId:$field,value:{singleSelectOptionId:$opt}}){projectV2Item{id}}
}' -f proj="PVT_kwHOCpdM7s4BXnLT" -f item="<ITEM_ID>" -f field="PVTSSF_lAHOCpdM7s4BXnLTzhSy32k" -f opt="47fc9ee4"
```
If the issue is not on the board, add it first (`gh project item-add 2 --owner nathansso --url <url>`) then set the status.

**Step 5 — Display summary**

```
Working on #<number> — <title>

  Worktree: <path>
  Branch:   issue-<number>-<slug>
  Ports:    backend <BE> / frontend <FE>
  Board:    In Progress

Next: open a terminal there and start an agent
  cd '<path>'
  claude

(or re-run with -Launch to open it automatically)

When the PR is merged, run:  /done <number>
```
