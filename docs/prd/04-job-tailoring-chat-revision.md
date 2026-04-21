# PRD 04 — Job Workspace, Tailoring Feedback, And Iterative Chat Revision

> **Prerequisite:** PRDs 01, 02, and 03 must be complete.
> **Prerequisite reading:** Read `graph/pipeline.py`, `database/models.py` (`JobDescription`, `UserJobResult`), `agents/tailor.py`, `agents/matcher.py`, `agents/chat.py`, and `tui/app.py` (job sidebar section) before starting.
> **Baseline test:** `python -m pytest test_smoke_formal.py -q` — all tests must pass before and after.
> **Core problem:** Jobs are created and forgotten. After tailoring, the output is a static file. There is no job lifecycle, no explainability of what changed or why, and no way to continue refining the output from the TUI.

---

## Task Checklist

### 1 — Add job lifecycle state to the DB model
- [ ] Add a `status` column to `JobDescription` in `database/models.py` with values: `created`, `analyzed`, `tailored`, `exported`
- [ ] Default value: `"created"`
- [ ] Add a `revision_notes: str` nullable column to `UserJobResult` to store the latest revision instruction
- [ ] Add an `export_path: str` nullable column to `UserJobResult` to store the path of the last exported file
- [ ] New columns must have defaults — existing rows must not break

### 2 — Show job lifecycle state in the sidebar
- [ ] In `tui/app.py` `_load_jobs_sidebar`, update the label for each job to show its status: `"Senior Eng @ Acme [tailored 92%]"` or `"Marketing Mgr @ Corp [created]"`
- [ ] Selecting a job that is `created` (no analysis run) must show a clear call to action: `"No analysis yet — type 'analyze' or paste the job description below"`
- [ ] Selecting a job that is `tailored` must show: latest ATS score, matched/missing skills summary, and `"Type a revision request or press F3 to re-tailor"`

### 3 — Accept job description as chat input
- [ ] In `agents/chat.py`, add a fast-path handler: if the user message is longer than 100 characters and `active_job` has status `created`, treat the message as a job description paste
- [ ] Store the text as `JobDescription.description` and update status to `"created"` (it already was, but now it has content)
- [ ] Respond with: `"Job description saved. Type 'analyze' to extract skills and requirements."`
- [ ] Add `"analyze"` to `SHORTCUTS` → triggers `analyze_active_job()` tool

### 4 — Add `analyze_active_job` and `tailor_active_job` tool functions
- [ ] Add to `agents/chat.py` TOOL_MAP:
  - `analyze_active_job(args)` — calls `JobAnalyzerAgent().analyze_and_save(...)` for the active job, updates status to `"analyzed"`, returns skill extraction summary
  - `tailor_active_job(args)` — runs the full `build_pipeline()` for active job/user, updates `UserJobResult` and job status to `"tailored"`, returns match summary
- [ ] Both functions require an active job — return error message if none selected
- [ ] Add `"analyze"`, `"tailor"`, `"tailor resume"`, `"run tailoring"` to `SHORTCUTS`

### 5 — Active job context in `ChatAgent`
- [ ] Add `self.active_job_id: str | None = None` to `ChatAgent.__init__`
- [ ] Add a `set_active_job(job_id: str)` method on `ChatAgent`
- [ ] In `tui/app.py`, call `self._get_agent().set_active_job(job_uuid)` when a job is selected from the sidebar
- [ ] When `active_job_id` is set, prepend a brief job context line to the LLM system prompt: `"Active job: {title} @ {company} | Status: {status} | ATS: {score}%"`
- [ ] Revision requests in chat (detected when message contains words like `"change"`, `"make it"`, `"add more"`, `"remove"`, `"tone"`) must use `active_job_id` as context

### 6 — Tailoring explainability output
- [ ] After `tailor_active_job` completes, the response must include a structured summary with these labeled sections:
  - `Matched (evidence-backed)` — skills confirmed in profile with source
  - `Emphasized` — skills that exist in profile and were featured prominently
  - `Inferred (low evidence)` — skills added that have weak or no profile backing — **always label these explicitly**
  - `Missing` — job requirements not addressed in the tailored output
- [ ] This summary must be shown in the chat view, not buried in a file
- [ ] Store this summary in `UserJobResult.matched_skills` (already a JSON column)

### 7 — Export command
- [ ] Add `"export"`, `"export resume"`, `"save resume"` to `SHORTCUTS` → triggers `export_active_job()`
- [ ] `export_active_job()` writes `tailored_resume_{job_title}_{timestamp}.md` to `~/.art/exports/`
- [ ] Updates `UserJobResult.export_path` with the file path
- [ ] Responds with the full export path

### 8 — Add tests
- [ ] Add to `test_smoke_formal.py`:
  - `test_job_lifecycle_status_transitions` — create job → analyze → tailor → verify `status` field in DB
  - `test_chat_sets_active_job_context` — `agent.set_active_job(uuid)`, then `agent.chat("analyze")` routes to `analyze_active_job` without LLM
  - `test_tailoring_explainability_sections` — after `tailor_active_job`, response contains all 4 labeled sections
  - `test_export_creates_file` — `export_active_job()` writes a file and returns its path

---

## Key Files

| File | Role |
|---|---|
| `database/models.py` | Add `status` to `JobDescription`, `revision_notes`/`export_path` to `UserJobResult` |
| `agents/chat.py` | Add `active_job_id`, `set_active_job`, new tool functions, new shortcuts |
| `graph/pipeline.py` | Used by `tailor_active_job` — read but do not modify node implementations |
| `agents/job_analyzer.py` | Used by `analyze_active_job` — read before wrapping |
| `agents/tailor.py` | Used by `tailor_active_job` — read before wrapping |
| `tui/app.py` | Call `set_active_job` on sidebar selection; update sidebar labels |
| `test_smoke_formal.py` | Add new tests |

---

## Do Not Touch

- `graph/pipeline.py` node implementations — wrap them, do not modify them
- `agents/formatter.py` — used by pipeline; do not change its interface
- `ingestion/` — no ingestion changes
- `llm.py` — no model changes
- `cli.py` — no CLI changes

---

## Constraints

1. Tailoring must still work from the CLI — `cli.py tailor <job>` must continue to function
2. The `inferred (low evidence)` label on weak skills is a trust/safety requirement — do not remove it
3. Exports go to `~/.art/exports/` — use `pathlib.Path.home() / ".art" / "exports"`
4. Do not add new pip dependencies
5. `active_job_id` must not persist across app restarts unless the user explicitly selects a job — it is a session-only concept
6. Schema additions must use `Optional` with defaults for backward compatibility

---

## Verification

```bash
python -m pytest test_smoke_formal.py -q
```

Then manually:
1. Create a new job in the sidebar
2. Paste a job description in chat
3. Type `analyze` — should show skill extraction
4. Type `tailor` — should show explainability summary with all 4 sections
5. Type `export` — should print a file path under `~/.art/exports/`

---

## Background Context

<details>
<summary>Current tailoring state</summary>

The current `graph/pipeline.py` runs a full LangGraph pipeline: ingest → analyze job → match skills → tailor → format. The output is written to `tailored_output.json` and `tailored_resume.md` in the project root. The `UserJobResult` model already stores `matched_skills`, `missing_skills`, and `tailored_resume_content` as JSON columns, but this data is never surfaced back to the user in the TUI. The `JobDescription` model has no status field. The chat agent has no concept of an active job. This PRD adds all the connective tissue.
</details>

---

## Progress

> Claude Code: update this section as you work. Do not delete unchecked items.

**Status:** `not started`

### Files Modified
_None yet_

### Files Created
_None yet_

### Completed Tasks
_None yet_

### Notes / Deviations
_Any decisions made that differ from the spec above_