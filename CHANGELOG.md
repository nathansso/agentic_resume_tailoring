# Changelog

All completed deliveries are recorded here — both PRD deliveries and self-contained issue-level work (issues or arcs that ship outside a PRD). PRDs remain as pure forward-looking specs.

---

## Issues 70 & 71 follow-up — Overleaf-style workspace: live compile, drag-on-PDF reorder, chat-centric insights
**Status:** complete | **Tests:** 516 Python pass (2 new) + 44 vitest (10 → 44)

Redesign of the job workspace shipped in #81, in three phases. The Resume/Overview tabs are gone: the workspace is now three always-visible panes — insights + chat (narrow) | `.tex` source | compiled preview — with the editor behaving like Overleaf (auto-save + auto-compile) and reordering done by dragging directly on the rendered PDF.

### What shipped
- **Three-pane layout, no tabs.** `JobWorkspace` renders a fixed-width chat column beside `ResumeSplit` (editor | preview, with a Split/Source/Preview view toggle). The jobs sidebar collapses to a slim rail that expands on hover (pinned open while the create form is in use). Job insights — skills match, what the last tailoring run changed, and the score breakdown — render as assistant briefing bubbles pinned at the top of the job chat (`lib/insightMessages.ts`, derived live from job state) instead of a separate dashboard card. Export links moved into the workspace header. `/api/jobs/{id}` now surfaces `explainability` from `UserJobResult.matched_skills._explainability`, and underscore-prefixed internal keys are filtered out of `matched_skills` (fixing a latent leak of `_explainability` as a skill chip).
- **Job-scoped chat welcome.** Empty job chats open with state-aware guidance from `lib/welcome.ts` (paste-JD → tailor → `"tailor emphasize Python more" (N runs left)` → budget exhausted) instead of the generic landing text; the welcome is rendered, not stored, so it tracks job-state changes live.
- **Live compile + auto-save (Compile/Save buttons removed).** `PdfPreview` renders via `pdfjs-dist` canvases (no iframe → no browser PDF chrome), flicker-free swap, last good render survives failures. `CompileScheduler` (pure, fake-timer-tested) debounces 1.8s trailing-edge, skips unchanged buffers, coalesces in-flight compiles (protecting the 2-slot semaphore), discards stale results, and pauses on 429 until a manual Recompile. Edits auto-save on the same settle with a Saving…/Saved indicator; Discard-edits remains. `COMPILE_DAILY_LIMIT` default 200 → 500.
- **Drag-and-drop reordering on the compiled PDF (ReorderPanel deleted).** `lib/pdfOverlay.ts` maps page-1 text geometry back onto the tex structure (NFKD/alphanumeric normalization, ordered-cursor heading matching against each block's own `\section{...}`, bullet prefix anchoring absorbing wrapped lines) with graceful degradation down to a disabled overlay. `PdfDragOverlay` is hand-rolled pointer drag over transparent bands — sections via a left-edge handle, bullets within their group, accent drop indicator. Drops apply `moveSectionTo`/`moveBulletTo` (new move-to-index primitives replacing the adjacent-swap ones) to the buffer and flush an immediate recompile; drags are enabled only while the preview matches the live buffer.

### Deviations from spec
- Consecutive drags wait one compile round-trip (drag re-enables when the new render lands); optimistic band re-sorting is a possible follow-up.
- DOCX export still regenerates from tailored JSON and ignores manual `.tex` edits (unchanged from #71, tooltip retained).
- Verified end-to-end with Playwright against a live server + tectonic: welcome, insights, first compile, section drag (order changed in tex and PDF), auto-save (`has_manual_edits` flips), broken-tex error with retained preview, discard-edits restore.

---

## Issues 70 & 71 — Job workspace + manual .tex resume editing
**Status:** complete | **Tests:** 514 pass (28 new Python) + 10 vitest

Two-issue arc shipped as four stacked PRs (#77–#80). #70 rebuilt the Job tab into a per-job workspace (JD at creation, auto analyze+tailor, job-scoped chat driving capped iterative re-tailoring); #71 added a manual `.tex` editor with compile preview, save/export of the edited source, and section/bullet reordering inside that workspace.

### What shipped
- **JD at creation + auto-pipeline (#70).** `POST /api/jobs/` accepts an optional `description`; the sidebar create form gained a JD textarea. Jobs created with a JD route straight to the Job tab and auto-run analyze → tailor with staged progress; JD-less jobs get a paste-JD panel wired to the same chain.
- **Job workspace (#70).** New `JobWorkspace` replaces the JobDetailPanel stepper: job-scoped chat on the left (the top-nav Chat tab is now always the landing chat), resume pane on the right with Resume/Overview tabs, skills chips, score breakdowns, and the retained PDF/LaTeX/DOCX export buttons. The Re-tailor button is removed — the chat drives revision.
- **Capped, instruction-driven re-tailoring (#70).** With an active job, chat `tailor <text>` (plus `re-tailor`/`retailor`) re-runs tailoring with `<text>` as revision instructions, threaded into the generation prompt and persisted on the previously-dead `UserJobResult.revision_notes` column. New lifetime per-job budget: `JobDescription.retailor_count` + `JOB_TAILOR_LIMIT` env (default 5); the router returns 409 at the cap and the budget shows in the workspace header.
- **Persisted manual `.tex` (#71).** `UserJobResult.edited_tex` (+ timestamp) with owner-scoped endpoints: `GET /tex` (seeds from `format_tex` when no edits), `PUT /tex`, `DELETE /tex`, and `POST /preview` compiling the posted buffer behind a `Semaphore(2)` + generous compile quota; compile failures return 422 with the LaTeX log tail. Exports serve `edited_tex` for tex/pdf (DOCX stays JSON-generated, noted in the UI).
- **Editor UI (#71).** `ResumeEditor` in the Resume tab: monospace buffer, Save / manual Compile-preview / Discard, PDF preview in an iframe, error surface. Section and bullet reordering via `%% ART-SECTION` markers emitted by `_build_tex` — pure text-block moves (`lib/texStructure.ts`, vitest-covered) that survive hand-edits and degrade gracefully when markers are removed.
- **Warn-then-discard.** Re-tailoring clears `edited_tex` at the tailor save block; the chat path asks a 1/2 confirmation first, and UI-initiated retries `window.confirm` when edits exist.

### Deviations from spec
- Preview is a manual "Compile preview" button (user's pick) rather than literal real-time rendering — pdflatex on the 512MB VM takes seconds per run.
- One-page auto-fit does not apply to edited-`.tex` exports (trimming operates on the tailored JSON, not raw source); overflow is visible in the preview.
- vitest added as the first frontend test infrastructure to cover the reorder logic.

---

## Issue 74 — GitHub Single Repo Ingestion
**Status:** complete | **Tests:** 485 pass (9 new)

Issue reported single-repo GitHub ingestion "doesn't seem to be working." Root cause: production has no `GITHUB_TOKEN` secret, so any user who hasn't personally connected GitHub OAuth hits GitHub's API fully unauthenticated — capped at 60 requests/hour **shared across the entire Fly app**. One single-repo import cost ~38 API calls (verified by instrumenting `requests.get` against a real repo), unbounded by repo size, because of an 8-request blind dependency-file check and a recursive one-call-per-directory-and-per-file import scan. One or two imports exhausted the whole app's quota for up to an hour, for every user — and the failure was silently mislabeled as "Could not fetch {owner}/{repo}. Check the owner/repo name," actively misdirecting the report.

### What shipped
- **Bounded, single-call file discovery.** `ingestion/github.py::GitHubIngestor` gained `_fetch_tree()`, using GitHub's Git Trees API (`git/trees/HEAD?recursive=1`) to list a repo's whole file tree in one call. `_extract_imports_from_repo` and `_fetch_dependency_files` now consume that tree instead of recursively walking directories (`_scan_directory_for_imports` deleted) — dependency-file checks only fire for filenames confirmed present (down from 8 blind requests every time), and import scanning is capped at `MAX_IMPORT_SCAN_FILES` (15) regardless of repo size. Verified call count for `openai/evals`: 38 → 20, now bounded instead of scaling with repo size.
- **Rate limits surfaced clearly instead of mislabeled.** New `GitHubRateLimitError`, raised from a single centralized `_get()` checkpoint when GitHub responds 403 with `X-RateLimit-Remaining: 0`. Propagates through `fetch_repo()`/`ingest()` (each already had a broad catch-all that was silently swallowing it into `None`/`[]`) to `services.ingest_github_repo()`/`ingest_github()`, which now return an actionable message ("try again in a few minutes, or connect your GitHub account for a much higher limit") instead of the misleading "check the owner/repo name" text — while preserving both functions' documented never-raise contract (`agents/chat.py::run_ingest_github_repo` and the FastAPI router rely on always getting a plain string back).
- **Tests (9 new).** `tests/test_ingestion_github.py` — rate-limit detection (true positive on 403+header, no false positive on plain 403/404), `fetch_repo` propagating the rate-limit instead of swallowing it, tree-based scan making exactly one tree call and staying bounded, dependency-file fetch only firing for tree-confirmed files, and the no-tree fallback still checking all known filenames. `tests/test_services.py` — rate-limit message clarity for both the single-repo and account-wide ingest paths.

### Deviations from spec
- Setting an actual `GITHUB_TOKEN` Fly secret (60/hr → 5000/hr) is the highest-leverage fix but requires generating a PAT and running `fly secrets set` — flagged as a recommended follow-up, not implemented here (infra action, not a code change).

---

## Issue 75 — Unwanted resume link removal
**Status:** complete | **Tests:** 476 pass (10 new)

Tailored resumes were losing links: header contact links (LinkedIn/GitHub/portfolio) were missing from exports, and project repo/demo links were never surfaced at all.

### What shipped
- **Root cause for the header.** The tailoring LLM never touches the header — `_build_tex_header` always renders it fresh from the `User` row. The real gap was upstream: resume ingestion detected *which* contact field types were present in the header (email/linkedin/github/phone/location) but discarded the actual values, so `User.linkedin_url`/`github_username` stayed empty unless manually retyped into the profile form.
- **Header contact backfill.** `ingestion/resume.py::extract_style_profile` now also returns `header.contact_values` (parsed email, LinkedIn URL, GitHub username, phone, and a new generic portfolio/website URL). `services.py::ingest_resume_file` backfills `User.linkedin_url`/`github_username`/`phone`/`location`/`portfolio_url` from it — **only when the field is currently empty**, so re-ingestion never clobbers a manually-curated value. New `User.portfolio_url` field, rendered in both the LaTeX and DOCX headers and exposed via `PATCH /api/profile/`.
- **Inline links preserved in body content.** The resume-parsing and tailoring LLM prompts now explicitly instruct link preservation (`[text](url)` markdown), and `agents/formatter.py::_convert_inline` gained markdown-link → `\href` conversion (previously it only handled `**bold**`/`*italic*` and silently dropped any embedded link).
- **Project repo/demo links auto-embedded (the issue's second ask).** New `Project.demo_url` field. `repo_url`/`demo_url` are no longer dropped before reaching the tailoring pipeline, but — since the LLM rewrite step is unreliable for verbatim field passthrough — they're re-attached deterministically after generation (`ResumeTailorAgent._merge_project_links`, matching the existing `_order_projects_by_selection`/`_enforce_bullet_budgets` guardrail pattern) rather than trusted to the model's JSON output. Rendered as `\href` links in the LaTeX projects section and as plain-text URLs in the DOCX export.
- **Tests (10 new).** Markdown-link → `\href` conversion, header portfolio-link rendering, project repo/demo link rendering (tex + docx) and omission when absent, contact-field backfill on ingest, no-clobber on re-ingest, and `_merge_project_links` passthrough (including the unrecognized-name case).

### Deviations from spec
- None — both asks in the issue (header preservation, auto-embedded project links) are addressed as scoped.

---

## Issue 73 — Data leakage across users
**Status:** complete | **Tests:** 466 pass (25 new)

A user reported their tailored resume showing another user's education (UCSD B.S. Math-Econ / M.S. Data Science). Diagnosis found three distinct isolation defects; all are fixed.

### What shipped
- **Per-user education storage (the reported symptom).** The resume formatter had one user's education *hardcoded* in all three export paths (LaTeX/PDF, DOCX, Markdown) — every user's export got it. New `Education` table (institution, degree, location, dates, GPA) keyed by `user_id`; resume ingestion extracts education via the LLM parser (with dedup on re-ingest), LinkedIn ingestion maps Bright Data's structured `education` records deterministically (merging with resume-ingested rows). The formatter renders the acting user's rows and **omits the section entirely when a user has none** — education is never fabricated. Existing users' education stays empty until they re-ingest a resume.
- **Knowledge graph scoped per user.** `SkillGraphBuilder` selected *all users'* skills/projects/experiences into one graph, contaminating the skill matcher's indirect-match check, the Data Explorer graph view, and the chat graph tool. It now requires a `user_id` and filters every query (skills joined through `UserSkill`); each build sees only that user's rows.
- **Request-scoped user binding replaces the global pointer file.** Web routers used to write the authenticated user's ID into the server-global `~/.art/active_profile_id` file that ~25 downstream `get_active_profile()` call sites re-read — concurrent users raced over one slot, cross-contaminating reads *and* ingestion writes. `set_request_user()` (a `ContextVar`) now binds the acting user per request context; bindings are set in async endpoint bodies (not the sync `get_current_user` dependency, which FastAPI runs in a threadpool where ContextVar writes don't propagate back) and flow through `asyncio.to_thread` into agent/service code. The pointer file survives purely as the single-user CLI fallback.
- **Chat history isolated.** Landing-context chat (`job_id=None`) was one shared conversation across *all* users, and `GET /api/chat/{job_id}/history` never checked job ownership. `ChatMessage` rows are now stamped with `user_id` (nullable column + migration; legacy NULL rows stay hidden from authenticated users), landing history is filtered by owner, and job history 403s for non-owners / 404s for unknown jobs.
- **Education tab in the Data Explorer.** `GET /api/profile/education` + an Education tab (between Experiences and Projects) so users can visually confirm their education ingested correctly — institution, degree, location, GPA, and dates rendered verbatim (dates are stored as free-form strings exactly as the resume wrote them: "June 2025", "Expected June 2027", or bare "2027"). The empty state notes that tailored resumes omit the education section until ingestion.
- **Tests (25 new).** `tests/test_education.py` — education rendering in all three formats, omission when absent, cross-user render isolation, parser save/dedup, LinkedIn mapping, service shape, endpoint caller-scoping. `tests/test_user_isolation.py` — graph node/edge scoping, graph-summary scoping, ContextVar-beats-pointer-file regression, landing-history isolation, legacy-row hiding, router ownership checks (403/404).
- **Live API verification.** Full register→history→graph→job→ownership smoke test against a running server on a scratch DB: 13/13 checks pass.

### Deviations from spec
- The issue hypothesized non-isolated knowledge graphs; that was real, but the reported symptom itself was hardcoded education in the formatter, and a third defect (shared landing chat + unchecked job-history ownership) was found and fixed in the same arc.
- Physically separate per-user graph stores were considered and rejected: the graph is an ephemeral in-memory projection of already-`user_id`-keyed relational rows, so scoping the builder's queries achieves full isolation without new infrastructure.

---

## Issue 68 — UI overhaul: OAuth-first GitHub ingest, profile menu, progress indicators
**Status:** complete | **Tests:** 441 pass (24 new)

The web UI still reflected pre-OAuth design: the GitHub ingest tab asked for a raw username even when the account was OAuth-connected, Profile sat as a left nav tab instead of under the user's name, long-running actions gave only a static text label, and ingest results showed the server temp filename (`tmpXXXX.pdf`) instead of the uploaded one.

### What shipped
- **OAuth-first GitHub ingest.** The GitHub tab now reads `/api/auth/github/status`: not connected → a "Connect GitHub" button that starts the OAuth flow; connected → one-click "Import My Repositories" (no username field). The OAuth callback redirect (`/?github_connected=1`) lands the user back on the GitHub ingest tab. A single public `owner/repo` import remains as a secondary option, and the raw-username form survives only as the fallback when OAuth isn't configured (local dev).
- **`POST /api/ingest/github` username optional.** Defaults to the connected account's `github_username`; 400 when neither a body username nor a connection exists.
- **Profile under the top-right user menu.** The header name is now a dropdown (Profile / Sign out); Profile removed from the left nav tabs. GitHub connection management stays on the Profile panel.
- **Progress indicators.** New `ProgressBar` component (indeterminate sweep + elapsed-time counter) shown during resume/GitHub/LinkedIn ingest, job analyze, and tailor; the background LinkedIn import status on the Profile panel uses it too.
- **Original filename in ingest results.** `ingest_router` passes `file.filename` through to `services.ingest_resume_file` / `ingest_linkedin_pdf` as a display name.
- **Parser hardening against malformed LLM output.** LinkedIn URL import crashed with `'str' object has no attribute 'get'` when an extraction model returned a wrapper object (`{"skills": [...]}`) or bare strings instead of a list of objects. `ResumeParserAgent._coerce_records` now normalizes all four extraction chains' output to a list of dicts, and `postprocess_skills` tolerates bare-string and non-dict items.
- **Deterministic LinkedIn entity mapping.** Bright Data's structured record (`projects`, `experience`) is now saved directly to Project/Experience rows instead of a lossy text → LLM → structure round trip — an audit showed the flattener discarded the four richest sections of a real profile (4,331 chars reduced to 336). Merge-aware upserts enrich entities already ingested from other sources (normalized/containment name matching, fill-missing-fields, `[LinkedIn]`-tagged description appends, idempotent on re-ingest) rather than duplicating them. LLM extraction still runs for skills only.
- **Lossless LinkedIn flattener.** `_brightdata_to_text` now includes projects, courses, honors and awards, and bio links, so skill extraction sees the whole profile. Verified by replaying a real scrape: 24 skills extracted vs 0 before.
- **Tests (24 new).** `tests/test_ingest_router.py` — username defaulting, explicit-username precedence, 400 without any username, filename pass-through for resume and LinkedIn PDF uploads. `tests/test_parser_coercion.py` — wrapper-object unwrapping, bare-string mapping, garbage rejection, `postprocess_skills` guards. `tests/test_linkedin_ingest.py` — lossless flattening, verbatim project saves, merge-with-existing (name and company variants, placeholder titles), idempotent description appends, LLM-bypass proof for structured entities.

### Deviations from spec
- The parser hardening and deterministic LinkedIn mapping were not in the #68 scope — they fix a production crash and a data-loss defect the user hit while testing LinkedIn URL ingestion during this arc. An Education table and raw-scrape persistence were deliberately deferred to a follow-up issue (schema changes).

---

## Issue 67 — web ingestion OOM and jobs API 404 in production
**Status:** complete | **Tests:** 416 pass (8 new)

Resume ingestion on the web OOM-killed the 512 MB Fly VM: `requirements-core.txt` shipped docling, whose converter pulls in PyTorch and loads layout models at parse time. Separately, `GET /api/jobs` 404'd because the SPA catch-all route (ES256 outage fix) fully matches slash-less `/api/*` paths before FastAPI's automatic trailing-slash redirect can fire, and `jobs.ts` fetched the slash-less form — so the job list never loaded.

### What shipped
- **docling demoted to a full-only dependency.** Removed from `requirements-core.txt` (the Docker image), added to `requirements-full.txt`; `pypdf` added to core as the lightweight fallback.
- **`ingestion/document_text.py`** — shared extraction helper: docling when installed, otherwise pypdf (PDF) / python-docx (DOCX) / plain read. Used by `ResumeIngestor` (with a new line-based section segmentation fallback) and `LinkedInIngestor.ingest_pdf`.
- **`/api/jobs` slash tolerance.** List/create routes answer with and without the trailing slash (`include_in_schema=False` aliases); `jobs.ts` now follows the repo's trailing-slash convention.
- **Tests (8 new).** `tests/test_ingest_fallback.py` — docling-free resume/LinkedIn ingestion, pypdf path, jobs-route slash regression; `tests/test_deps_split.py` — docling excluded from core, pypdf present in core, docling present in full.

### Deviations from spec
- None. The ES256 outage fixes referenced in the issue were committed separately (`3585ecd`).

---

## Issue 51 (Phase 1) & issues 15/26/27/54/58 reconciliation — tailoring efficacy benchmark, analyze fix, allocation & redundancy improvements
**Status:** complete | **Tests:** 387 pass (25 new)

Built the standing apparatus for measuring and improving tailoring quality, and fixed the three reported failure modes (experience text not tracking relevance, unselective skills, term over-repetition) plus a production-grade bug the benchmark's first run exposed.

### What shipped
- **JD dataset + scraper.** `scripts/scrape_job_descriptions.py` pulls real postings from public Greenhouse/Lever board APIs (role-filtered, HTML-stripped, deduped); 8 SWE/ML postings checked in under `eval/jd_dataset/` with a documented schema, plus a synthetic candidate profile (`eval/profiles/benchmark_profile.md`).
- **Benchmark harness (#51 Phase 1).** `eval/tailoring_benchmark.py` replays the exact user flow **through the web API** (register → login → upload resume → create job → paste JD → Analyze → Tailor → Export) via FastAPI `TestClient` on an isolated temp DB/env — production data, `~/.art`, and the deployed site are untouched. `--stub` runs fully offline behind a deterministic fake LLM + hash embedder; default mode uses real LLMs. Emits per-task + aggregate JSON, a flat CSV, and per-task `.tex`/`.json` renders under `eval/results/` (gitignored).
- **Quality metrics.** `eval/metrics.py`: ATS baseline→tailored composite/per-component deltas; experience-allocation balance (Spearman between per-experience JD relevance and bullet-word share); skills selectivity/organization (rendered count vs cap bounds, matched-skill recall, selection ratio, category order); redundancy (boundary-aware term counts — "sql" never matches inside "mysql" — over-repetition rate, bullet type-token ratio).
- **LLM-as-judge (carries #27's aim).** `eval/llm_judge.py` + `--judge`: 1–5 scores with rationales for relevance_balance / redundancy / faithfulness; malformed judge output rejected; real call integration-gated.
- **Notebook.** `eval/tailoring_benchmark.ipynb` drives the benchmark and visualizes results: aggregate charts, per-task text-allocation drill-down, tailored-resume viewer over the run's renders, cross-run trend view.
- **fix(analyze): web jobs got zero skills.** `POST /api/jobs/{id}/analyze` passed `job_id`, but `JobAnalyzerAgent.analyze_and_save` ignored it and attached all extracted `JobSkill` rows to a new orphan `JobDescription` — so web-created jobs matched with **0 skills** and tailoring ran without skill signal. Existing jobs are now analyzed in place (skills replaced idempotently, user's title/company preserved, cached JD embedding invalidated). Found by the benchmark's first run.
- **feat(tailor): relevance-based bullet budgets.** Experiences are JD-relevance ranked with per-experience `bullet_budget` (up to `TAILOR_MAX_EXP_BULLETS` for the most relevant, down to `TAILOR_MIN_EXP_BULLETS`), injected into the prompt **and enforced deterministically** post-generation.
- **feat(tailor): anti-redundancy.** Prompt rule against term stuffing; evaluator flags terms mentioned more than `TAILOR_MAX_TERM_MENTIONS` times (boundary-aware) into retry feedback.
- **Tests (25 new).** `tests/test_tailoring_benchmark.py` (metrics units, stub determinism, dataset sanity, subprocess end-to-end smoke, judge parsing/rejection + integration-gated real call) and `tests/test_prd04.py` additions (analyzer job_id regression ×3, budgets/enforcement/redundancy ×6).
- **Baseline measurement (real-LLM run, 8/8 tasks):** composite 33.0 → 75.4 (mean delta **+42.4**); allocation correlation mean 0.55/median 0.6; over-repeated terms ≤ 1 per task (mean 0.25); matched-skill recall mean 0.90.

### Issue reconciliation
- **#54, #58** — already shipped on `main`; closed and moved to Done (they were missing from the project board entirely; added).
- **#26** — memory-eval framework already shipped; closed.
- **#27** — closed as superseded: the LLM-as-judge apparatus landed in the tailoring benchmark instead of the chat-memory eval (re-file if chat-memory coherence becomes active).
- **#15** — closed without the online implementation: its measurable core (a dataset of resume/JD/output/score tuples) now exists offline via the benchmark artifacts; SaaS-scale telemetry + consent/GDPR scope conflicted with the offline-first direction and depended on unbuilt #14.
- **#51** — Phase 1 delivered by this entry; remains open (In progress) for Phase 2 score-driven tuning.

### Deviations from spec
- #51 proposed `eval/ats_tasks/` + `eval/ats_efficacy.py`; shipped as `eval/jd_dataset/` + `eval/tailoring_benchmark.py` with a strictly larger metric surface (allocation/skills/redundancy/judge on top of the composite deltas).
- The end-to-end pytest runs the harness in a **subprocess** with its own temp DB rather than on the in-process `isolated_engine` fixture — too many modules bind `engine` at import time for safe in-process rebinding; the isolation goal is met either way.
- Known tuning observation from the first real run: the skills cap saturates at `MAX_SKILLS` (18) on every task — drop-off rule never fires for a 37-skill profile. Left for #51 Phase 2 calibration.

---

## Issues 61 & 62 — Supabase-only auth migration + password recovery
**Status:** complete | **Tests:** 362 pass (18 new)

Made Supabase the single source of truth for authentication in production and added a self-service password-recovery flow on top of it. Previously `/login` verified a local PBKDF2 hash *first* and only then minted a Supabase JWT, so a Supabase-only password reset would have locked users out — the dual credential stores had to stay in sync. Login is now by **email + password**, production authenticates against Supabase alone (**fail-closed**: the local password/cookie path can never run when Supabase is configured), and password reset "just works" because there is no local hash to reconcile. An offline local fallback is retained for dev/tests only.

### What shipped
- **Auth mode gate.** `database/auth.py::supabase_configured()` — single source of truth for the mode (env vars set AND `supabase` importable). `web/auth.py::get_current_user` is fail-closed: Supabase JWT only when configured, local signed cookie only when not.
- **Email login + Supabase-owned credential.** `web/routers/auth_router.py` — `/login` takes `email`+`password` with a generic "Invalid email or password" error (no enumeration); in Supabase mode `/register` stores **no** local `password_hash` (column kept for schema back-compat) and handles the email-confirmation-pending case. Login/reset backfill `supabase_uid` so pre-migration accounts resolve from the JWT `sub`. New `database/user_utils.py` helpers: `authenticate_local_email`, `set_supabase_uid`, `set_local_password`.
- **Password recovery.** New Supabase helpers `supabase_send_password_reset` and `supabase_update_password` (recovery-session `set_session`→`update_user`, least-privilege — no service-role key). New endpoints: `GET /api/auth/capabilities`, `POST /api/auth/forgot-password` (generic 200, 503 in local fallback), `POST /api/auth/reset-password` (min 8-char strength enforced before Supabase is called; invalid/expired token → 400).
- **Frontend.** `LoginPage` switched to email + conditional "Forgot password?" link (gated on `capabilities`); new `ForgotPasswordPage` and `ResetPasswordPage` (reads recovery tokens from the URL fragment, strips them from history, confirm + strength validation); routes wired in `App.tsx`; `api/auth.ts` gains `getAuthCapabilities`/`forgotPassword`/`resetPassword` and email login.
- **Email-confirmation redirect (#62).** `supabase_sign_up` now accepts `email_redirect_to`, and `/register` passes `<APP_BASE_URL>/login` so the sign-up confirmation link lands on the login page instead of the Supabase Site URL / app root. Generalized the router's `_app_url(request, path)` helper (shared with the reset redirect).
- **Tests.** `tests/test_password_reset.py` (18) — capabilities, dev-fallback email login, Supabase-mode login + uid backfill, no-local-hash register, sign-up confirmation `/login` redirect, no-enumeration forgot-password, reset success/invalid-token/weak-password, and local-fallback 503s.
- **Docs.** `web/CLAUDE.md` auth-flow section rewritten; `.env.example` documents the all-Supabase behavior + `APP_BASE_URL` (reset-link redirect origin, must be allowlisted in Supabase → Auth → URL Configuration).

### Deviations from spec
- Scope expanded beyond the original "add password recovery" issue to a full Supabase-only auth migration (agreed with the maintainer), because reset could not be made safe while login depended on a separately-synced local hash.
- Local dev/tests deliberately retain the offline password path; it is unreachable in production (Supabase always configured there), so it is not a production attack surface.
- Reset uses the recovery session (`set_session` + `update_user`) rather than the service-role admin API, keeping the flow least-privilege. `SUPABASE_SERVICE_ROLE_KEY` remains unused by this feature.

---

## Repo hygiene — public-readiness cleanup + service-layer extraction
**Status:** complete | **Tests:** 344 pass (0 new; net −37 from removing the TUI test suite)

Prepared the repository to be made public: removed personal data and the
deprecated Textual TUI, extracted the shared service layer to a top-level
module, and added a proper README. No product behavior changed.

### What shipped
- **Service-layer extraction.** `tui/services.py` → `services.py` (self-contained business logic used by the web app, agents, and CLI); rewrote 45 import sites. The `tui.services` string key in the eval stub map was updated to `services`.
- **TUI retirement.** Deleted `tui/` (app, screens, widgets), `tests/test_tui.py`, the textual-web `tui`/`serve` CLI commands + `cmd_serve`, `launch.bat`, `launch.ps1`, `textual-web.toml`, and `scripts/automation_smoke_check.py`. Dropped `textual`/`plotext` from `requirements*.txt`. Web-deploy structural checks preserved as `tests/test_web_deploy.py`.
- **Personal data + artifact removal.** Removed personal resume/cover-letter/parsed-resume files, `notebooks/`, one-off `debug_*`/`test_*` scripts (kept operational scripts), the empty `agentic_resume.db`, `test.txt`, `knowledge_graph.png`, and tracked `__pycache__/*.pyc`. Made `tests/test_integration.py` self-contained via synthetic `tests/fixtures/` (`sample_resume.md`, `sample_job.txt`). Expanded `.gitignore` for personal materials and local DBs.
- **Docs.** Added `README.md` (overview, architecture, quickstart, deploy). Rewrote `docker-compose.yml` and `INSTALL.md` to serve the web app (uvicorn) instead of the TUI. Deleted the obsolete `STARTUP.md` and `.github/instructions/tui.instructions.md`. Updated `CLAUDE.md`, `agents/CLAUDE.md`, `.github/copilot-instructions.md`, and `supabase/README.md` to drop TUI references.

### Deviations from spec
- Historical `CHANGELOG.md` entries, `docs/prd/`, and `docs/ROADMAP.md` were left untouched — they accurately describe past work (including the then-existing `tui/services.py`) and are treated as historical records.
- The PII purge from git *history* (via `git filter-repo`) is performed as a separate final step outside this commit, since it rewrites all commits.

---

## Issues 54 & 58 — Skills Section Tailoring + Best-of-N Attempt Selection
**Status:** complete | **Tests:** 381 pass (38 new)

Reworked how the Technical Skills section is built and how the tailoring loop selects its final output. Previously the skills section rendered the full skill list under static alphabetical categories (never consulting the job description), and the generate→evaluate loop shipped whatever the last retry produced. Now skills are JD-relevance ranked, capped, and role-aware ordered with a semantic signal and persistent pinned "core" skills, and the loop ships the best-scoring attempt rather than the last. Delivered as #54 Phases 1–4 plus the #58 follow-up.

### What shipped
- **Phase 1 (#55) — JD-relevance ranking, cap, role-aware ordering.** `agents/skill_scorer.py` (**created**) — pure-function scorer (`score_skills`, `select_skills`, `rank_and_select_skills`) blending TF-IDF (with IDF over the JD corpus), JD weight, match confidence, proficiency, and evidence; dynamic cap via drop-off + min/max bounds. `agents/tailor.py` — `_rank_skills()` persists `tailored_content["skills_ranked"]`. `agents/formatter.py` — renders the JD-ranked list in relevance order (falls back to the full DB list). `agents/ats_scorer.py` — skills flattening prefers `skills_ranked`.
- **Phase 2 (#56) — persistent embeddings + semantic component.** `agents/skill_embeddings.py` (**created**) — shared MiniLM cache (`ensure_skill_embeddings`, `load_skill_vectors`, `ensure_job_embedding`), degrades gracefully when the model is unavailable. `database/models.py` + `database/db.py` — `Skill.embedding/embedding_model` and `JobDescription.embedding/embedding_model` columns + backward-compatible ALTER migrations. Reingest hooks recompute embeddings (`agents/parser.py`, `tui/services.py`); `agents/chat.py` invalidates the cached JD embedding on job re-analysis. `scripts/backfill_skill_embeddings.py` (**created**).
- **Phase 3 (#57) — pinned core skills.** `database/models.py` — `UserSkill.is_core` (+ migration). Pinned skills always render and seed a relevance floor. Surfaced end-to-end: `web/routers/profile_router.py` (`POST /api/profile/skills/core`), web frontend ★ pin toggle in the Skills tab, `cli.py` `pin-skill` command, and `tui/services.py` `set_skill_core`.
- **Phase 4 (#54) — tunable weights + offline tuning harness.** `agents/skill_scorer.py` — all weights and cap bounds env-overridable (`SKILL_W_*`, `SKILL_MIN/MAX`, etc.) with unchanged defaults, plus per-call `weights`/`bounds` overrides and a `selection_recall` metric. `eval/skill_selection_eval.py` (**created**) — LLM-free harness over checked-in fixtures comparing weight presets by recall + rendered count.
- **Issue 58 — best-of-N attempt selection.** `agents/tailor.py` — the generate→evaluate loop tracks the highest-scoring attempt by algorithmic composite and ships the argmax (falling back to the last content only when no attempt scored), runs the full `MAX_RETRIES` budget by default, and early-exits only above a high "great" bar. Budget and great-bar thresholds are env-overridable (`TAILOR_MAX_RETRIES`, `TAILOR_GREAT_SKILL_COVERAGE`, `TAILOR_GREAT_KW_COVERAGE`).
- **Tests** — `tests/test_skill_scorer.py` (12), `tests/test_skill_embeddings.py` (8), `tests/test_skill_pinning.py` (7, incl. web `TestClient`), `tests/test_skill_tuning.py` (7), and 4 best-of-N tests in `tests/test_prd04.py`.

### Deviations from spec
- Kept the local MiniLM embedding model rather than adding a provider-swappable embedding config — semantic scoring degrades to lexical + metadata signals when the model is unavailable.
- Final calibration of the skill-scoring weights and the #58 great-bar thresholds is deferred to the #51 ATS efficacy benchmark; this arc ships the tunable mechanism with sensible (un-calibrated) defaults.

---

## Issue 13 — LinkedIn Ingestion via Bright Data
**Status:** complete | **Tests:** 332 pass (10 new)

Replaced the Playwright LinkedIn scraper with Bright Data's Web Scraper API and surfaced LinkedIn ingestion in the web app for the first time. The scrape auto-triggers when a user sets/changes their LinkedIn URL (the "initialize/update knowledge graph" moment) and runs in the background; PDF upload remains as a fallback.

### What shipped
- `ingestion/linkedin.py` — new `ingest_brightdata()` (trigger → poll `/progress` → download `/snapshot`) + `_brightdata_to_text()` flattener; removed `ingest_web` and the Playwright/bs4 scraping path. `ingest_pdf` fallback retained.
- `config.py` — `BRIGHTDATA_API_KEY` (platform-wide) and `BRIGHTDATA_LINKEDIN_DATASET_ID` (default `gd_l1viktl72bvl7bjuj0`).
- `database/models.py` + `database/db.py` — `User.linkedin_ingested_url/linkedin_ingest_status/linkedin_ingest_error/linkedin_ingested_at` columns + backward-compatible ALTER migrations.
- `tui/services.py` — `ingest_linkedin(url, user_id)` records the importing/done/failed lifecycle; never raises.
- `web/routers/ingest_router.py` — `POST /api/ingest/linkedin` and `/linkedin/pdf`.
- `web/routers/profile_router.py` — `PATCH /api/profile` schedules a background ingest when the URL changes; GET exposes ingest status.
- Frontend — LinkedIn tab in `IngestPanel` (URL + PDF fallback) and a live import-status indicator in `ProfilePanel`.
- `cli.py` — `ingest-linkedin` rewired to Bright Data.
- Deps — removed `playwright` from `requirements*.txt`, lockfile, and generator; repointed `test_deps_split.py` heavyweight checks to `sentence-transformers`.

### Deviations from spec
- Issue framed an optional per-user key with a "users without API access" fallback; shipped a platform-wide key (hosted SaaS) with PDF upload as the fallback when the key is unset.

---

## PRD 10 — Persistent Per-Job Chat Memory
**Status:** complete | **Tests:** 108 pass (6 new)

Every chat message is now written to SQLite on each turn. On app restart, selecting a job replays prior messages in the scroll and restores `ChatAgent.history` so the AI retains full context.

### What shipped
- `database/models.py` — `ChatMessage` table (`message_id`, `job_id`, `role`, `content`, `created_at`); auto-created by `SQLModel.metadata.create_all` on next startup; no manual migration needed
- `tui/services.py` — `save_chat_message(job_id, role, content)` and `load_chat_history(job_id, limit=20)`; both non-raising with try/except; `job_id=None` represents landing context
- `agents/chat.py` — `chat()` lazily imports `tui.services` and calls `save_chat_message` after every append to `self.history` (fast-path, tool-call, and clarify/response paths); `set_active_job()` loads DB history on cold start and sets `self.history` if non-empty
- `tui/app.py` — `_show_job_details()` calls `services.load_chat_history()` on first visit and reconstructs the scroll before the job detail card; populates `_job_chat_cache` from DB result
- `tests/test_services.py` — 3 new tests: round-trip for job, landing context (`job_id=None`), limit behavior
- `tests/test_chat.py` — 3 new tests: persistence of user + assistant rows, history restore via `set_active_job`, DB write failure does not affect response

### Deviations from spec
- None

---

## PRD 05 — Desktop Productization, Cloud Models, And Data Security
**Status:** complete | **Tests:** 64 pass

Moved app data to `~/.art/`, wired startup config validation, added Windows launchers, and documented the install process.

### What shipped
- `config.py` — `APP_DATA_DIR = Path.home() / ".art"`, `EXPORTS_DIR`, `UPLOADS_DIR`, `LOGS_DIR`; `DATABASE_URL` moved from `{root}/art.db` to `~/.art/art.db`; `ensure_app_dirs(base_dir=None)` for idempotent directory creation
- `config_validator.py` — **created**; `validate_config() -> list[str]` checks `LLM_PROVIDER` value, API key presence for chosen provider, Ollama reachability, and `APP_DATA_DIR` writability
- `database/db.py` — `_migrate_db_location()` runs before engine creation; copies `{project_root}/art.db` → `~/.art/art.db` once, non-destructively, and logs the migration
- `tui/app.py` — `on_mount` calls `ensure_app_dirs()` then `validate_config()`; config errors shown in status bar as `[CONFIG ERROR] ...`
- `cli.py` — `_check_config()` helper added; called at top of every command; prints errors and exits with code 1 on failure
- `launch.bat` — **created**; Windows CMD launcher
- `launch.ps1` — **created**; PowerShell launcher
- `.env.example` — **created**; all recognized env vars with comments
- `INSTALL.md` — **created**; install guide covering clone → venv → pip → `.env` → launch
- `tests/test_prd05.py` — 5 tests: missing API key (OpenAI), missing API key (Anthropic), unknown provider, `ensure_app_dirs` subdirectory creation, no secrets in logs

### Deviations from spec
- `validate_config()` accepts `"anthropic"` as a valid `LLM_PROVIDER` (not listed in the PRD spec but is the current default)
- `ensure_app_dirs()` accepts an optional `base_dir` parameter to keep tests hermetic
- Disabling ingestion/tailoring buttons on config error deferred — status bar error message is the signal; button disabling requires TUI-layer changes beyond PRD 05 scope

---

## Session — Chat Routing Overhaul, TUI Polish, Profile Overlay
**Status:** complete | **Tests:** 38 pass

Rewrote the chat fast-path to be context-aware and far less aggressive, added a numbered-options system for ingestion, replaced flat skill queries with a job-match view, and shipped several TUI quality-of-life fixes.

### What shipped
- `agents/chat.py` — removed `COMMAND_PHRASES` and all fuzzy/token-based data-query routing; only ingestion entry-points and help bypass the LLM now; added `_pending_options` dict and `_last_bot_asked_question()` so short replies after a bot question reach the LLM; added `_ingest_github_with_options()` for numbered GitHub ingestion flow; added step 1c token-combo detection (catches "ingest skill from my github" etc.); removed short-message guard; added `query_skills_vs_jobs()` (shows ATS score + matched/missing per job — preferred over raw skill dump in chat)
- `tui/screens/onboarding.py` — redesigned as a 4-step sequential flow (name → resume → GitHub → LinkedIn) with a file-upload button via tkinter and skip options for optional steps
- `tui/screens/profile.py` — **created**; `ProfileScreen` overlay with avatar initials, editable name/GitHub/LinkedIn fields, and live stats; dismissed via Escape or Save
- `tui/app.py` — status bar avatar button opens ProfileScreen; `/copy` slash command strips Rich markup and pipes to `clip.exe`; `ctrl+c` bound to noop; SIGINT suppressed at process level; F1–F4 replaced with slash commands
- `tui/services.py` — added `get_profile_data()` and `update_profile()`
- `CLAUDE.md` — **created**; project conventions, test coverage requirements, commit/changelog practices
- `.gitignore` — `tailored_output.json` and `tailored_resume.md` untracked (generated artifacts)
- `test_smoke_formal.py` — 9 new tests covering: onboarding step validation, slash-command routing, profile services, ProfileScreen mount, ctrl+c binding, clipboard, pending-option resolution, token-combo routing for all three ingestion sources, `query_skills_vs_jobs`

### Deviations from spec
- Short-message fast-path removed entirely rather than just relaxed — LLM is fast enough to handle short queries gracefully
- `query_skills()` retained in `TOOL_MAP` for cases where the LLM explicitly wants a raw skill dump; `query_skills_vs_jobs()` is the preferred tool surfaced in the system prompt

---

## PRD 03 — Onboarding, Profile Ingestion, And Knowledge Graph UX
**Status:** complete | **Tests:** 16 pass

Replaced the hardcoded single-user prototype with an explicit local active profile. Added a first-run onboarding screen to the TUI, structured the knowledge graph view, and wired GitHub ingestion as a post-onboarding option.

### What shipped
- `database/models.py` — added `onboarding_complete` (bool) and `onboarding_steps` (JSON) columns to `User`
- `database/db.py` — added `_migrate_db()` for backward-compatible SQLite column additions on existing DBs
- `database/user_utils.py` — replaced `get_or_create_default_user()` with `get_active_profile() -> User | None` and `create_profile(name, email, github_username, linkedin_url) -> User`; active profile persisted at `~/.art/active_profile_id`; old function kept as backward-compat wrapper
- `tui/screens/onboarding.py` — **created**; `OnboardingScreen` collects name/email/resume path/GitHub/LinkedIn, validates inputs, runs ingestion in a background worker with progress messages, then dismisses back to main app
- `tui/app.py` — `on_mount` calls `get_active_profile()` and pushes `OnboardingScreen` if `None`; `_on_onboarding_done` callback refreshes state and offers GitHub ingestion button
- `tui/services.py` — `get_first_user_id()` updated to use `get_active_profile()`; added `get_graph_summary()` and `ingest_github_for_profile()`
- `agents/chat.py` — all `select(User).limit(1)` calls replaced with `get_active_profile()`
- `graph/pipeline.py` — `ingest_resume_node` prefers `get_active_profile()`, falls back to wrapper
- `test_smoke_formal.py` — fixture patches `user_utils`; `_seed_user_and_skill` writes profile pointer; 4 new tests added

### Deviations from spec
- `get_or_create_default_user()` retained as backward-compat wrapper rather than removed — pipeline and CLI paths still reference it
- `on_mount` loads data tables before pushing onboarding so empty-state placeholders render correctly if user dismisses the screen
- `ACTIVE_PROFILE_FILE` exposed as module-level var in `user_utils` to allow monkeypatching in tests
- `test_onboarding_screen_mounts` wraps `OnboardingScreen` in a minimal `App` because `run_test()` is only available on `App`

---

## PRD 02.5 — Chat-Triggered Ingestion And Tailoring
**Status:** complete | **Tests:** 12 pass

Wired the ingestion and tailoring pipelines directly into the TUI chat agent. Users can now type `ingest resume <path>`, `ingest github`, or `tailor <job>` in the chat box and get results without the LLM being invoked — all handled by argument-parsing fast-paths.

### What shipped
- `tui/services.py` — `ingest_resume_file`, `ingest_github`, `ingest_linkedin_pdf` service functions (no exceptions escape to caller)
- `agents/chat.py` — four tool functions (`run_ingest_resume`, `run_ingest_github`, `run_ingest_linkedin_pdf`, `run_tailor`), registered in `TOOL_MAP`; regex fast-paths added to `_semantic_command_match`; `ingest github` shortcut added
- `test_smoke_formal.py` — 4 new fast-path and error-handling tests

### Deviations from spec
- Fast-path regexes match against the raw (un-normalized) message so file paths with dots/slashes are preserved — the normalizer would strip them
- `run_tailor` monkeypatched at the `chat_module` level in tests since it's defined in the same module as the agent
- LinkedIn web scraping (Playwright) intentionally excluded — too heavy for a chat thread

---

## PRD 02 — Chat Latency Reduction And Model Routing
**Status:** complete | **Tests:** 8 pass

Expanded the fast-path routing in the chat agent and replaced the brittle two-provider LLM factory with a role-based abstraction. OpenAI is now the default provider.

### What shipped
- `config.py` — added `CHAT_MODEL`, `EXTRACT_MODEL`, `TAILOR_MODEL` env vars (default `gpt-4o-mini`); `ANTHROPIC_API_KEY` stub; changed default `LLM_PROVIDER` from `ollama` to `openai`
- `llm.py` — rewritten with `ModelRole` constants and role-aware `get_llm(role, temperature)`
- `agents/chat.py` — expanded `SHORTCUTS` and `COMMAND_PHRASES` (help, job, graph commands); `get_help_text()` added; short-message fast-path for < 4 unrecognized tokens; latency logging at DEBUG level
- `agents/parser.py`, `job_analyzer.py` — role=`"extract"`
- `agents/tailor.py` — role=`"tailor"`
- `agents/enhancer.py` — role=`"chat"`
- `test_smoke_formal.py` — 3 new tests; monkeypatch lambda updated for role kwarg

### Deviations from spec
- Short-message fast-path triggers on messages with < 4 tokens that don't match any shortcut or phrase (exact spec wording)
- `test_get_llm_roles` mocks `ChatOpenAI` at the `langchain_openai` module level to avoid needing a real API key in CI

---

## PRD 01 — TUI Stabilization And Workflow Foundation
**Status:** complete | **Tests:** 5 pass

Separated DB queries from widget rendering, added explicit app state tracking, and made the TUI guide users through the workflow instead of requiring knowledge of hidden commands.

### What shipped
- `tui/services.py` — created; DB query functions extracted from widget methods, each returning plain data (lists/dicts)
- `tui/app.py` — `AppState` constants; `_refresh_app_state()`; status bar below header; empty-state placeholder rows in all three data tables; `_load_graph_view` moved to `@work(thread=True)`
- `test_smoke_formal.py` — 3 new tests (empty-state tables, `_refresh_app_state` return values, status bar text); `tui.services.engine` patched in fixture

### Deviations from spec
- `_load_viz` retains direct DB access — its multi-query charting logic was out of scope for task 1
- Status bar uses `--` instead of `—` (em-dash) for Windows ASCII terminal safety
- `_refresh_app_state()` returns the state string rather than relying on callers reading `app.app_state`, enabling cleaner testability
