# Parallel Claude Code agents with git worktrees

Run several **human-steered** Claude Code sessions on this repo at once — one per
issue — without them clobbering each other's files, branches, ports, or data.

This guide covers the whole loop: the mental model, one-time setup, the day-to-day
`/work` → code → PR → `/done` cycle, running the app inside a worktree when you
need to, and cleanup.

---

## Mental model

```
                 ┌─────────────────────────────┐
                 │  Dispatcher session          │
                 │  (primary checkout, `main`)  │
                 │  /ready  /work N  /done N     │
                 └───────────┬─────────────────┘
                             │ /work 95 provisions…
          ┌──────────────────┼──────────────────┐
          ▼                  ▼                  ▼
   ../art-issue-95    ../art-issue-102    ../art-issue-110
   branch issue-95    branch issue-102    branch issue-110
   ports 8001/5174    ports 8002/5175     ports 8003/5176
   private SQLite     private SQLite      private SQLite
   ─ its own claude   ─ its own claude    ─ its own claude
     session            session             session
```

- **One dispatcher session** in your primary checkout. It never writes code — you
  use it to pick issues (`/ready`), spin up worktrees (`/work N`), and close them
  out (`/done N`). The project board lives here.
- **One worktree per issue.** Each is a full working copy on its own branch,
  backed by the same `.git`. A separate `claude` session drives each one in its
  own terminal.
- Best for issues that touch **distinct parts of the repo** — those never
  conflict. Two issues in the *same* module still work, but you reconcile at PR
  time, so don't pair those.

### What is and isn't shared

| Thing | Shared across worktrees? | Handled by |
|---|---|---|
| Git history (`.git`) | ✅ shared | git worktrees |
| Source files / working tree | ❌ isolated | separate worktree dirs |
| Branch | ❌ one branch per worktree | `issue-<N>-<slug>` |
| `.venv` | ✅ reused (repo root) | activate the shared venv |
| `.env` (secrets) | ❌ copied per worktree | bootstrap script |
| Database | ❌ private SQLite per worktree | `ART_DATA_DIR` / `DATABASE_URL` override |
| Dev ports | ❌ distinct per worktree | `-Index` → `8000+N` / `5173+N` |

> Git enforces that a branch is checked out in only one worktree at a time, so
> each agent **must** be on its own branch — which is exactly what you want, so
> each can commit independently.

---

## One-time setup

Nothing to install — the shared `.venv` at the repo root already has every
dependency. Just keep your primary checkout's `main` current so new worktrees
branch from fresh code:

```powershell
git checkout main
git pull
```

The tooling this guide relies on:

- `scripts/new-agent-worktree.ps1` — provisions an isolated worktree.
- `.claude/commands/work.md` — the `/work` slash command (wraps the script + board).
- `.claude/commands/done.md` — `/done` now also tears the worktree down.
- `web/frontend/vite.config.ts` — the dev proxy reads `VITE_API_PORT` (default
  8000) so each worktree's frontend can reach its own backend.

---

## Day-to-day loop

### 1. Pick disjoint issues (dispatcher session)

```
/ready
```

Choose a handful that touch different areas — e.g. one in `web/routers/`, one in
`database/`, one in `agents/`.

### 2. Start each issue

```
/work 95
```

`/work 95`:

1. reads the issue title and derives a slug,
2. runs the bootstrap script → `../art-issue-95` on branch `issue-95-<slug>`,
3. moves the board card to **In Progress**,
4. prints the worktree path, assigned ports, and the launch recipe.

Useful pass-through flags:

| Flag | When to use |
|---|---|
| `-Frontend` | the issue needs the React app running (installs `node_modules`) |
| `-Launch` | auto-open a new Windows Terminal tab already running `claude` |
| `-SharedDb` | the agent needs your real seeded Postgres data (read-mostly work) |
| `-Index N` | force a specific port offset if you hit a clash |

Example: `/work 95 -Frontend -Launch`

### 3. Drive the agent in its own terminal

If you didn't pass `-Launch`, open a terminal yourself:

```powershell
cd 'C:\Users\...\art-issue-95'
claude
```

Then tell that session which issue it's on and let it work. It edits, runs
`python run_tests.py` (DB-isolated, so no cross-agent interference), and — when
it needs to verify against the live app — launches the app itself on this
worktree's ports.

### 4. Land the work (from the worktree)

```powershell
cd 'C:\Users\...\art-issue-95'
git add -A
git commit -m "fix(scope): ..."
git push -u origin issue-95-<slug>
gh pr create --fill
```

Merge the PR on GitHub. Because each agent is on its own branch, PRs don't
conflict at the git level unless they touch the same lines.

### 5. Close it out (dispatcher session)

```
/done 95
```

`/done 95` moves the board card to **Done**, closes the issue, unblocks any
dependents (→ Ready), and **removes the `art-issue-95` worktree + local branch**.

---

## Running the app inside a worktree

You usually won't, but when an agent (or you) wants to see the app live, run its
printed recipe — two terminals, using this worktree's assigned ports:

```powershell
# Terminal 1 — backend (reuses the shared repo-root venv)
& 'C:\Users\...\agentic_resume_tailoring\.venv\Scripts\Activate.ps1'
cd 'C:\Users\...\art-issue-95'
uvicorn web.app:app --port 8001 --reload

# Terminal 2 — frontend
cd 'C:\Users\...\art-issue-95/web/frontend'
$env:VITE_API_PORT=8001; npm run dev -- --port 5174
```

App is at **http://localhost:5174**. Notes:

- The **DB starts empty** (private SQLite). Register a local user in the UI —
  local cookie auth is used because the `supabase` package is absent locally.
- Need real data instead? Provision that worktree with `-SharedDb` so it uses the
  shared Postgres from `.env` (accepting that concurrent runs share one database).
- `-Frontend` must have been passed at creation (or run `npm install` in
  `web/frontend/` yourself) before the Vite server will start.

---

## Housekeeping

```powershell
git worktree list                          # see all active worktrees
git worktree remove ../art-issue-95        # manual teardown (--force if the DB dir is dirty)
git branch -D issue-95-<slug>              # delete the branch after the PR merges
```

Keep a long-lived worktree current with `main`:

```powershell
cd 'C:\Users\...\art-issue-95'
git fetch origin
git rebase origin/main
```

### Gotchas

- **One agent per worktree.** Don't point two `claude` sessions at the same folder.
- **Conflicting Python deps** on one branch? Give just that worktree its own
  `.venv`; the rest keep sharing the root one.
- **`worktree remove` refuses to delete** a dirty tree — the private `.artdata/`
  DB counts as dirty. Use `--force` once you've confirmed nothing else is unsaved.
- **Windows PowerShell 5.1**: the bootstrap script tolerates git's informational
  stderr (which 5.1 otherwise treats as fatal). Run it with `pwsh` or
  `powershell -File`.

---

## Direct script reference

`/work` is a thin wrapper; you can call the script directly:

```powershell
./scripts/new-agent-worktree.ps1 -Name <slug> [-Branch <b>] [-BaseRef <ref>]
                                 [-Index <n>] [-Frontend] [-SharedDb] [-Launch]
```

| Parameter | Default | Meaning |
|---|---|---|
| `-Name` | *(required)* | worktree folder becomes `../art-<Name>` |
| `-Branch` | `agent/<Name>` | branch to create |
| `-BaseRef` | `main` | ref to branch from |
| `-Index` | worktree count | port offset: backend `8000+N`, frontend `5173+N` |
| `-Frontend` | off | run `npm install` so the Vite server can start |
| `-SharedDb` | off | use shared Postgres instead of a private SQLite file |
| `-Launch` | off | open a new Windows Terminal tab running `claude` |
