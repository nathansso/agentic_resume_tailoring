"""Claude Code hooks for the ART plugin (issue #201): `art hook <event>`.

Three things a host loses without them (docs/harness.md § 10, § 13):

- **`user-prompt`** (`UserPromptSubmit`): the user's edits in `art ui`. Every
  `.tex` save and drag commits an `editor` node (#196, #204); before each
  message this hook tells the host which jobs the user changed since the last
  message, and what changed, so the host builds on that HEAD instead of
  overwriting it. The first prompt of a session only sets the cursor.
- **The memory gate** (#202), also on `user-prompt`: `observe` runs on the
  message itself. A message that may state a standing preference adds one line
  asking the host to confirm it with the user and call `record_preference`; a
  clear low-stakes one ART saves itself, and the line says so. Anything else
  adds nothing.
- **`session-start`** (`SessionStart`, matcher `compact`): pinned preferences
  after compaction. A model-written summary is where negated preferences get
  lost (#129), so the pins come back from ART word for word, with the job the
  session was working on.

A hook must never break the user's prompt: any failure prints nothing and exits
0. Output is Claude Code's JSON (`hookSpecificOutput.additionalContext`), or
nothing when there is nothing to say. Per-session state (the event cursor and
the current job) lives in `$ART_DATA_DIR/sessions/<session_id>.json`.

Model-free by rule (`tests/test_harness_boundary.py`).
"""

from __future__ import annotations

import json
import logging
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence
from uuid import UUID

log = logging.getLogger(__name__)

MAX_ITEMS = 6
MAX_BULLETS = 3


# ── per-session state ────────────────────────────────────────────────────────

def _state_path(session_id: str) -> Path:
    from config import APP_DATA_DIR
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id or "default")[:120]
    return Path(APP_DATA_DIR) / "sessions" / f"{safe}.json"


def load_state(session_id: str) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(_state_path(session_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save_state(session_id: str, state: Dict[str, Any]) -> None:
    path = _state_path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state), encoding="utf-8")


def _latest_event(user_id: UUID) -> int:
    from sqlalchemy import func
    from sqlmodel import Session, select

    import database.db as _db
    from database.models import TreeEvent

    with Session(_db.engine) as session:
        return int(session.exec(select(func.max(TreeEvent.event_id))
                                .where(TreeEvent.user_id == user_id)).one() or 0)


def _job_label(user_id: UUID, job_id: str) -> str:
    from sqlmodel import Session

    import database.db as _db
    from database.models import JobDescription

    with Session(_db.engine) as session:
        job = session.get(JobDescription, UUID(str(job_id)))
    return f"{job.title} @ {job.company}" if job else "a job"


# ── user-prompt ──────────────────────────────────────────────────────────────

def _describe(user_id: UUID, node: Dict) -> List[str]:
    """What an editor node changed against its parent, in a few lines."""
    from harness import tree

    if not node.get("parent_id"):
        return ["a first version"]
    diff = tree.diff_nodes(user_id, node["parent_id"], node["node_id"])
    lines = []
    for item in diff.get("items", [])[:MAX_ITEMS]:
        parts = [f"{item['change']} {item['title'] or item['key']}"]
        if item.get("reordered"):
            parts.append("bullets reordered")
        for b in item.get("bullets_removed", [])[:MAX_BULLETS]:
            parts.append(f'removed "{b}"')
        for b in item.get("bullets_added", [])[:MAX_BULLETS]:
            parts.append(f'added "{b}"')
        lines.append("; ".join(parts))
    if diff.get("skills_added") or diff.get("skills_removed"):
        lines.append(f"skills +{diff.get('skills_added', [])} -{diff.get('skills_removed', [])}")
    if diff.get("tex_changed"):
        lines.append("edited the LaTeX source directly")
    if diff.get("layout_changed"):
        lines.append("changed the section or bullet order")
    return lines or ["no content change"]


def _editor_edits(user_id: UUID, session_id: str) -> Optional[str]:
    """The user's editor edits since the last message, or None. The first prompt of a session only
    sets the cursor."""
    from harness import tree

    state = load_state(session_id)
    if state is None:
        save_state(session_id, {"cursor": _latest_event(user_id), "job_id": None})
        return None

    events = tree.events_since(user_id, state.get("cursor") or 0)
    if not events:
        return None
    state["cursor"] = events[-1]["event_id"]
    state["job_id"] = events[-1]["job_id"]

    edits: Dict[str, Dict] = {}                     # job -> its last editor commit
    for ev in events:
        if ev["kind"] != "commit":
            continue
        node = tree.get_node(user_id, ev["node_id"])
        if node.get("source") == "editor":
            edits[ev["job_id"]] = node
    save_state(session_id, state)
    if not edits:
        return None

    out = ["ART: since your last message the user edited their resume in the editor."]
    for job_id, node in edits.items():
        out.append(f"- {_job_label(user_id, job_id)} (job {job_id}): HEAD is now "
                   f"{node['node_id']}. " + "; ".join(_describe(user_id, node)) + ".")
    out.append("Build on that HEAD (get_head) and keep these edits; do not overwrite them.")
    return "\n".join(out)


def _memory_note(user_id: UUID, payload: Dict[str, Any], session_id: str,
                 allow_writes: bool) -> Optional[str]:
    """The memory gate (#202) on the user's message: one line for the host when it may be a standing
    preference (confirm, then `record_preference`), or when ART saved one. Never raises."""
    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        return None
    try:
        from harness import memory
        return memory.observe(user_id, prompt, session_id, write=allow_writes,
                              bounded=True).get("note") or None
    except Exception as exc:                          # the gate must never break the prompt
        log.warning("memory gate failed: %s", exc)
        return None


def user_prompt(user_id: UUID, payload: Dict[str, Any], allow_writes: bool = True) -> Optional[str]:
    session_id = str(payload.get("session_id") or "default")
    edits = _editor_edits(user_id, session_id)
    memory = _memory_note(user_id, payload, session_id, allow_writes)
    return "\n".join(part for part in (edits, memory) if part) or None


# ── session-start (after compaction) ─────────────────────────────────────────

def session_start(user_id: UUID, payload: Dict[str, Any], allow_writes: bool = True) -> Optional[str]:
    from harness.tools import art_pins

    if payload.get("source") not in (None, "compact"):
        return None
    state = load_state(str(payload.get("session_id") or "default")) or {}
    pins = art_pins(user_id, job_id=state.get("job_id"))["pins"]
    out = []
    if pins:
        out.append("ART pinned preferences, verbatim (restored after compaction; honour "
                   "every one):")
        out += [f"- {p['text']}" for p in pins]
    if state.get("job_id"):
        out.append(f"You were tailoring {_job_label(user_id, state['job_id'])} "
                   f"(job {state['job_id']}). Call get_head before building on it.")
    return "\n".join(out) or None


# ── entry point ──────────────────────────────────────────────────────────────

EVENTS = {"user-prompt": ("UserPromptSubmit", user_prompt),
          "session-start": ("SessionStart", session_start)}


def run(event: str, payload: Dict[str, Any], user_id: Optional[UUID],
        allow_writes: bool = True) -> Optional[Dict]:
    """The hook's JSON reply, or None to say nothing. `allow_writes` False (a read-only store)
    keeps the memory gate from writing."""
    name, fn = EVENTS[event]
    if user_id is None:
        return None
    text = fn(user_id, payload, allow_writes=allow_writes)
    if not text:
        return None
    return {"hookSpecificOutput": {"hookEventName": name, "additionalContext": text}}


def main(argv: Sequence[str]) -> int:
    """`art hook <event>`: read the hook's JSON from stdin, answer on stdout.
    Always exits 0: a hook failure must not block the user's message."""
    try:
        event = argv[0] if argv else ""
        if event not in EVENTS:
            print(f"art hook: unknown event {event!r}; one of {', '.join(EVENTS)}",
                  file=sys.stderr)
            return 0
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        from harness.runtime import bootstrap
        user_id, writes = bootstrap(None, None)
        reply = run(event, payload, user_id, allow_writes=writes)
        if reply:
            sys.stdout.write(json.dumps(reply) + "\n")
    except Exception as exc:                          # never block the prompt
        print(f"art hook: {exc}", file=sys.stderr)
    return 0


# ── installing the hooks (Codex, #203) ───────────────────────────────────────
#
# Claude Code loads these hooks from the plugin (plugin/hooks/hooks.json). Codex
# cannot: its plugins no longer carry hooks (`plugin_hooks` was removed), so they
# go in the user's `$CODEX_HOME/hooks.json` or a repo's `.codex/hooks.json`, and
# Codex asks the user to trust them (`/hooks`). Both hosts read the same JSON.

SOURCE = "git+https://github.com/nathansso/agentic_resume_tailoring"
_WIRING = (("UserPromptSubmit", None, "user-prompt"), ("SessionStart", "compact", "session-start"))


def hook_config(source: str = SOURCE) -> Dict[str, Any]:
    """The hooks JSON both hosts read, running `art hook` from `source`."""
    hooks: Dict[str, Any] = {}
    for event, matcher, arg in _WIRING:
        group: Dict[str, Any] = {} if matcher is None else {"matcher": matcher}
        group["hooks"] = [{"type": "command", "timeout": 30,
                           "command": f"uvx --from {source} art hook {arg}"}]
        hooks[event] = [group]
    return {"hooks": hooks}


def merge_hooks(existing: Dict[str, Any], new: Dict[str, Any]) -> int:
    """Add `new`'s hook groups to `existing` in place, skipping any command
    already configured. Never removes or edits the user's own hooks. Returns
    how many groups were added."""
    added = 0
    table = existing.setdefault("hooks", {})
    for event, groups in new["hooks"].items():
        have = {h.get("command") for g in table.get(event, []) for h in g.get("hooks", [])}
        for group in groups:
            if all(h["command"] in have for h in group["hooks"]):
                continue
            table.setdefault(event, []).append(group)
            added += 1
    return added


def codex_hooks_path(project: bool = False) -> Path:
    import os
    if project:
        return Path.cwd() / ".codex" / "hooks.json"
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "hooks.json"


def codex_main(argv: Sequence[str]) -> int:
    """`art hooks codex [--project] [--write] [--from SOURCE]`."""
    import argparse

    p = argparse.ArgumentParser(prog="art hooks codex", description=(
        "Print the ART hooks for Codex, or merge them into its hooks.json."))
    p.add_argument("--project", action="store_true",
                   help="this repo's .codex/hooks.json instead of $CODEX_HOME/hooks.json")
    p.add_argument("--write", action="store_true", help="merge into the file (else print)")
    p.add_argument("--from", dest="source", default=SOURCE,
                   help="where uvx gets ART (default: the GitHub repo)")
    args = p.parse_args(list(argv))
    config = hook_config(args.source)
    if not args.write:
        sys.stdout.write(json.dumps(config, indent=2) + "\n")
        return 0
    path = codex_hooks_path(args.project)
    existing: Dict[str, Any] = {}
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf-8") or "{}")
        except ValueError:
            print(f"art hooks: {path} is not valid JSON; not touching it.", file=sys.stderr)
            return 2
    added = merge_hooks(existing, config)
    if added:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
    print(f"art hooks: {added} hook group(s) added to {path}. In Codex, run /hooks to review "
          "and trust them.", file=sys.stderr)
    return 0
