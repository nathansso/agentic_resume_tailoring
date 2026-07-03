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

Your data (database, exports, uploads) is stored in a Docker named volume (`art_data`) and persists across runs.

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

---

## Option C — Cloud deploy

### Fly.io

> **Pre-requisites:** [flyctl installed](https://fly.io/docs/hands-on/install-flyctl/),
> a Fly.io account, and a Supabase project with auth enabled.

```bash
# 1. Clone the repo (fly.toml is already included)
git clone https://github.com/nathansso/agentic_resume_tailoring.git
cd agentic_resume_tailoring

# 2. Create the app (keeps the committed fly.toml, skips overwriting it)
fly launch --no-deploy

# 3. Create the persistent volume
fly volumes create art_data --size 1 --region iad

# 4. Set required secrets
fly secrets set \
  ANTHROPIC_API_KEY=sk-ant-... \
  DATABASE_URL=postgresql://postgres.[PROJECT-REF]:[PASSWORD]@aws-0-[REGION].pooler.supabase.com:6543/postgres \
  SUPABASE_URL=https://[PROJECT-REF].supabase.co \
  SUPABASE_ANON_KEY=eyJ... \
  SUPABASE_SERVICE_ROLE_KEY=eyJ...

# 5. Deploy
fly deploy

# 6. Apply Supabase schema + RLS policies (first deploy only)
fly ssh console -C "python cli.py supabase-setup"
```

After deploy, visit `https://<your-app>.fly.dev` — users see the login screen with no local install needed.

**Notes:**
- Use the Supabase **pooler** connection string (port 6543) for production, not the direct connection (port 5432).
- `SUPABASE_SERVICE_ROLE_KEY` is backend-only — never expose it to clients.
- Supabase RLS enforces per-user data isolation automatically for every request.
- To verify RLS isolation: log in as two different users and confirm each can only see their own jobs and resume data.

### Cloud deploy — Render

1. Create a new **Web Service** pointing at this repo.
2. Set **Environment** → **Docker** (Render detects the Dockerfile automatically).
3. Add environment variable `ANTHROPIC_API_KEY` (or `OPENAI_API_KEY`).
4. Set the port to `8000` in Render's service settings.
5. Click **Deploy**.
