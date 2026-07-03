# ART — Repository Guidelines

## Project overview
ART is a resume-tailoring platform that ingests resume, GitHub, and LinkedIn data into a knowledge graph, then tailors resumes to job descriptions through chat and workflow tools. The **web app** is the primary and only actively maintained implementation.

**Web app (primary):** https://artie-resume-tailoring.fly.dev/  
Deployed on Fly.io. React + TypeScript frontend served as static files by a FastAPI backend. Supabase Postgres in production (via `DATABASE_URL` Fly.io secret); falls back to SQLite locally. Supabase Auth used for JWT session tokens when env vars are present; falls back to local signed cookies.

A `cli.py` command surface mirrors the core ingestion/tailoring pipeline for scripting and tests. The web app is the only user-facing product; a Textual TUI existed previously and was removed — its shared service layer now lives in `services.py`.

**Stack:**
- Frontend: React 18, TypeScript, Vite — lives in `web/frontend/`
- Backend API: FastAPI (Python), routers in `web/routers/`
- Database: SQLModel ORM — Supabase Postgres in production (via `DATABASE_URL` Fly.io secret); falls back to SQLite locally when `DATABASE_URL` is unset
- Auth: Supabase Auth (JWT) with local `itsdangerous` cookie fallback
- AI: LangGraph, LangChain, OpenAI / Anthropic

**Entry points:**
- `uvicorn web.app:app --port 8000` — web server (production uses Fly.io Docker deploy)
- `npm run dev` (in `web/frontend/`) — Vite dev server on port 5173, proxies `/api` to port 8000
- `python cli.py <command>` — CLI surface

**Deploy:** `fly deploy` from repo root — builds Docker image (Node 20 → Python 3.12), pushes to Fly.io.

**Environment:**
```bash
source .venv/Scripts/activate   # bash
.venv\Scripts\Activate.ps1      # PowerShell
```

Do not break the CLI when making web changes.

---

## Always-on rules

- Keep changes scoped to the requested task. Avoid unrelated refactors.
- Preserve backward compatibility for user data and the SQLite schema.
- Every behavior change must ship with at least one test.
- Generated artifacts like `tailored_output.json` and `tailored_resume.md` are outputs, not source files.
- Keep root guidance stable. Folder-specific implementation rules belong in local `CLAUDE.md` files.

---

## Guidance hierarchy

Use the repo guidance in this order:

1. The active PRD in `docs/prd/` defines task scope, sequencing, and acceptance criteria.
2. This root `CLAUDE.md` defines stable repo-wide workflow, testing, and architecture rules.
3. Local `CLAUDE.md` files add folder-specific implementation constraints without replacing the root rules.
4. `.github/` Copilot instruction files are thin planning aids and should not become a second policy system.

Keep these roles separate:
- PRDs are task specs.
- Root `CLAUDE.md` is the repo policy surface.
- Local `CLAUDE.md` files are implementation-local supplements.
- `.github/` instruction files mirror the essentials for VS Code tooling.

---

## Work tracking

- Use the PRDs in `docs/prd/` as forward-looking specs.
- Do not retroactively edit PRD task checklists. Update only the `## Progress` section while work is in flight.
- `CHANGELOG.md` records completed deliveries — both PRD deliveries and self-contained issue-level work (issues/arcs that ship outside a PRD). Add a new top entry when the work merges to `main`.
  - Title the entry by its source: `PRD NN — …` for PRD work, `Issue NN — …` (or `Issues NN & MM — …` for a multi-issue arc) for issue-level work.
  - Include a `**Status:** complete | **Tests:** N pass (M new)` line, a short summary, a `### What shipped` list, and a `### Deviations from spec` section.
  - The entry must reflect shipped (merged) state. When a PR carries the entry, the entry only reaches `main` on merge, so it may describe everything in that PR.
- Keep one logical unit of work per commit.
- For issue resolution, follow the workflow in `ISSUE_WORKFLOW.md`.

**Commit format:** `type(scope): short description`

Examples:
- `feat(prd-03): onboarding screen and active profile flow`
- `test(chat): fast-path routing regression coverage`
- `docs: mark PRD 02.5 complete`

---

## GitHub project board

The **ART Development Plan** project (number 2, owner `nathansso`) must stay in sync with the codebase at all times. Both of the following behaviors are required — do not skip them.

### Slash commands

Use these slash commands to manage the board without manual API calls:

| Command | Usage | Purpose |
|---|---|---|
| `/projects` | `/projects` | Full board view grouped by status |
| `/ready` | `/ready` | Issues ready to work on next |
| `/issue` | `/issue 14` | Full details + board status for one issue |
| `/start` | `/start 14` | Move issue to In Progress |
| `/done` | `/done 14` | Move to Done, auto-unblock dependents |
| `/new-issue` | `/new-issue Fix PDF export` | Create issue, add to board, assess deps |

### Board completeness invariant

**Every issue in the repo must be on the project board.** When starting any session or after creating/discovering issues, verify completeness:

```bash
# Find issues missing from the board
gh issue list --repo nathansso/agentic_resume_tailoring --state all --limit 100 --json number,url \
  | python -c "
import json, sys, subprocess
repo_issues = {i['number']: i['url'] for i in json.loads(sys.stdin.read())}
board = json.loads(subprocess.check_output(['gh','project','item-list','2','--owner','nathansso','--format','json']))
on_board = {i['content']['number'] for i in board['items'] if i.get('content')}
for num, url in sorted(repo_issues.items()):
    if num not in on_board:
        print(f'Missing #{num}: {url}')
"
```

For any missing issue, add it and set status (closed → Done; open unblocked high-priority → Ready; otherwise → Backlog).

### When a new issue is created

1. Add it to the project immediately:
   ```bash
   gh project item-add 2 --owner nathansso --url <issue-url>
   ```
2. Assess dependencies against all open issues. Add a `## Dependencies` section to the issue body listing any blockers by number (e.g. `Blocked by #14`). If unblocked, note `None`.
3. Set the initial status — `Ready` if unblocked and high priority, `Backlog` otherwise — using the GraphQL mutation below.

### After implementing an issue

1. Move the item to Done (option `98236657`) via the GraphQL API — no confirmation needed.
2. Check whether any Backlog issues that depended on the just-completed issue are now unblocked, and move them to `Ready` (option `e18bf179`).

### Project board API reference

```bash
# Look up an item's ITEM_ID
gh project item-list 2 --owner nathansso --format json \
  | python -c "import json,sys; items=json.load(sys.stdin)['items']; [print(f\"{i['content']['number']}: {i['id']}\") for i in items if i.get('content')]"

# Update a status field (use GraphQL variables — literal IDs in the query body cause "Expected type 'ID!'" errors)
gh api graphql -f query='mutation($proj:ID!,$item:ID!,$field:ID!,$opt:String!){
  updateProjectV2ItemFieldValue(input:{projectId:$proj,itemId:$item,fieldId:$field,value:{singleSelectOptionId:$opt}}){projectV2Item{id}}
}' -f proj="PVT_kwHOCpdM7s4BXnLT" -f item="<ITEM_ID>" -f field="PVTSSF_lAHOCpdM7s4BXnLTzhSy32k" -f opt="<OPTION_ID>"
```

**Status option IDs:**

| Status | Option ID |
|---|---|
| Backlog | `f75ad846` |
| Ready | `e18bf179` |
| In progress | `47fc9ee4` |
| Done | `98236657` |

**Status definitions:**

| Status | Meaning |
|---|---|
| Backlog | Blocked by open issues or not yet prioritized |
| Ready | Unblocked and next in line |
| In progress | Actively being implemented |
| Done | Shipped and verified |

---

## Architecture invariants

- Use `database/user_utils.py::get_active_profile()` for active user lookups. `get_or_create_default_user()` exists only as backward-compat support.
- Keep schema changes backward-compatible with defaults so existing local DBs still load.
- Keep chat/router changes aligned with actual product capabilities. Do not teach prompts a capability that code does not expose.
- Prefer adding focused local guidance instead of expanding this file with volatile implementation details.

For folder-specific rules:
- See `web/CLAUDE.md` for FastAPI routers, React components, auth flow, and deploy.
- See `agents/CLAUDE.md` for chat routing, prompt, and tool-calling work.

When adding more guidance, prefer updating one of those local files over expanding this file with transient implementation detail.

---

## Testing

Run this before and after behavior changes:

```bash
python run_tests.py          # full suite (integration tests excluded)
python run_tests.py -k chat  # filter by keyword
python run_tests.py --integration  # include slow/network tests
```

Or directly:

```bash
python -m pytest tests/ -q
```

Test layout:
- All tests live under `tests/` — one file per concern.
- `tests/conftest.py` — shared `isolated_engine` fixture and `_seed_user_and_skill` helper.
- `tests/test_chat.py` — chat routing, fast-path, trace tests.
- `tests/test_services.py` — services, ingestion diff, profile.
- `tests/test_db.py` — DB and user-utils tests.
- `tests/test_llm.py` — LLM factory tests.
- `tests/test_eval.py` — PRD 06 eval harness tests.
- `tests/test_prd04.py` — PRD 04 job lifecycle and tailoring tests.
- `tests/test_integration.py` — full pipeline (marked `@pytest.mark.integration`).

Testing conventions:
- Use the `isolated_engine` fixture for DB-related tests.
- Mark slow or network-dependent tests with `@pytest.mark.integration` and `@pytest.mark.slow`.
- Import `_seed_user_and_skill` from `conftest` when tests need a seeded user+skill.

Definition of done:
1. The feature works.
2. At least one test covers the new behavior.
3. `python run_tests.py` passes in full.
4. Move the issue to Done on the project board and unblock any newly-unblocked issues.
