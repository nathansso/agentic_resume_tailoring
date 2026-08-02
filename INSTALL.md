# Installing ART

## Option A — Docker (recommended, no Python setup needed)

**Requirements:** Docker Desktop installed and running.

```bash
# 1. Clone the repo
git clone https://github.com/nathansso/agentic_resume_tailoring.git
cd agentic_resume_tailoring

# 2. Configure your API key
cp .env.example .env
# Edit .env and set ANTHROPIC_API_KEY (or OPENAI_API_KEY if using OpenAI)

# 3. Launch
docker compose up --build
```

Open http://localhost:8000.

`docker compose up` brings up **Postgres with pgvector** alongside the app and points
`DATABASE_URL` at it, so local development runs on the same engine as production
(Supabase Postgres). Database contents live in the `art_pgdata` named volume; exports and
uploads live in `art_data`. Both persist across runs.

To bring up only the database — the usual case when you are running the app from a venv
(Option B) or running the test suite:

```bash
docker compose up -d postgres
```

### Optional: LinkedIn scraping and semantic skill matching

The default Docker image uses `requirements-core.txt` (no Playwright browser, no sentence-transformers). To enable those features, build with the full requirements manually.

---

## Option B — Local Python (development)

**Requirements:** Python 3.11+

```bash
# 1. Clone and set up the venv
git clone https://github.com/nathansso/agentic_resume_tailoring.git
cd agentic_resume_tailoring
python -m venv .venv

# 2. Activate
source .venv/Scripts/activate   # bash / Git Bash on Windows
.venv\Scripts\Activate.ps1      # PowerShell

# 3. Install dependencies
pip install -r requirements-core.txt   # lightweight
# or
pip install -r requirements-full.txt   # includes Playwright + sentence-transformers

# 4. Configure
cp .env.example .env
# Edit .env and set your API key + SESSION_SECRET_KEY

# 5. Launch the web server
DEV_MODE=1 uvicorn web.app:app --port 8000 --reload
```

Open http://localhost:8000. For frontend hot-reloading, run the Vite dev server
in a second terminal (`cd web/frontend && npm install && npm run dev`) — it
proxies `/api` to port 8000. See the README for the full dev workflow.

The CLI mirrors the core pipeline without the web UI:

```bash
python cli.py ingest-resume <file>
python cli.py ingest-github [username]
python cli.py tailor <job_file_or_text>
python cli.py status
```

### Database: Postgres or SQLite

With `DATABASE_URL` unset the app falls back to SQLite at `~/.art/art.db`, which keeps this
option and the CLI working with no Docker at all. Production is Postgres, so prefer running
against Postgres when you can:

```bash
docker compose up -d postgres
export DATABASE_URL=postgresql://art:art@localhost:5433/art
```

### Running the test suite

The suite runs on SQLite by default and on Postgres when `ART_TEST_DATABASE_URL` is set:

```bash
python run_tests.py                                              # SQLite leg

docker compose up -d postgres
export ART_TEST_DATABASE_URL=postgresql://art:art@localhost:5433/art
python run_tests.py                                              # Postgres leg
```

Both legs run in CI, with Postgres as the required job. **`ART_TEST_DATABASE_URL` is
deliberately separate from `DATABASE_URL`**: your `DATABASE_URL` may point at production
Supabase, and the suite creates and drops schemas, so the two must never be the same value.
Each test gets a throwaway schema that is dropped on teardown.

---

## Option C — Cloud deploy

### Railway (current target)

> **Pre-requisites:** [Railway CLI installed](https://docs.railway.com/guides/cli),
> a Railway account, and a Supabase project with auth enabled.

Railway builds the repo-root `Dockerfile` (Node 24 → Python 3.12) using the
committed `railway.json`, which also wires the `/api/health` healthcheck and the
restart policy.

```bash
# 1. Clone and link the project
git clone https://github.com/nathansso/agentic_resume_tailoring.git
cd agentic_resume_tailoring
railway link

# 2. Set variables (dashboard → Variables, or via CLI)
railway variables \
  --set ANTHROPIC_API_KEY=sk-ant-... \
  --set SESSION_SECRET_KEY=$(python -c "import secrets; print(secrets.token_hex(32))") \
  --set DATABASE_URL=postgresql://postgres.[PROJECT-REF]:[PASSWORD]@aws-0-[REGION].pooler.supabase.com:6543/postgres \
  --set SUPABASE_URL=https://[PROJECT-REF].supabase.co \
  --set SUPABASE_ANON_KEY=eyJ... \
  --set SUPABASE_SERVICE_ROLE_KEY=eyJ...

# 3. Deploy
railway up
```

**Notes:**
- Attach a **volume mounted at `/data`** if you want the SQLite fallback or
  generated artifacts to survive redeploys. The Dockerfile deliberately declares
  no `VOLUME` instruction — Railway rejects it.
- Apply the Supabase schema and RLS policies once, from any machine with
  `DATABASE_URL` set: `python cli.py supabase-setup`.
- The container currently listens on the fixed port `8000` rather than Railway's
  injected `$PORT`; binding to `$PORT` is tracked in #48.

**Notes:**
- Use the Supabase **pooler** connection string (port 6543) for production, not the direct connection (port 5432).
- `SUPABASE_SERVICE_ROLE_KEY` is backend-only — never expose it to clients.
- Supabase RLS enforces per-user data isolation automatically for every request.
- To verify RLS isolation: log in as two different users and confirm each can only see their own jobs and resume data.
