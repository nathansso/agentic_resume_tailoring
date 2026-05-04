# Task 09 — Data Tab Cleanup

## Context
The main `TabbedContent` in `tui/app.py` has three top-level tabs: Chat, Data,
and Viz. The Viz tab renders ASCII charts via `plotext` — it adds complexity and
dependency weight without meaningful value. The Projects subtab inside Data uses
a `DataTable` with a wide horizontal layout that truncates descriptions and is
hard to read. This task removes Viz entirely and replaces the Projects layout
with a readable multi-line card format.

---

## Required reading — read ALL before writing any code

- `CLAUDE.md` (root)
- `tui/CLAUDE.md`
- `tui/app.py` — entire file; all changes are here
- `tui/services.py` — `get_projects()` return shape (no changes needed)
- `tests/test_tui.py` — TUI test patterns

---

## Task 1 — Remove the Visualization tab entirely

Remove all Viz-related code from `tui/app.py`. Work through the file
systematically and remove each of the following:

**`compose()`**
- Delete the entire `with TabPane("Viz", id="tab-viz"):` block and its `RichLog`.

**Methods**
- Delete `_load_viz()`
- Delete `_run_viz()` (the `@work(thread=True)` method)
- Delete `action_show_viz()`

**CSS**
- Delete the `#viz-content` CSS block.

**Bindings**
- Remove any `Binding` referencing `"f4"` or `"viz"`.
- Remove `F4=Viz` from any footer label string.

**`on_mount()` and `_on_onboarding_done()`**
- Remove `self._load_viz()` calls from both.

**`_handle_chat_input()`**
- Remove the `elif cmd == "viz":` branch.
- Remove `"/viz"` from the unknown-command help text.
- Remove `"/viz"` from the welcome message in the chat scroll (in `compose()`
  or wherever the welcome `Static` is defined).

**Imports**
- Remove `RichLog` from the Textual widget imports if it is no longer referenced
  anywhere else in the file.
- Do not remove `plotext` handling from `tui/services.py` or anywhere outside
  `tui/app.py` — only clean up what is in this file.

---

## Task 2 — Projects subtab: multi-line card layout

The current `subtab-proj` tab contains `DataTable(id="proj-table")` rendered by
`_load_proj_table()`. Replace this with a scrollable card layout.

### `compose()` change
Replace:
```python
with TabPane("Projects", id="subtab-proj"):
    yield DataTable(id="proj-table")
```
With:
```python
with TabPane("Projects", id="subtab-proj"):
    with VerticalScroll(id="proj-scroll"):
        pass
```

### Replace `_load_proj_table()` with `_load_proj_cards()`
Query projects via `services.get_projects(services.get_first_user_id())`.

**Empty state:** Mount a single:
```python
Static(
    "No projects found — type `ingest github <username>` to add GitHub repos",
    classes="system-msg"
)
```
inside `#proj-scroll`.

**Each project card:** Mount one `Static` per project with Rich markup:
```
[bold]{name}[/bold]
[dim]URL:[/dim]   {url}
[dim]Desc:[/dim]  {description, up to 120 characters}
```
Give each card `classes="proj-card"`.

If the description exceeds 120 characters, truncate with `...`. Do not wrap to
a fourth line.

### CSS to add
```css
.proj-card {
    padding: 1 2;
    border-bottom: solid $primary 30%;
}
```

### Update all call sites
Every call to `_load_proj_table()` in the file must be replaced with
`_load_proj_cards()`. Check `_load_data_tables()` and any other callers.

### Import cleanup
`DataTable` is still used by `#exp-table` (Experiences subtab). Do **not**
remove `DataTable` from imports.

---

## Architecture constraints

- Changes are scoped to `tui/app.py` only.
- `tui/services.py` is unchanged — `get_projects()` already returns the right
  shape.
- Do not touch the Skills, Experiences, or Graph subtabs.
- Do not touch the Chat tab.

---

## Satisfaction criteria

- [ ] The TUI launches and shows only Chat and Data tabs — no Viz tab.
- [ ] Typing `/viz` in chat returns the unknown-command message (or is simply
      absent from the known command list).
- [ ] F4 no longer triggers any action.
- [ ] The welcome message in the chat scroll does not mention `/viz`.
- [ ] The Projects subtab shows each project as a multi-line card with name,
      URL, and description on separate lines.
- [ ] Long descriptions are truncated at 120 characters with `...`.
- [ ] The Experiences subtab (`#exp-table`) still renders correctly.
- [ ] The Skills and Graph subtabs are unaffected.
- [ ] `python run_tests.py` passes in full.
- [ ] At least two new tests:
      - `tab-viz` widget does not exist in the mounted app.
      - `#proj-table` does not exist; `#proj-scroll` does exist.
