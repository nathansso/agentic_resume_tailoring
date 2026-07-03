# ART — Agentic Resume Tailoring

**Tailor your resume to any job description through an AI chat workflow.**

ART ingests your resume, GitHub, and LinkedIn data into a skills knowledge
graph, then uses LLM agents to tailor a focused, ATS-friendly resume for a
specific job — with a chat interface for iterative revision.

🔗 **Live demo:** https://artie-resume-tailoring.fly.dev/

---

## What it does

- **Ingest once, reuse everywhere.** Parse a resume (`.md` / `.docx` / `.pdf`),
  scan GitHub repos, and import a LinkedIn profile into a structured profile of
  skills, experiences, and projects.
- **Knowledge graph.** Skills are linked to the experiences and projects that
  evidence them, so tailoring can cite real support rather than keyword-stuff.
- **Chat-driven tailoring.** Paste a job description and ask ART to tailor your
  resume. A LangGraph pipeline scores skills against the job, selects the
  strongest evidence, and drafts a one-page resume.
- **Best-of-N selection.** Tailoring generates multiple candidates and keeps the
  highest-scoring one, with an early-exit when a candidate clears a quality bar.
- **ATS scoring + revision.** Each tailored resume gets an ATS-style score, and
  you can iterate by chatting (“emphasize my backend work”, “drop the coursework”).

## Tech stack

| Layer     | Technology |
|-----------|------------|
| Frontend  | React 18, TypeScript, Vite |
| Backend   | FastAPI (Python 3.11+) |
| Database  | SQLModel ORM — SQLite locally, Supabase Postgres in production |
| Auth      | Supabase Auth (JWT) with a local signed-cookie fallback |
| AI        | LangGraph + LangChain, Anthropic / OpenAI |
| Deploy    | Docker → Fly.io |

## Architecture

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
                          database/ (SQLModel: SQLite / Supabase Postgres)
```

The FastAPI backend serves the compiled React app as static files, so a single
process runs the whole product in production.

---

## Quickstart

### Option A — Docker (no Python setup)

```bash
git clone https://github.com/nathansso/agentic_resume_tailoring.git
cd agentic_resume_tailoring
cp .env.example .env          # then set ANTHROPIC_API_KEY (or OPENAI_API_KEY)
docker compose up --build
```

Open http://localhost:8000.

### Option B — Local development

Run the FastAPI backend and the Vite dev server together for hot-reloading:

```bash
git clone https://github.com/nathansso/agentic_resume_tailoring.git
cd agentic_resume_tailoring

# Backend
python -m venv .venv
source .venv/Scripts/activate      # bash / Git Bash on Windows
# .venv\Scripts\Activate.ps1        # PowerShell
pip install -r requirements-core.txt
cp .env.example .env               # set your API key + SESSION_SECRET_KEY
DEV_MODE=1 uvicorn web.app:app --port 8000 --reload

# Frontend (separate terminal) — proxies /api to :8000
cd web/frontend
npm install
npm run dev                        # http://localhost:5173
```

`requirements-full.txt` adds optional LinkedIn scraping (Playwright) and
semantic skill matching (sentence-transformers).

### CLI

A command-line surface mirrors the core pipeline:

```bash
python cli.py ingest-resume <file>
python cli.py ingest-github [username]
python cli.py tailor <job_file_or_text>
python cli.py status
```

---

## Configuration

Copy `.env.example` to `.env` and fill in what you need. The essentials:

| Variable | Purpose |
|----------|---------|
| `LLM_PROVIDER` | `anthropic` (default) or `openai` |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | LLM access |
| `SESSION_SECRET_KEY` | signs local session cookies (`python -c "import secrets; print(secrets.token_hex(32))"`) |
| `DATABASE_URL` | unset → local SQLite; set → Supabase Postgres |
| `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET` | GitHub OAuth for repo ingestion (optional) |

Supabase Auth variables are only needed for cloud multi-user deployment — see
`.env.example` for the full list.

## Testing

```bash
python run_tests.py               # full suite (integration tests excluded)
python run_tests.py -k chat       # filter by keyword
python run_tests.py --integration # include slow / network tests
```

## Project structure

```
web/            FastAPI backend (routers/) + React frontend (frontend/)
agents/         LLM chat routing, parsing, tailoring agents
graph/          LangGraph tailoring pipeline
knowledge_graph/ skills-to-evidence graph builder
ingestion/      resume / GitHub / LinkedIn ingestors
database/       SQLModel models, engine, user utilities
services.py     shared business logic used by web, agents, and CLI
cli.py          command-line entry point
tests/          pytest suite (+ tests/fixtures/ sample data)
docs/           roadmap and product requirement docs (PRDs)
```

## Deployment

Detailed Docker, Fly.io, and Render instructions live in
[`INSTALL.md`](INSTALL.md). In short — from the repo root:

```bash
fly deploy
```

builds the Docker image (Node 20 → Python 3.12) and pushes to Fly.io.

---

## License

No license file is currently provided; all rights reserved by the author.
