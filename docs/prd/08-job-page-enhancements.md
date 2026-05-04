# Task 08 — Job Page Enhancements

## Context
The TUI sidebar (`tui/app.py`) shows a `ListView` of saved jobs. Selecting a job
appends its detail card to the global chat scroll — there is no per-job chat
isolation and no way to delete a job. This task adds job deletion and per-job
chat session caching.

**Dependency:** Task 10 (persistent chat memory) builds on this task. Task 08
implements in-memory per-job caching. Task 10 makes that cache durable across
restarts by backing it with SQLite. Implement Task 08 first, then Task 10.

---

## Required reading — read ALL before writing any code

- `CLAUDE.md` (root)
- `tui/CLAUDE.md`
- `agents/CLAUDE.md`
- `tui/app.py` — entire file; all changes are here and in services
- `tui/services.py` — `get_jobs`, `get_job_details`, DB patterns
- `database/models.py` — `JobDescription`, `UserJobResult`
- `agents/chat.py` lines 470–510 — `ChatAgent.__init__`, `set_active_job`
- `agents/chat.py` lines 1060–1160 — `chat()` method and `self.history` usage
- `tests/test_prd04.py` — existing job lifecycle tests
- `tests/test_tui.py` — TUI test patterns
- `tests/conftest.py` — `isolated_engine` fixture

---

## Task 1 — Delete jobs from the sidebar

### UI change
In `_load_jobs_sidebar()`, restructure each `ListItem` to contain both the
existing `Label` and a small `Button("×", id=f"del-{item_id}")` right-aligned.

Update CSS so the delete button sits flush right without breaking the label
layout. Suggested: wrap label and button in a `Horizontal` inside the `ListItem`.

### Button wiring
In `on_button_pressed()`, detect button IDs starting with `"del-"`. Extract
the suffix as `item_id`, look up `job_uuid` from `self._job_item_to_uuid`, then
call `self._delete_job(job_uuid)`.

### Confirmation
Before deleting, mount a one-line confirmation message in `#chat-scroll`:
```
"Delete '{title} @ {company}'? This cannot be undone. [Yes] [No]"
```
Render this as a `Static` with two inline `Button` widgets:
`confirm-delete-job-btn` and `cancel-delete-job-btn`. Wire them in
`on_button_pressed`. On cancel, remove the confirmation widget. On confirm,
proceed with deletion.

### `_delete_job` method (in `ArtApp`)
1. Call `services.delete_job(job_uuid)`.
2. Remove the job's entry from `self._job_chat_cache` (see Task 2).
3. If `job_uuid == self._selected_job_id`:
   - Clear `self._selected_job_id` and `self._selected_job_label`.
   - Call `self._get_agent().set_active_job(None)`.
   - Clear `#chat-scroll` and restore the landing welcome message from cache.
4. Call `_load_jobs_sidebar()` and `_refresh_app_state()`.

### Service function — `services.delete_job`
Add to `tui/services.py`:
```python
def delete_job(job_uuid: str) -> str:
    """Delete a JobDescription and all its UserJobResult rows.
    Returns plain-English result. Never raises."""
```
Steps:
1. Delete all `UserJobResult` rows where `job_id == UUID(job_uuid)`.
2. Delete the `JobDescription` row.
3. Return `"Job deleted."` on success or an error message on failure.
Wrap in try/except — never raise from this function.

Old ATS scores (`UserJobResult` rows) are not useful without the job that
generated them. Cascade-delete them.

---

## Task 2 — Per-job chat caching (in-memory)

### Goal
Each job gets its own isolated chat scroll and agent history. Selecting a new
job shows a blank chat. Returning to a previously visited job replays all prior
messages from that session.

This is in-memory only. Full cross-session persistence is added in Task 10.

### State to add in `ArtApp.__init__`
```python
self._job_chat_cache: dict[str, list[tuple[str, str]]] = {}
# key = job_uuid string, or "landing" for the pre-job welcome state
# value = list of (css_class, message_text) tuples in render order

self._active_context_key: str = "landing"
```

### Capture messages as they are rendered
In `_post_chat_response()`, after mounting the `Static`, append:
```python
self._job_chat_cache.setdefault(self._active_context_key, []).append(
    ("bot-msg", response)
)
```

In `_handle_chat_input()`, after mounting the user message `Static`, append:
```python
self._job_chat_cache.setdefault(self._active_context_key, []).append(
    ("user-msg", f"You: {text}")
)
```

### Seed the landing cache on startup
In `on_mount()`, after the welcome `Static` is composed, seed:
```python
self._job_chat_cache["landing"] = [
    ("bot-msg", "<welcome message text>")
]
```
This allows restoring the welcome screen if the user returns from a job to the
landing state.

### Job selection — `_show_job_details`
Replace the current "append to existing scroll" behavior with:

1. Save current scroll state is already captured by the append hooks above — no
   explicit save step needed.
2. Set `self._active_context_key = job_uuid`.
3. Clear `#chat-scroll` using `scroll.remove_children()` (or remove each
   widget).
4. **If `job_uuid` is already in `self._job_chat_cache`** (returning to a prior
   job in this session):
   - Re-mount each `(css_class, text)` tuple as a `Static(text, classes=css_class)`.
   - Do **not** mount the job detail card again (it is already in the cache).
   - Restore agent history via `agent.set_active_job(job_uuid)` (see Task 3).
5. **If `job_uuid` is NOT in cache** (first visit in this session):
   - Mount the job detail card (existing `_show_job_details` content).
   - Append it to cache: `self._job_chat_cache[job_uuid] = [("bot-msg", detail_text)]`
   - Call `agent.set_active_job(job_uuid)` with empty history.

### Returning to landing state
If the selected job is deleted and `self._active_context_key` needs to reset
to `"landing"`, restore from `self._job_chat_cache["landing"]`.

---

## Task 3 — Per-job agent history

### Extend `ChatAgent` in `agents/chat.py`

Add to `ChatAgent.__init__`:
```python
self._job_histories: dict[str | None, list] = {}
self._active_job_id: str | None = None
```

Update `set_active_job(job_id)`:
```python
def set_active_job(self, job_id: str | None) -> None:
    # Save current history under the current job key
    self._job_histories[self._active_job_id] = list(self.history)
    # Switch
    self._active_job_id = job_id
    # Restore history for the new job (empty list if first visit)
    self.history = list(self._job_histories.get(job_id, []))
```

This means switching back to a previously visited job in the same session
restores the LLM's full conversation context.

Task 10 will extend `set_active_job` further to load from DB on first visit,
so the agent also remembers history from previous app sessions.

---

## Architecture constraints

- No direct DB calls in `ArtApp` — use `tui/services.py`.
- `ChatAgent.set_active_job` must remain backward-compatible; existing tests
  must still pass.
- The delete `×` button must not be accidentally triggered when selecting a job.
  Use a distinct non-overlapping click target.
- Keep `_job_item_to_uuid` in sync: `_delete_job` must also remove the entry
  from this dict.

---

## Satisfaction criteria

- [ ] Clicking "×" on a job shows a confirmation prompt in the chat area.
- [ ] Confirming deletion removes the job from the sidebar and from the DB,
      including all its `UserJobResult` rows.
- [ ] If the deleted job was active, the scroll resets to the landing state.
- [ ] Clicking a brand-new job shows only the job detail card — no prior
      messages from other jobs or the landing screen.
- [ ] Clicking a previously visited job (same session) replays all prior
      messages in order.
- [ ] The AI agent history is restored when returning to a prior job in the
      same session — it does not lose context mid-session.
- [ ] The landing welcome screen is preserved and restored when no job is
      active.
- [ ] `python run_tests.py` passes in full.
- [ ] At least three new tests:
      - `services.delete_job` removes the job and its `UserJobResult` rows.
      - Selecting a new job yields an empty agent history.
      - Returning to a previously visited job restores the cached agent history.
