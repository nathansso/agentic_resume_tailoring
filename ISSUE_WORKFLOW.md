# Issue Resolution Workflow

Follow this workflow whenever you are assigned a GitHub issue or asked to resolve a project to-do.

---

## Phase 1 — Ingest & Assess

- Read the full issue: title, body, labels, linked PRs or commits, and all existing comments.
- Identify the affected modules, files, and architectural layers.
- Classify the issue type: bug, feature, refactor, or docs.
- Surface any acceptance criteria explicitly stated in the issue.
- Do not touch code in this phase.

---

## Phase 2 — Plan

Produce a comprehensive written plan before making any changes. The plan must cover:

- **Problem statement** — what is broken or missing, and why (root cause for bugs).
- **Implementation approach** — what will change and how, at the file level.
- **Test strategy** — what new tests will be added, and which existing tests verify the behavior.
- **Risks and edge cases** — backward-compatibility concerns, schema impact, contract changes.

Present the plan in full to the user. Do not proceed to implementation until the user approves.

---

## Phase 3 — Review & Revise

- Incorporate user feedback and revise the plan.
- Repeat until the user explicitly approves the plan.
- **Gate: implementation does not begin until the user gives explicit approval.**

---

## Phase 4 — Implementation

- Follow the approved plan exactly. Stay scoped to it.
- Write tests alongside each change — not after.
- Run the full test suite before committing:

  ```bash
  python run_tests.py
  ```

- All tests must pass. Do not commit with failing tests.
- If out-of-scope issues are discovered, surface them as a comment or new issue — do not fix them unilaterally.

---

## Phase 5 — Commit

- Commit using the repo format: `type(scope): short description`
- Keep one logical unit of work per commit.
- Reference the issue number in the commit body if applicable, e.g.:

  ```
  fix(chat): handle empty router response gracefully

  Closes #42 (pending user review — do not auto-merge)
  ```

---

## Phase 6 — Post Update to Issue

After committing, post a comment on the GitHub issue using `gh issue comment <number> --body "..."` that includes:

- What was implemented and which files changed.
- Which tests were added and confirmation that the suite passes.
- Any known limitations, follow-up items, or out-of-scope findings.

**Do not close or resolve the issue.** Leave that action for the user after they review the implementation.

---

## Hard Rules

These apply in every session, without exception:

1. **Never close an issue automatically.**
2. **Never skip the planning phase** — no code before an approved plan.
3. **Tests must pass before any commit.**
4. **Stay scoped** — surface out-of-scope findings as comments, not unilateral changes.
