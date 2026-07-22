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
      App.tsx                  # Router: / (landing or app), /login, /register, /* (RequireAuth → MainPage)
      context/AuthContext.tsx  # Auth state, setUser, logout
      index.css                # Tailwind directives + the palette custom properties
      pages/
        LandingPage.tsx        # Public marketing page at `/` for signed-out visitors
        LoginPage.tsx
        RegisterPage.tsx
        MainPage.tsx           # Top-level layout: header, JobSidebar, main content area
      components/
        AuthLayout.tsx         # Shared card/brand chrome for the signed-out pages
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

**Login is by email + password.** Session token stored in `access_token` cookie (httponly, samesite=strict, 30-day TTL).

`database/auth.py::supabase_configured()` is the single gate that picks the mode (env vars set AND the `supabase` package importable). It is **fail-closed**: whenever Supabase is configured, the local password/cookie path is never used.

- **Supabase path (production):** register/login go through Supabase Auth; the Supabase JWT is the cookie. **No local password hash is stored** (`password_hash` stays `NULL` — the column is kept only for schema back-compat). `get_current_user` accepts *only* Supabase JWTs. On login/reset the local profile's `supabase_uid` is backfilled so pre-migration accounts resolve from the JWT `sub`.
- **Local fallback (offline dev/tests only):** when Supabase is absent, `make_session_token()` issues an `itsdangerous` signed cookie and passwords are hashed locally with PBKDF2-HMAC-SHA256. This path can never run in production.

### Password recovery (issue #61)
- `GET /api/auth/capabilities` → `{password_reset_enabled, auth_mode}`; the login page shows "Forgot password?" only when enabled.
- `POST /api/auth/forgot-password {email}` → Supabase `reset_password_for_email` with `redirect_to` = `<APP_BASE_URL or request origin>/reset-password`. **Always returns the same generic 200** (no account enumeration). Returns 503 in the local fallback (no email transport).
- Recovery link lands on the frontend `/reset-password` page, which reads the Supabase recovery tokens from the URL fragment and strips them from history.
- `POST /api/auth/reset-password {access_token, refresh_token, new_password}` → re-establishes the recovery session and calls Supabase `update_user`. Enforces min 8-char password (422) before touching Supabase; invalid/expired token → 400. Supabase guarantees single-use/expiry of the recovery token.

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
- The `AIUsage` table stores one row per (user_id, date, kind) with a running `call_count`.
- **LinkedIn (Bright Data) scrapes are billed per call**, so they get a separate, much tighter cap via `check_linkedin_quota` / `increment_linkedin_usage` (kind `"linkedin"`), controlled by `LINKEDIN_DAILY_LIMIT` (default 2). `OWNER_EMAIL` is exempt; resets at midnight UTC. There are three trigger paths, all gated by the same daily cap:
  1. **Data ingestion** — `PATCH /api/profile/` auto-triggers a background scrape when the LinkedIn URL is newly set/changed (onboarding). The background task (`_linkedin_ingest_task`) self-guards the quota and increments.
  2. **Sign-in** — `POST /api/auth/login` calls `_maybe_refresh_linkedin_on_login`, which schedules a background refresh **at most once per day** (skipped if `linkedin_ingested_at` is today) and only if quota remains. Deliberately hooked on `login`, not `/me`, so it does not fire on every cookie-authed page load.
  3. **Discretionary** — the manual `POST /api/ingest/linkedin` endpoint (`check_linkedin_quota` dependency + up-front increment so retries still count).

---

## Frontend conventions

- **Styling is Tailwind CSS** (issue #134). Write utility classes in `className`; compose conditionals with `cn()` from `src/lib/utils.ts`. The old `const s: Record<string, CSSProperties>` pattern is retired — do not add new style objects.
- The palette lives in `src/index.css` as HSL custom properties (`--background`, `--primary`, `--accent`, `--muted-foreground`, …) and is surfaced to Tailwind through `tailwind.config.js`. Use the semantic utilities (`bg-card`, `text-muted-foreground`, `border-border`) — **never hardcode a hex colour**. There is no `theme.ts`; it was deleted.
- **Light is the default and the designed-for case** — a Supabase-style theme: white ground, near-black ink, `#E6E8EB` hairlines, mint `#3ECF8E` brand green on CTAs with a deep `#006239` label. Dark is a maintained counterpart under the `.dark` class on `<html>`, not a naive inversion.
- **Contrast rule:** `bg-primary` (mint) is for *fills* — buttons, the logo mark — always paired with `text-primary-foreground`. Mint fails contrast as small text on white, so green **text** uses `text-accent` (deep green in light, light green in dark). `text-warning` is separate again, for mid-range scores and incomplete-record badges, so semantic state never borrows the brand hue.
- Theme state: `lib/theme.ts` (pure resolution + DOM apply, unit-tested), `context/ThemeContext.tsx` (provider), `components/ThemeToggle.tsx` (the button, present in the landing nav and app header). An inline script in `index.html` sets the class before first paint to avoid a flash — **if you change the resolution rule, change it in both places.**
- Fonts: `font-sans` is **Switzer**, self-hosted from `public/fonts/` via `@font-face` in `index.css` (Fontshare, free for commercial use; chosen as the closest free stand-in for the commercial Suisse Intl). Only the monospace face still comes from Google Fonts. `font-mono` (JetBrains Mono) is reserved for genuinely code-like surfaces — the LaTeX source editor and compile errors.
- **Inline `style` is still correct for runtime-computed geometry** and nothing else: pane widths and split fractions (`JobWorkspace`, `ResumeSplit`), chat padding/gap from `paneResize`, and the PDF drag bands in `PdfDragOverlay`, whose positions come from PDF text metrics. Keep a comment saying why when you do this.
- `AuthLayout` (`src/components/AuthLayout.tsx`) owns the shared card/brand chrome and the `inputClass` / `buttonClass` / `linkClass` constants for all four signed-out pages.
- **Known gap:** `DataExplorer.tsx` still uses the old style-object pattern. Its tokens are rebound to the new palette via a local constant block, so it themes correctly and imports nothing, but converting its JSX to utilities is outstanding work on #134.
- `ActiveView` union: `"chat" | "data" | "ingest" | "profile" | "job"`.
- `WelcomePanel` is shown in `MainPage` only when: no jobs exist, no job is selected, loading is complete, AND `welcomeDismissed` is false. Any CTA click sets `welcomeDismissed = true`.

## Routing

`/` is public: signed-out visitors get `LandingPage` (marketing, issue #83), signed-in users get the app shell. `MainPage` itself does not use the router — it switches on `ActiveView` state — so the `/*` catch-all simply renders the shell for any other path.

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
- `LINKEDIN_DAILY_LIMIT` — per-user daily LinkedIn (Bright Data) scrape cap (default 2)
- `OWNER_EMAIL` — email address exempt from rate limiting
- `BRIGHTDATA_API_KEY` — platform-wide Bright Data key for LinkedIn ingestion (optional; LinkedIn auto-import is disabled and the PDF-upload fallback is used when unset)
- `BRIGHTDATA_LINKEDIN_DATASET_ID` — Bright Data People Profiles dataset id (defaults to `gd_l1viktl72bvl7bjuj0`)
