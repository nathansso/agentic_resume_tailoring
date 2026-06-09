# Web Layer — Implementation Guide

## Architecture overview

The web layer is a single-page React app served by FastAPI as static files.

```
web/
  app.py              # FastAPI app factory — registers all routers, mounts SPA
  auth.py             # get_current_user dependency (Supabase JWT or local cookie)
  routers/
    auth_router.py    # /api/auth/ — login, register, logout, me
    profile_router.py # /api/profile/ — profile CRUD, skills, experiences, projects, graph
    jobs_router.py    # /api/jobs/ — job CRUD, analyze, tailor, export (pdf/tex/docx)
    dependencies.py   # check_ai_quota / increment_ai_usage — per-user daily AI rate limit
    chat_router.py    # /api/chat/ — history, send (SSE streaming)
    ingest_router.py  # /api/ingest/ — resume upload, GitHub ingestion
  frontend/
    src/
      App.tsx                  # Router: /login, /register, /* (RequireAuth → MainPage)
      context/AuthContext.tsx  # Auth state, setUser, logout
      pages/
        LoginPage.tsx
        RegisterPage.tsx
        MainPage.tsx           # Top-level layout: header, JobSidebar, main content area
      components/
        WelcomePanel.tsx       # Shown to new users with no jobs — 4 CTAs
        ChatPanel.tsx          # Chat UI with SSE streaming
        DataExplorer.tsx       # Tabs: Skills, Experiences, Projects, Graph, Charts
        ProfilePanel.tsx       # View/edit user profile fields
        IngestPanel.tsx        # Resume upload, GitHub ingestion
        JobSidebar.tsx         # Job list, create/delete
        JobDetailPanel.tsx     # Job description, analyze, tailor, export
      api/                     # Typed fetch wrappers for all endpoints
      types.ts                 # Shared TypeScript interfaces
      theme.ts                 # Color/font constants
```

---

## Auth flow

Session token stored in `access_token` cookie (httponly, samesite=strict, 30-day TTL).

- **Supabase path**: if `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_JWT_SECRET` are set, register/login go through Supabase Auth and the Supabase JWT is stored as the cookie.
- **Local fallback**: if Supabase env vars are absent, `make_session_token()` issues a signed cookie via `itsdangerous`. Passwords are always hashed locally with PBKDF2-HMAC-SHA256.
- `get_current_user` in `web/auth.py` tries Supabase JWT first, then local cookie.

---

## API conventions

- All routes are under `/api/` and require `get_current_user`.
- Use trailing slashes on fetch calls (e.g. `/api/profile/`) — FastAPI redirects slash-less URLs with 307 which can drop cookies through the Vite proxy.
- Chat responses use SSE (`text/event-stream`). The `job_id` segment is `"landing"` when no job is selected.
- Heavy operations (analyze, tailor, export, ingest) run in `asyncio.to_thread` to avoid blocking the event loop.
- AI-gated routes (`POST /api/chat/{id}/send`, `POST /api/jobs/{id}/tailor`) use `Depends(check_ai_quota)` and call `increment_ai_usage()` on success. Onboarding/ingest routes are explicitly excluded.

---

## Rate limiting

`web/routers/dependencies.py` implements per-user daily AI call limits via the `AIUsage` table.

- `check_ai_quota` — FastAPI dependency; raises HTTP 429 when the daily limit is hit.
- `increment_ai_usage(user_id, session)` — call after a successful AI response to record usage.
- `AI_DAILY_LIMIT` env var (default 20) controls the cap. `OWNER_EMAIL` env var is always exempt.
- The `AIUsage` table stores one row per (user_id, date) with a running `call_count`.

---

## Frontend conventions

- Styles are plain `CSSProperties` objects (`const s: Record<string, CSSProperties> = { ... }`), no CSS files or CSS-in-JS libraries.
- `ActiveView` union: `"chat" | "data" | "ingest" | "profile" | "job"`.
- `WelcomePanel` is shown in `MainPage` only when: no jobs exist, no job is selected, loading is complete, AND `welcomeDismissed` is false. Any CTA click sets `welcomeDismissed = true`.
- Color palette and font sizes come from `src/theme.ts` — do not hardcode colors.

---

## Running locally

```bash
# Terminal 1 — backend
uvicorn web.app:app --port 8000 --reload

# Terminal 2 — frontend dev server (proxies /api → localhost:8000)
cd web/frontend && npm run dev -- --port 5173
```

App is at http://localhost:5173.

---

## Deployment

```bash
fly deploy   # from repo root — builds Docker image, pushes to Fly.io
```

- App: `artie-resume-tailoring` on Fly.io, region `iad`
- URL: https://artie-resume-tailoring.fly.dev/
- Config: `fly.toml` in repo root
- Dockerfile: multi-stage — Node 20 builds React (`npm run build` → `web/static/`), Python 3.12 serves it via FastAPI `StaticFiles`
- Volume: `art_data` mounted at `/data` — SQLite database lives at `/data/art.db`
- Health check: `GET /api/health`

Required env vars (set as Fly.io secrets):
- `SESSION_SECRET_KEY` — signs local session cookies
- `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_JWT_SECRET` — Supabase Auth (optional; local fallback used if absent)
- `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` — LLM access
- `AI_DAILY_LIMIT` — per-user daily AI call cap (default 20)
- `OWNER_EMAIL` — email address exempt from rate limiting
