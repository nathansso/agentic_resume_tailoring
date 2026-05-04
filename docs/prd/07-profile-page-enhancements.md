# Task 07 — Profile Page Enhancements

## Context
`ProfileScreen` in `tui/screens/profile.py` is a modal overlay pushed via
`push_screen()` from `tui/app.py`. It currently has fields for name, GitHub
username, and LinkedIn URL. This task adds a back button, a hidden GitHub token
field, and a resume management section.

This project will be open-source. Users download it and run it locally on their
own machine. Token storage must be safe by default without requiring any extra
setup from the user.

---

## Required reading — read ALL before writing any code

- `CLAUDE.md` (root)
- `tui/CLAUDE.md`
- `tui/screens/profile.py` — screen being modified
- `tui/services.py` — all DB and file access goes through here
- `database/models.py` — User model lives here
- `database/user_utils.py` — active profile lookup pattern
- `tui/app.py` lines 420–426 — how ProfileScreen is pushed and dismissed
- `.env` — confirm `GITHUB_TOKEN` is not already present
- `.gitignore` — **verify `.env` is listed before writing any token logic**
- `tests/test_services.py` — where new service tests go
- `tests/conftest.py` — `isolated_engine` fixture

---

## Task 1 — Rename "Close" → "Back to Chat"

In `ProfileScreen.compose()`, change the label of `close-profile-btn` from
`"Close"` to `"Back to Chat"`. The dismiss behavior is unchanged
(`self.dismiss(None)`). No routing or callback changes needed.

---

## Task 2 — GitHub read token field

### UI
Add below the GitHub username field in `compose()`:
```
Label("GitHub Token", classes="field-label")
Input(password=True, placeholder="ghp_... (optional)", id="profile-token-input")
```
`password=True` makes Textual dot out the value automatically.

On mount, call `services.get_github_token()`. If a token is stored, set the
input value to a fixed mask string `"••••••••"` (never the real token). If no
token is stored, leave the field empty.

On save (existing `_save()` flow), read the input value. If it equals the mask
`"••••••••"`, skip writing (user did not change it). If it is a non-empty string
that is not the mask, call `services.save_github_token(value)`. If it is empty,
call `services.save_github_token("")` to clear the token.

### Storage
Tokens are stored in `.env` using `python-dotenv`. This is the correct approach
for a local-first open-source tool: `.env` is gitignored so it never enters
version control, and each user manages their own `.env` on their own machine.

**Before implementing**, verify `.env` is in `.gitignore`. If it is not, add it.

### Service functions to add in `tui/services.py`

```python
def get_github_token() -> str:
    """Read GITHUB_TOKEN from .env. Returns '' if not set."""

def save_github_token(token: str) -> None:
    """Write GITHUB_TOKEN to .env via dotenv.set_key().
    If token is '', remove the key. Never log the value."""
```

Use `python-dotenv`'s `dotenv_values()` for reads and `set_key()` / `unset_key()`
for writes. The `.env` file path should be resolved relative to the project root
(use `pathlib.Path(__file__).parent.parent / ".env"`).

The token must **never** be logged, printed, stored in SQLite, or appear in any
status message. Add a comment `# token value intentionally not logged` on any
line near the write call.

---

## Task 3 — Base resume management

### Model change
Add to the `User` model in `database/models.py`:
```python
resume_path: Optional[str] = None
```
This must be migration-safe: existing rows default to `None` without any manual
migration step. Confirm that `init_db()` uses `SQLModel.metadata.create_all()`
with `checkfirst=True` or equivalent so new columns do not break existing DBs.

### UI — resume section
Add below the stats line in `ProfileScreen`, inside the form panel:

1. A `Label` showing `"Base Resume: {filename}"` where `{filename}` is
   `Path(resume_path).name`, or `"Base Resume: none"` if no path is stored.
   Widget id: `"resume-label"`.

2. A `Button("Delete Resume", id="delete-resume-btn")` — disabled when
   `resume_path` is None.

3. A `Button("Upload New Resume", id="upload-resume-btn")`.

4. A hidden `Vertical(id="resume-upload-area")` (CSS `display: none` by default)
   containing:
   - `Input(placeholder="Absolute path to resume file", id="resume-path-input")`
   - `Button("Confirm Upload", variant="primary", id="confirm-upload-btn")`
   - `Button("Cancel", id="cancel-upload-btn")`

### Interactions

**"Upload New Resume"** — show `resume-upload-area`, focus `resume-path-input`.

**"Confirm Upload"**:
1. Show a status message `"Ingesting resume..."` in `#profile-status`.
2. Call `services.ingest_resume_file(path)` inside `@work(thread=True)`.
3. On completion, call `services.update_resume_path(user_id, path)`.
4. Update `#resume-label` to show the new filename.
5. Hide `resume-upload-area`.
6. Show `"Resume ingested."` in `#profile-status`.

**"Delete Resume"**:
1. Show a confirmation prompt in `#profile-status`:
   `"Delete resume path? Skills and experience data will be kept. [Confirm] [Cancel]"`
   Replace the status label with two inline buttons: `confirm-delete-resume-btn`
   and `cancel-delete-resume-btn`.
2. On confirm: call `services.delete_resume(user_id)`. Update `#resume-label` to
   `"Base Resume: none"`. Disable the delete button. Restore the status area.
3. On cancel: restore the status area with no change.

Deleting the resume path does **not** remove the physical file from disk. It does
**not** purge skills, experiences, or projects from the database. It only clears
the `resume_path` field on the User row.

### When a new resume is uploaded
Call the existing `services.ingest_resume_file(path)` which parses the file and
calls `ResumeParserAgent().parse_and_save()`. This overwrites and adds to
existing skills/experiences/projects — the ingestion pipeline already handles
deduplication. No special merge logic is needed here.

### Service functions to add in `tui/services.py`

```python
def get_resume_path(user_id: UUID) -> Optional[str]:
    """Return resume_path for the given user, or None."""

def update_resume_path(user_id: UUID, path: str) -> None:
    """Set resume_path on the User row."""

def delete_resume(user_id: UUID) -> None:
    """Clear resume_path on the User row. Does not delete the file or any
    ingested data."""
```

All DB access goes through `tui/services.py`. No direct DB calls in
`ProfileScreen`.

---

## Architecture constraints

- Use `@work(thread=True)` for `ingest_resume_file` — it is slow.
- All DB reads/writes go through `tui/services.py`.
- Token value must never appear in logs, status widgets, or console output.
- Do not modify `tui/app.py` for this task except to verify the push/dismiss
  pattern is unchanged.

---

## Satisfaction criteria

- [ ] "Back to Chat" button dismisses the modal and returns to the main chat.
- [ ] GitHub token field shows a mask (`"••••••••"`) when a token is already
      stored; shows empty when none is stored.
- [ ] Saving with the mask unchanged does not overwrite the token.
- [ ] Saving a new token value writes it to `.env`; the field shows the mask on
      next open.
- [ ] Saving an empty token value removes `GITHUB_TOKEN` from `.env`.
- [ ] Token is never visible in any widget, log line, or status message.
- [ ] `.env` is confirmed gitignored before any token write logic exists.
- [ ] "Upload New Resume" shows the upload area and `"Ingesting resume..."` while
      processing.
- [ ] After upload, the resume label shows the new filename.
- [ ] "Delete Resume" shows a confirmation step before clearing the path.
- [ ] After delete, skills/experiences/projects rows in the DB are untouched.
- [ ] Existing local databases (no `resume_path` column) load without error.
- [ ] `python run_tests.py` passes in full.
- [ ] At least two new tests:
      - `get_github_token` / `save_github_token` round-trip (mock `.env` file).
      - `update_resume_path` / `delete_resume` service functions using
        `isolated_engine`.
