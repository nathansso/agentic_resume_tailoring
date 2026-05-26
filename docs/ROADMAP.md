# ART — Local Demo Roadmap

**Goal:** Anyone can `git clone → docker compose up` and have a fully working ART instance in under 5 minutes, no Python knowledge required.

---

## Current state

| Area | Status |
|---|---|
| Docker / compose files | ❌ None exist |
| `requirements.txt` | ⚠️ Unpinned versions — non-reproducible builds |
| Data path (`~/.art/`) | ✅ Configurable via `config.py` — needs volume mapping |
| API key config | ✅ `.env.example` documented — needs in-TUI entry for demo |
| In-TUI API key entry | ❌ Users must manually edit `.env` |
| LinkedIn ingestion (Playwright) | ⚠️ Heavyweight browser dep — exclude from base image |
| Sentence-transformers | ⚠️ Downloads ~100MB model at runtime — pre-cache in image |
| LLM providers supported | ✅ Anthropic + OpenAI |
| First-run experience | ⚠️ No demo data; users need their own resume |

---

## Phase 0 — Dependency & portability foundation

*Prerequisite work. Small PRs, unblocks everything downstream.*

### P0.1 — Pin dependency versions *(new issue)*
- Run `pip freeze`, produce `requirements-lock.txt` alongside `requirements.txt`
- Audit for Windows-only packages (e.g. `pywin32`) that will fail on Linux containers
- Goal: reproducible Docker builds with no dependency drift

### P0.2 — Split heavyweight optional deps *(new issue)*
- Separate `playwright` (LinkedIn scraping) and `sentence-transformers` (~100MB) into `requirements-full.txt`
- Base Docker image uses a `requirements-core.txt` → fast build, small image
- Full image with browser support available via a separate tag
- Defers the hardest-to-containerize dep and unblocks the 90% use case

### P0.3 — Cross-platform path audit *(sub-task of #3)*
- Verify `~/.art/` resolves correctly on Linux (already uses `Path.home()`)
- Confirm no Windows-isms (`launch.bat`, `launch.ps1`) leak into Python code paths
- Validate `config.py` `APP_DATA_DIR` works as a Docker volume mount point

---

## Phase 1 — Core Docker image *(#3, expanded scope)*

*After this phase: any dev with Docker Desktop can `docker compose up` and reach the TUI.*

**Deliverables:**
```
Dockerfile              # python:3.12-slim, installs core deps, pre-caches embedding model
docker-compose.yml      # single service, named volume, env_file directive
.dockerignore           # excludes .venv/, .git/, *.db, personal data files
README.md               # Docker-first quick-start section at the top
```

**Key design decisions:**
- Base image: `python:3.12-slim`
- Pre-bake `all-MiniLM-L6-v2` (sentence-transformers) during `docker build` — no surprise 100MB download on first `docker run`
- Named volume: `art-data:/root/.art` — persists SQLite DB and exports across restarts
- Entry point: `python cli.py tui`
- API key injection: `env_file: .env` in compose — user copies `.env.example → .env`, adds key, done

**Quick-start experience:**
```bash
git clone https://github.com/nathansso/agentic_resume_tailoring.git
cd agentic_resume_tailoring
docker compose up     # TUI launches, prompts for API key on first run
```

**Acceptance criteria:**
- `docker build` succeeds without manual steps
- TUI launches and reaches onboarding screen
- SQLite DB persists across `docker compose down && docker compose up`
- Missing API key shows a clean in-TUI prompt, not a Python traceback

---

## Phase 1.5 — In-TUI API key entry *(new issue)*

*Makes the Docker demo self-contained: users enter their key inside the app, no `.env` editing required.*

**Onboarding — new Step 1 (auto-skipped if key already set):**
- Radio-style buttons: **Anthropic** / **OpenAI**
- Password-masked API key input field
- Calls `services.save_llm_config(provider, api_key)` → writes to `.env` and sets `os.environ` immediately (no restart required)
- Step is silently skipped if a key is already present in the environment (for users who pre-configure via `.env` or Docker env vars)

**Profile screen — new LLM section:**
- Shows current provider + masked key (`••••••••` when set)
- Update button to switch provider or rotate key
- Same `save_llm_config()` call, same masking pattern as the existing GitHub token section

**No restart required:** `llm.py`'s `get_llm()` reads from `os.environ` at call time so the key takes effect on the next LLM call.

---

## Phase 2 — Demo seed data *(new issue)*

*Lets people try the full tailoring flow without uploading their own resume.*

- `demo/` directory: `sample_resume.md` + `sample_job.txt` (fictional content, safe to ship)
- Onboarding offers "Load sample data" on first run alongside the normal "Upload my resume" path
- README quick-start uses demo data for the end-to-end example
- Goal: someone pulling the image for the first time can see a tailored resume in under 2 minutes

---

## Phase 3 — First-run UX polish *(new issue)*

- Progress indicator when the embedding model initialises (pre-cached, but init still takes ~1s)
- Friendly startup error screen if no key is configured — not just a status-bar message
- Docker-aware first-run detection (empty volume = first launch; prompt to load demo data)

---

## Phase 4 — Distribution *(new issue + update #8)*

### P4.1 — Multi-arch image + GHCR publish *(new issue)*
- GitHub Action: on merge to `main`, build `linux/amd64` + `linux/arm64` (Apple Silicon), push to `ghcr.io/nathansso/art:latest`
- After this, no clone is required:
  ```bash
  docker run -it --env-file .env -v art-data:/root/.art ghcr.io/nathansso/art:latest
  ```

### P4.2 — Revisit #8 (Seamless TUI launch)
- Replace the native launcher goal with a Docker wrapper script (`art.bat` / `art.sh`) that calls `docker compose up`
- Same one-click experience, zero Python install required on the host

---

## Issue map

| Phase | Issue | Board status |
|---|---|---|
| P0.1 | New: pin dependency versions + requirements-lock.txt | Ready |
| P0.2 | New: split heavyweight optional deps | Ready |
| P0.3 | Sub-task of **#3** | Ready |
| P1 | **#3** Dockerize (update scope) | Ready |
| P1.5 | New: in-TUI API key entry | Ready |
| P2 | New: sample demo seed data | Backlog |
| P3 | New: first-run UX polish | Backlog |
| P4.1 | New: multi-arch + GHCR publish | Backlog |
| P4.2 | Update **#8** scope to Docker wrapper | Backlog |

---

## Out of scope for local demo

| Item | Why |
|---|---|
| GitHub OAuth (#4) | PAT works fine for demo |
| LinkedIn Playwright scraping | Too heavy for base image; LinkedIn PDF import still works |
| SaaS / multi-tenant (#14) | Different product track |
| Cloud DB migration (#7) | Demo is local-only by design |
