# ART — Agentic Resume Tailoring

**Tailor your resume to any job description through an AI chat workflow.**

ART ingests your resume, GitHub, and LinkedIn data into a skills knowledge
graph, then uses a pipeline of LLM agents to plan, generate, and score a
focused, ATS-friendly one-page resume for a specific job — with a chat
interface for iterative, reviewable revision.

> **Direction: the harness pivot ([#207](https://github.com/nathansso/agentic_resume_tailoring/issues/207)).**
> ART is becoming a local, model-free plugin for the coding agent you already pay for
> (Claude Code, Codex). Your agent's model does the writing. ART supplies the knowledge
> graph, an approved-bullet library, your preferences, the gates that enforce them, and a
> local LaTeX editor (`art ui`). The target design is [`docs/harness.md`](docs/harness.md).
> Everything below describes the current, merged system.

🔗 **Hosted demo (frozen):** https://web-production-2ead7.up.railway.app/
📦 **Setup:** [`INSTALL.md`](INSTALL.md) — Docker, local Python, and cloud deploy
🔑 **Optional Jev key:** the harness can ask TypeSafe's Jev a few narrow classification questions. Every decision point works without a key; to set one, see what is sent and turn it off (`ART_JEV_MODE=off`), read [INSTALL.md § Jev (optional)](INSTALL.md#jev-optional)

---

## How it works

```mermaid
flowchart LR
    subgraph Ingestion
        R[Resume<br/>md / docx / pdf] --> KG
        G[GitHub repos] --> KG
        L[LinkedIn PDF] --> KG
        KG[(Knowledge graph<br/>skills ↔ evidence)]
    end
    subgraph Tailoring pipeline
        JD[Job description] --> AN[Analyze<br/>extract skills]
        AN --> MA[Match<br/>profile vs. JD]
        KG --> MA
        MA --> PL[Plan<br/>per-item actions]
        PL --> GE[Generate<br/>constrained rewrite]
        GE --> EV[Evaluate<br/>ATS score + checks]
        EV -->|retry with feedback| GE
        EV --> OUT[One-page resume<br/>LaTeX → PDF / DOCX]
    end
    OUT --> CHAT[Chat revision loop<br/>propose → approve → apply]
    CHAT -->|delta plan| PL
    EV --> DL[(Decision log<br/>context · actions · reward)]
    CHAT -->|1–5 score| DL
```

Every tailoring run is **planned as typed, per-item edit actions**, executed
under deterministic guards, **scored algorithmically**, and **logged** — so the
system can explain each change and, in future, learn which tailoring choices
work over time.

---

## Documentation

| Document | What it covers |
|---|---|
| [`docs/harness.md`](docs/harness.md) | **Target architecture.** ART as a model-free plugin for the user's coding agent: decisions, Jev decision points, separate metrics, plan programs, bullet library, local storage, `art ui`, and the H0–H5 roadmap |
| [`docs/architecture.md`](docs/architecture.md) | The current merged system: memory units, reasoning units, KG retrieval, the preference/persona tier, exploration, the determinism invariants, and the harness path (Jev decisions, the executor and its gates, the bullet library, the memory gate) |
| [`docs/benchmark.md`](docs/benchmark.md) | What the benchmark measures, its three execution modes, a worked run, and what a number may claim |
| [`eval/README.md`](eval/README.md) | Operational reference for every eval harness — commands, dataset schemas, replay contract |
| [`INSTALL.md`](INSTALL.md) | The plugin and `art-mcp`, the optional Jev key, backup and migration, Docker, local development, configuration, and cloud deploy |
| [`docs/parallel-agents.md`](docs/parallel-agents.md) | Worktree-isolated agent sessions |
| [`CHANGELOG.md`](CHANGELOG.md) | Every completed delivery, with its deviations from spec |

### Back up your data, or bring a web-app profile local

Local data has no server copy. `art export` writes one zip (your knowledge graph,
preferences, jobs, every tailored version, your bullet library and baselines, and the
`applications/` folder), and `art import` loads it back. Credentials are never in it.

```bash
art export --out ~/backups/art.zip                     # back up
art import ~/backups/art.zip --user-id <your-user-id>  # restore into an empty store
art import ~/backups/art.zip --merge                   # add what is missing to a store with data
```

To move a profile from the hosted web app into the local store once, read it straight
from the web app's database. The database URL is yours and is read-only here; pass it in
the environment rather than on the command line:

```bash
export ART_IMPORT_SOURCE_URL='postgresql://<user>:<password>@<host>:5432/postgres'
art import --from-supabase --user-id <your-supabase-profile-id> --dry-run   # look first
art import --from-supabase --user-id <your-supabase-profile-id> --set-active
```

Import binds the `--user-id` you give it, refuses a local fallback profile, and refuses a
profile that already has data unless you pass `--merge` (or `--replace --confirm-replace`).
Details: [`docs/harness.md`](docs/harness.md) § 12.

---

## Architecture

React/TypeScript SPA served as static files by a FastAPI backend — one process
runs the whole product in production. `services.py` is the shared business
layer used by the web routers, the chat agent, and the CLI.

```
web/frontend (React/TS)  ──►  FastAPI routers (web/routers/)
                                     │
                                     ▼
                         services.py  ── shared business logic
                          │        │
                          ▼        ▼
              ingestion/        agents/ + graph/  ── LLM tailoring pipeline
              (resume,          knowledge_graph/  ── skills graph builder
               github,                 │
               linkedin)               ▼
                          database/ (SQLModel: Supabase Postgres / SQLite)
```

**Memory units** — what the system carries between turns and between jobs:

- the **skills knowledge graph**, where every skill records where it was
  demonstrated (`UserSkill.evidence_source/detail`), so nothing is ever claimed
  without support;
- **JobCards**, one deterministic card per finished job, relevance-ranked and
  injected into the planner under a token budget so prompt cost stays flat as
  jobs accumulate;
- the **decision log** on `UserJobResult.tailoring_decisions` — an append-only
  `(context, actions, propensity, reward)` tuple per run;
- the **preference store and persona index**, a lossless one-level abstraction
  over standing preferences extracted from chat;
- **layout overrides**, the arrangement a user chose, which the pipeline reads
  and never writes;
- **chat history** and its rolling compression.

**Reasoning units** — how a job description becomes a resume:

1. **Analyze / match** — the JD is extracted once into a persisted, inspectable
   `JDProfile` (requirements with type, criticality, terms, and source order),
   then matched against the graph to fix the pre-tailor baseline score.
2. **Plan** — a planner LLM emits one typed action per resume item:
   `keep | revise | replace | delete`, with a named strategy
   (`keyword_weave | quantify | tighten | reframe`), the keywords to weave, and a
   rationale. The plan is then validated deterministically — unknown items
   dropped, replace is pool-only, a section can never be emptied — and degrades
   to a safe default plan if the model fails. Re-tailors plan a **delta against
   the current tailored resume**, never a regeneration.
3. **Generate** — the generator executes the plan under strict rules, then
   deterministic guards enforce what prompts cannot guarantee: deleted items stay
   out, `keep` items keep their bullets verbatim, budgets and ordering hold.
4. **Evaluate** — the same algorithmic ATS engine that scored the baseline scores
   each attempt, plus keyword *placement* precision, faithfulness drift, and
   stuffing checks. The loop retries with targeted feedback and ships the
   **best-of-N** attempt, never the last one.
5. **Format** — tailored JSON renders to LaTeX (Jake's Resume layout) compiled by
   tectonic to a one-page PDF, with DOCX mirroring the same layout.

Two things run **unconditionally** inside step 2's input loading, because a tier
consulted only when something upstream thinks it relevant is a tier that
silently stops binding: a **knowledge-graph evidence step** that promotes and
annotates items the JD's own prose never names, and a **persona step** that
arbitrates the candidate's standing preferences against the job's requirements.
Truthfulness wins that arbitration outright — a preference to emphasize
something the graph does not evidence is refused, not honoured.

**Chat** is router-first: deterministic fast paths handle command-like input
with no model call, and everything else goes to a routing LLM restricted to a
`TOOL_CALL / CLARIFY / RESPONSE` envelope. Within a job chat, tailoring is a
strict action set — `PROPOSE_PLAN`, `APPLY_PLAN`, `SHOW_DIFF`, `EXPLAIN`,
`REVERT`, `SAVE_ARTIFACT` — so a revision is a reviewable delta you approve, not
a regeneration you receive.

**Auth and storage.** Supabase Auth (JWT via JWKS) in production with a
signed-cookie fallback locally; per-request user binding keeps multi-user data
isolated. SQLModel on **Supabase Postgres**, with `docker compose up` bringing up
a local pgvector Postgres so development runs the engine production runs. SQLite
is a *fallback* when `DATABASE_URL` is unset — it keeps `cli.py` and the
no-Docker path working, but dialect gaps between the two have shipped bugs
before. Schema changes ship as idempotent `ALTER TABLE` migrations.

Full detail, with the LLM-versus-deterministic split stated explicitly, is in
[`docs/architecture.md`](docs/architecture.md).

---

## Evaluation

`eval/tailoring_benchmark.py` replays a versioned corpus of **150 verified
intern/entry postings across five role families**, against **20 stratified
candidate profiles**, through the real HTTP API (register → ingest → analyze →
tailor → export) on an isolated database, computing ATS deltas,
experience-allocation balance, skills organization, and a four-mode redundancy
suite per task. Both sides are stratified and every metric is reported per
stratum — pooling a candidate axis into one number is what hides which ability
moved.

It runs in three modes — `product`, `replay`, `plumbing` — with different
evidentiary weight, and **the mode travels with every number**. A plumbing run
never rewrites a bullet, so its figures describe the harness and the
deterministic post-processing, never tailoring quality. Four narrower harnesses
isolate what the end-to-end delta cannot see: JobCard quality, knowledge-graph
updates, skill selection, and chat memory.

See [`docs/benchmark.md`](docs/benchmark.md) for what the numbers mean and
[`eval/README.md`](eval/README.md) for how to run them.

---

## Tech stack

| Layer     | Technology |
|-----------|------------|
| Frontend  | React 18, TypeScript, Vite, Tailwind CSS |
| Backend   | FastAPI (Python 3.11+) |
| Database  | SQLModel ORM — Supabase Postgres (pgvector), SQLite fallback |
| Auth      | Supabase Auth (JWT) with a local signed-cookie fallback |
| AI        | LangGraph + LangChain, Anthropic / OpenAI |
| PDF       | LaTeX (tectonic) |
| Deploy    | Docker → Railway |
| CI        | GitHub Actions — the suite on both Postgres and SQLite |

## Testing

```bash
python run_tests.py               # full suite (integration tests excluded)
python run_tests.py -k chat       # filter by keyword
python run_tests.py --integration # include slow / network tests
```

That runs the **SQLite leg**. Production is Postgres, so anything touching
storage must also run the Postgres leg:

```bash
docker compose up -d postgres
ART_TEST_DATABASE_URL=postgresql://art:art@localhost:5433/art python run_tests.py
```

Each test gets a throwaway schema that is dropped on teardown.
`ART_TEST_DATABASE_URL` is deliberately **separate** from `DATABASE_URL` — the
latter may point at production Supabase, and the suite creates and drops
schemas. Both legs run in CI (`.github/workflows/tests.yml`) on every push and
PR, with Postgres required.

## Project structure

```
web/            FastAPI backend (routers/) + React frontend (frontend/)
agents/         chat router, job analyzer, JD profile, matcher, planner, tailor,
                preferences, persona, arbitration, JobCards, scorers, formatter
graph/          LangGraph tailoring pipeline (CLI + chat composition)
knowledge_graph/ skills-to-evidence graph builder
ingestion/      resume / GitHub / LinkedIn ingestors
database/       SQLModel models, engine, migrations, vector search, user utilities
eval/           benchmark + regression eval harnesses, datasets, metrics
supabase/       Supabase schema, RLS policies, migrations
scripts/        JD sourcing / verification / scraping, agent-worktree bootstrap
services.py     shared business logic used by web, agents, and CLI
cli.py          command-line entry point
tests/          pytest suite (+ tests/fixtures/ sample data)
docs/           architecture, benchmark, and contributor guides
```

Task specs and roadmaps live in GitHub issues and the **ART Development Plan**
board, not in checked-in documents.

## Roadmap

Work is sequenced in phases on the board. **P0** (research spikes) and **P1**
(preferences & knowledge) are complete: the P1 epic #140 closed with the JD
profile, layout overrides, the preference and persona tiers, JobCards,
chat→graph extraction, and KG evidence in the planner. **P2** shipped the
redundancy suite (#122) and weighted keyword scoring (#125).

From 2026-09-28 the sequence follows the **harness pivot** (epic **#207**,
[`docs/harness.md`](docs/harness.md)), which replaces P3 and P4.

| Phase | Window | Scope | Status |
|---|---|---|---|
| **H0** · Decide & Spike | Sep 28 – Oct 4 | Docs and board (#188); a read-only `art-mcp` spike (#189) | Done |
| **H1** · Core & Jev | Oct 5 – Oct 18 | Model-free checks and import boundary (#190), adapters (#191), host-filled ingestion (#192), Jev decisions engine (#193) with fitted support thresholds (#237), packaging (#194), export/import (#195), timezone-aware datetimes and lock-held installs (#210), a hermetic test suite (#249) | Shipped except #249 |
| **H2** · Executor & Library | Oct 19 – Nov 8 | Tailoring tree (#196), plan programs and the acceptance rule (#197), citation, numeric and reworded-pin gates (#198, #123, #232), semantic coverage (#126), bullet library and baselines (#199, #229), line budget (#200), project context (#230), skill retrieval (#233); open: fitted tolerances (#127) and the policy artifact (#241), minimal-delta revisions (#117), required/preferred coverage (#151), keyword insertion ranking (#163), the skills floor past the cap (#185) | Mostly shipped |
| **H3** · Plugin & Memory | Nov 9 – Nov 22 | Claude Code plugin (#201), Jev memory gate and pins (#202, #244), Codex (#203) | Done |
| **H3b** · Editor | Nov 23 – Dec 6 | `art ui` local editor (#204); open: chat panel (#205) and the retargeted UI issues #87, #82, #84, #136, #147 | Editor shipped |
| **H4** · Host Evaluation | Dec 7 – Dec 27 | Host runner with arms A/B/B′/C (#206) on the benchmark (#172, #173, #178, #181) | Not started |
| **H5** · Offline Policy | From Dec 28 | Ranker and tolerances trained offline (#174, #152, #157, #119, #51; arc epic #114). Runs only if H4 says go | Not started |

H1 through H3 largely shipped ahead of the calendar; the windows are still the plan.

The ATS composite is monotone in text, so it is reported only. Tailoring metrics
stay separate and are never pooled into one objective.

---

## License

No license file is currently provided; all rights reserved by the author.
