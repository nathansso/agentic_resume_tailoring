# Task 10 — Persistent Per-Job Chat Memory

## Context
`ChatAgent.history` in `agents/chat.py` is in-memory only. It is lost when the
app restarts. Task 08 adds in-memory per-job caching for the current session.
This task makes that cache durable: every chat message is written to SQLite, and
when a job is selected the agent history and TUI scroll are reconstructed from
the database.

**Prerequisite:** Task 08 must be complete. This task extends `set_active_job()`
and `_show_job_details()` that Task 08 already modified.

---

## Required reading — read ALL before writing any code

- `CLAUDE.md` (root)
- `agents/CLAUDE.md`
- `tui/CLAUDE.md`
- `database/models.py` — add `ChatMessage` here
- `database/db.py` — `init_db` pattern; confirm `SQLModel.metadata.create_all`
- `tui/services.py` — add service functions here
- `agents/chat.py` — full file; understand `self.history`, `set_active_job`,
  `chat()`, and the existing import pattern (lazy imports inside methods to
  avoid circular imports)
- `tui/app.py` — `_show_job_details` and `_post_chat_response` (modified by
  Task 08)
- `tests/conftest.py` — `isolated_engine` fixture
- `tests/test_db.py` — DB model test patterns

---

## Task 1 — Add `ChatMessage` model

Add to `database/models.py`:
```python
class ChatMessage(SQLModel, table=True):
    message_id: UUID = Field(default_factory=uuid4, primary_key=True)
    job_id: Optional[UUID] = Field(default=None, foreign_key="jobdescription.job_id")
    role: str        # "user" | "assistant"
    content: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
```

`job_id = None` represents the landing context (no job selected). The
`foreign_key` relationship allows cascade queries but does not require a
SQLModel `Relationship` field unless needed elsewhere.

`init_db()` already calls `SQLModel.metadata.create_all(engine)`. Verify this
is the case — the new table will be created automatically on next startup. No
manual migration is needed. Existing databases must load without error.

---

## Task 2 — Service functions

Add to `tui/services.py`:

```python
def save_chat_message(job_id: Optional[str], role: str, content: str) -> None:
    """Persist one message to the ChatMessage table. Never raises."""

def load_chat_history(job_id: Optional[str], limit: int = 20) -> list[dict]:
    """Return the last `limit` messages for this job as a list of
    {"role": str, "content": str} dicts, ordered oldest-first.
    Returns [] if none found or on error."""
```

Implementation notes:
- `save_chat_message`: convert `job_id` string → `UUID` if not None. Use
  `with Session(engine)`. Wrap entire body in try/except, log a warning on
  failure, never raise.
- `load_chat_history`: query `ChatMessage` where `job_id` matches, order by
  `created_at` ascending, limit to last `limit` rows. Convert each row to
  `{"role": row.role, "content": row.content}`. Wrap in try/except, return `[]`
  on error.

---

## Task 3 — Persist messages in `ChatAgent`

In `agents/chat.py`, the `chat()` method appends to `self.history`. After each
append, also call `services.save_chat_message`.

**Import:** `tui.services` must be imported lazily inside the method body to
avoid circular imports. Check the existing import pattern in `chat.py` — other
methods import from `tui` or `database` inside function bodies. Follow the same
pattern.

**User message** — after:
```python
self.history.append({"role": "user", "content": user_message})
```
Add:
```python
try:
    from tui import services as _svc
    _svc.save_chat_message(self._active_job_id, "user", user_message)
except Exception:
    pass
```

**Assistant message** — after the final `self.history.append(...)` for the
assistant response, add the same pattern with `role="assistant"` and the
response text.

A DB write failure must **never** surface as a chat error to the user.

---

## Task 4 — Restore history on job switch

Extend `ChatAgent.set_active_job(job_id)` (already modified by Task 08):

After setting `self._active_job_id = job_id`, load history from DB:
```python
try:
    from tui import services as _svc
    db_history = _svc.load_chat_history(job_id, limit=20)
except Exception:
    db_history = []

if db_history:
    # DB history takes precedence over in-memory cache on first load
    self.history = db_history
# else: self.history was already set from _job_histories in Task 08
```

This means:
- Within a session: in-memory history from `_job_histories` is used (fast,
  no DB read after the first load).
- On app restart: `_job_histories` is empty, so DB is the source.
- History is capped at 20 messages for LLM context.

The existing LLM call in `chat()` already slices `self.history[-12:]` before
sending to the model. Do not change that slice.

---

## Task 5 — Reconstruct TUI scroll from DB on app restart

In `ArtApp._show_job_details(job_uuid)` (already modified by Task 08):

The Task 08 logic checks `if job_uuid in self._job_chat_cache`. On a fresh app
start, this cache is empty. Extend the "not in cache" branch to also check DB:

```python
if job_uuid not in self._job_chat_cache:
    db_history = services.load_chat_history(job_uuid)
    if db_history:
        # Reconstruct scroll and cache from DB
        cached = []
        for msg in db_history:
            if msg["role"] == "user":
                css, text = "user-msg", f"You: {msg['content']}"
            else:
                css, text = "bot-msg", msg["content"]
            scroll.mount(Static(text, classes=css))
            cached.append((css, text))
        self._job_chat_cache[job_uuid] = cached
        # Then mount the job detail card as usual
    else:
        # Truly new job — mount only the detail card (existing Task 08 behavior)
        ...
```

This means after an app restart, selecting a job with history shows all prior
messages before the job detail card.

---

## Architecture constraints

- `ChatAgent` must import `tui.services` lazily (inside method bodies) to
  avoid circular imports. This is the existing convention in `chat.py`.
- `save_chat_message` and `load_chat_history` must be non-blocking best-effort.
  A DB failure must never crash the chat or prevent a response.
- Do not change `ChatAgent.chat()`'s return type or public signature.
- History cap: load at most 20 messages from DB. The LLM call already slices
  to 12 messages — do not change that.
- `job_id = None` is valid and represents the landing context. Both service
  functions must handle it correctly.

---

## Satisfaction criteria

- [ ] Sending a message to a job writes a `ChatMessage` row to SQLite.
- [ ] Both user and assistant turns are persisted.
- [ ] After app restart, selecting a job with prior history shows those messages
      in the scroll in the correct order.
- [ ] `ChatAgent.history` is populated from DB when a job is selected after a
      restart — the AI does not lose context.
- [ ] `job_id = None` (landing context) messages are stored and retrievable.
- [ ] A DB write failure in `save_chat_message` does not affect the chat
      response or raise any exception visible to the user.
- [ ] Existing local databases (no `chatmessage` table) auto-migrate on the
      next `init_db()` call without error.
- [ ] `python run_tests.py` passes in full.
- [ ] At least three new tests using `isolated_engine`:
      - `save_chat_message` + `load_chat_history` round-trip for a job.
      - `load_chat_history` with `job_id=None` returns landing messages.
      - `load_chat_history` with `limit=2` returns only the 2 most recent
        messages, oldest-first.
