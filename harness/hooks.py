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
  adds nothing. A message that depends on the turn before it ("never list that
  again") is read together with the assistant's last reply, taken from the
  `transcript_path` the host passes (#244, `harness/transcript.py`); no readable
  transcript means the gate reads the message alone.
- **`session-start`** (`SessionStart`, matcher `compact`): pinned preferences
  after compaction. A model-written summary is where negated preferences get
  lost (#129), so the pins come back from ART word for word, with the job the
  session was working on. So do the hard page rules and the bans on things the
  graph does not hold yet (#299), scoped to that job.

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
from typing import Any, Dict, List, Optional, Sequence, Tuple
from uuid import UUID

from harness import ART_VERSION

log = logging.getLogger(__name__)

MAX_ITEMS = 6
MAX_BULLETS = 3
MAX_TEX_BULLETS = 6


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

def _base_of(user_id: UUID, node: Dict) -> Optional[str]:
    """The node an editor run started from: the nearest ancestor that is not itself an editor edit."""
    from harness import tree

    base, hops = node.get("parent_id"), 0
    while base and hops < 200:
        parent = tree.get_node(user_id, base)
        if parent.get("source") != "editor":
            break
        base, hops = parent.get("parent_id"), hops + 1
    return base


def _describe(user_id: UUID, node: Dict) -> List[str]:
    """What an editor run changed since the version it started from, in a few lines."""
    from harness import tree

    base = _base_of(user_id, node)
    if not base:
        return ["a first version"]
    diff = tree.diff_nodes(user_id, base, node["node_id"])
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
        lines += _describe_tex(diff.get("tex_edits"))
    if diff.get("layout_changed"):
        lines.append("changed the section or bullet order")
    return lines or ["no content change"]


def _describe_tex(edits: Optional[Dict]) -> List[str]:
    """The user's edits to the LaTeX source, a bullet per line (#278)."""
    from harness import texdiff

    if texdiff.is_empty(edits):
        return ["edited the LaTeX source directly (no change to the text)"]
    out = ["edited the LaTeX source directly:"]
    clip = texdiff.brief(edits, MAX_TEX_BULLETS, 120, 6)
    for e in clip["bullets"]:
        where = e["title"] or e["item"]
        if e["change"] == "revised":
            out.append(f'  - {where}: "{e["before"]}" became "{e["after"]}"')
        elif e["change"] == "added":
            out.append(f'  - {where}: added "{e["after"]}"')
        else:
            out.append(f'  - {where}: removed "{e["before"]}"')
    if clip.get("truncated"):
        out.append(f"  - ... and {clip['truncated']} more bullet edits")
    for r in clip["reordered"]:
        out.append(f"  - {r['title'] or r['item']}: bullets reordered")
    for r in clip["unmapped"]:
        out.append(f"  - unmapped change in {r['region']} (not a bullet):\n" +
                   "\n".join("      " + ln for ln in r["diff"].splitlines()))
    return out


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
        described = _describe(user_id, node)
        head = f"- {_job_label(user_id, job_id)} (job {job_id}): HEAD is now {node['node_id']}. "
        if any("\n" in ln or ln.startswith("  ") for ln in described):
            out.append(head + "\n".join(["Changes:"] + ["  " + ln if not ln.startswith("  ") else ln
                                                        for ln in described]))
        else:
            out.append(head + "; ".join(described) + ".")
    out.append("Build on that HEAD (get_head) and keep these edits; do not overwrite them. "
               "A plan that leaves out an edited bullet is refused (discards_editor_edits); "
               "diff_nodes lists every edit.")
    return "\n".join(out)


def _memory_note(user_id: UUID, payload: Dict[str, Any], session_id: str,
                 allow_writes: bool) -> Optional[str]:
    """The memory gate (#202) on the user's message: one line for the host when it may be a standing
    preference (confirm, then `record_preference`), or when ART saved one. Never raises."""
    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        return None
    path = payload.get("transcript_path")
    try:
        from harness import memory
        return memory.observe(user_id, prompt, session_id, write=allow_writes, bounded=True,
                              transcript_path=path if isinstance(path, str) else None).get("note") or None
    except Exception as exc:                          # the gate must never break the prompt
        log.warning("memory gate failed: %s", exc)
        return None


def user_prompt(user_id: UUID, payload: Dict[str, Any], allow_writes: bool = True) -> Optional[str]:
    session_id = str(payload.get("session_id") or "default")
    edits = _editor_edits(user_id, session_id)
    memory = _memory_note(user_id, payload, session_id, allow_writes)
    return "\n".join(part for part in (edits, memory) if part) or None


# ── session-start (after compaction) ─────────────────────────────────────────

def _rule_line(rule: Dict[str, Any]) -> str:
    """A hard page rule as the user said it, with what it limits and the scope it came from."""
    where = rule.get("scope_type") or "global"
    scope = "" if where == "global" else (
        f" [role family {rule.get('scope_value')}]" if where == "role_family" else " [this job]")
    return f"- {rule['text']} ({rule.get('param')}: {rule.get('value')}){scope}"


def session_start(user_id: UUID, payload: Dict[str, Any], allow_writes: bool = True) -> Optional[str]:
    """What a compaction summary loses, word for word (#129, #299): the strength-5 pins, the
    hard page rules (#298) and the bans on things the graph does not hold yet, all scoped to the
    session's job through `preferences_for`. `art_pins` already returns these; a strength-5
    pending ban is in `pins`, so only the softer ones are added below."""
    from harness.tools import art_pins

    if payload.get("source") not in (None, "compact"):
        return None
    state = load_state(str(payload.get("session_id") or "default")) or {}
    held = art_pins(user_id, job_id=state.get("job_id"))
    out = []
    if held["pins"]:
        out.append("ART pinned preferences, verbatim (restored after compaction; honour "
                   "every one):")
        out += [f"- {p['text']}" for p in held["pins"]]
    if held["rules"]:
        out.append("ART hard page rules, verbatim (a plan that loosens one is refused):")
        out += [_rule_line(r) for r in held["rules"]]
    soft_bans = [b for b in held["pending_bans"] if not b["hard"]]
    if soft_bans:
        out.append("ART pending bans, verbatim (not in the knowledge graph yet; follow them):")
        out += [f"- {b['text']}" for b in soft_bans]
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
    except (Exception, SystemExit) as exc:            # never block the prompt
        print(f"art hook: {exc}", file=sys.stderr)
    return 0


# ── installing the hooks (Codex, #203, #290) ──────────────────────────────────
#
# Both hosts read the same JSON, and both load it from the plugin
# (plugin/hooks/hooks.json): Codex 0.131 and later load a plugin's hooks by default
# (`hooks/hooks.json`, or the manifest's `hooks` entry), and ask the user to trust them
# (`/hooks`). So a Codex plugin install already carries ART's hooks. `art hooks codex
# --write` is the fallback for a Codex without plugin hooks (or one used without the
# plugin): it puts them in the user's `$CODEX_HOME/hooks.json` or a repo's
# `.codex/hooks.json`. Running both fires every hook twice, so `--write` checks whether an
# enabled plugin already bundles ART's hooks and then removes its own entries instead.

REPO = "nathansso/agentic_resume_tailoring"
# The one released wheel the plugin runs (#256): the editor is built into it (#255), the URL
# never changes for a version, and uv caches it by URL, so a start needs no remote check.
# Releasing is pushing the tag v<ART_VERSION>; .github/workflows/release.yml builds and
# attaches the wheel. tests/test_plugin.py fails when this, plugin/.mcp.json and ART_VERSION
# disagree.
WHEEL_URL = (f"https://github.com/{REPO}/releases/download/v{ART_VERSION}/"
             f"art_mcp-{ART_VERSION}-py3-none-any.whl")
SOURCE = f"art-mcp[ui] @ {WHEEL_URL}"
_WIRING = (("UserPromptSubmit", None, "user-prompt"), ("SessionStart", "compact", "session-start"))


def hook_command(arg: str, source: str = SOURCE) -> str:
    """The shell line for one hook. It must run unchanged in sh, Git Bash and PowerShell
    (the shells Claude Code uses, and the ones Codex runs hooks in: its session shell, which
    is PowerShell on Windows), so it is plain words, double quotes and `;`. `cmd.exe`, where
    Codex falls back only with no session shell, is not covered (#283). Codex's Windows-only
    `commandWindows` swaps the command text, not the shell, so it does not help (#290).

    `--offline` makes uvx use only what the MCP server's start already cached: a hook
    never waits on the network (an online start re-checks the package indexes and takes
    seconds, or 30 s to time out with no connection), and a cold cache is a silent no-op
    until the server has started once. `; exit 0` covers what `art hook` cannot, uvx
    failing before `art` runs (no uvx, no cache, no wheel), so a hook never blocks the
    message."""
    return f'uvx --offline --from "{source}" art hook {arg}; exit 0'


def hook_config(source: str = SOURCE) -> Dict[str, Any]:
    """The hooks JSON both hosts read, running `art hook` from `source`."""
    hooks: Dict[str, Any] = {}
    for event, matcher, arg in _WIRING:
        group: Dict[str, Any] = {} if matcher is None else {"matcher": matcher}
        group["hooks"] = [{"type": "command", "timeout": 30,
                           "command": hook_command(arg, source)}]
        hooks[event] = [group]
    return {"hooks": hooks}


_UVX = re.compile(r"\buvx\b")
_FROM = re.compile(r"--from\b")
_HOOK_CALL = re.compile(r"\bart hook (?:%s)\b" % "|".join(re.escape(e) for e in EVENTS))
_SERVER_CALL = re.compile(r"(?:^|\s)(?:art-mcp|art mcp)(?:\s|$)")


def is_art_hook(command: Any) -> bool:
    """Whether `command` is one of ART's own hook lines, whichever source an earlier install
    pointed `uvx --from` at: a git source with or without a ref, or a release wheel of any
    version (#283). `art hook <event>` run through `uvx --from` is ART's by its name alone; the
    server entry point `art-mcp` / `art mcp` counts only when the line names this repo, since
    other projects ship an `art-mcp`. Anything else is the user's."""
    if not isinstance(command, str) or not _UVX.search(command) or not _FROM.search(command):
        return False
    if _HOOK_CALL.search(command):
        return True
    return REPO in command and bool(_SERVER_CALL.search(command))


def merge_hooks(existing: Dict[str, Any], new: Dict[str, Any]) -> Tuple[int, List[Tuple[str, str]]]:
    """Make `existing` carry `new`'s hook groups, in place.

    ART's own entries from an earlier install (`is_art_hook`) that are not in `new` are removed
    rather than left beside the new ones, where they re-check a remote on every call and fail
    silently offline (#283); so is a duplicate of a current entry. Entries that are not ART's
    keep their order and are never edited. An entry is "the same" by (group matcher, command).
    Returns (groups added, [(event, command) removed])."""
    added = 0
    removed: List[Tuple[str, str]] = []
    table = existing.setdefault("hooks", {})
    for event, groups in new["hooks"].items():
        wanted = {(g.get("matcher"), h["command"]) for g in groups for h in g["hooks"]}
        kept: set = set()
        survivors: List[Dict[str, Any]] = []
        for group in table.get(event, []):
            handlers = group.get("hooks", [])
            keep = []
            for h in handlers:
                key = (group.get("matcher"), h.get("command"))
                if is_art_hook(h.get("command")):
                    if key not in wanted or key in kept:
                        removed.append((event, h["command"]))
                        continue
                    kept.add(key)
                keep.append(h)
            if len(keep) == len(handlers):
                survivors.append(group)
            elif keep:
                survivors.append({**group, "hooks": keep})   # a mixed group keeps the user's handlers
        if event in table:
            table[event] = survivors
        for group in groups:
            if all((group.get("matcher"), h["command"]) in kept for h in group["hooks"]):
                continue
            table.setdefault(event, []).append(group)
            added += 1
    return added, removed


def codex_home() -> Path:
    import os
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def codex_hooks_path(project: bool = False) -> Path:
    if project:
        return Path.cwd() / ".codex" / "hooks.json"
    return codex_home() / "hooks.json"


# The first stable Codex that loads plugin hooks by default (openai/codex#22549, "Enable plugin
# hooks by default"). 0.126 to 0.130 load them only with `features.plugin_hooks = true`; before
# 0.126 they do not load at all (#290).
PLUGIN_HOOKS_MIN_CODEX = (0, 131, 0)


def codex_version() -> Optional[Tuple[int, int, int]]:
    """The installed Codex's version from `codex --version`, or None when it is not on PATH or
    its output is not a version."""
    import shutil
    import subprocess

    exe = shutil.which("codex")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=15).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", out)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def _has_art_hook(config: Any) -> bool:
    """Whether a hooks JSON object carries any ART hook line."""
    hooks = config.get("hooks") if isinstance(config, dict) else None
    if not isinstance(hooks, dict):
        return False
    for groups in hooks.values():
        for group in groups if isinstance(groups, list) else []:
            for handler in group.get("hooks", []) if isinstance(group, dict) else []:
                if isinstance(handler, dict) and is_art_hook(handler.get("command")):
                    return True
    return False


def plugin_with_art_hooks(home: Path) -> Optional[str]:
    """The id (`name@marketplace`) of an enabled Codex plugin whose installed copy bundles ART's
    hooks, or None. Codex records installs in `<home>/config.toml` (`[plugins."art@art"]
    enabled = true`) and copies each into `<home>/plugins/cache/<marketplace>/<name>/<version>/`;
    the answer is read from the copied `hooks/hooks.json`, so a different plugin that happens to
    be called `art` does not count."""
    import tomllib

    try:
        config = tomllib.loads((home / "config.toml").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    plugins = config.get("plugins")
    for plugin_id, entry in (plugins.items() if isinstance(plugins, dict) else []):
        name, _, market = plugin_id.partition("@")
        if not (name and market and isinstance(entry, dict) and entry.get("enabled") is True):
            continue
        for path in sorted((home / "plugins" / "cache" / market / name).glob("*/hooks/hooks.json")):
            try:
                if _has_art_hook(json.loads(path.read_text(encoding="utf-8"))):
                    return plugin_id
            except (OSError, ValueError):
                continue
    return None


def remove_art_hooks(existing: Dict[str, Any]) -> List[Tuple[str, str]]:
    """Remove every ART hook from `existing`, in place, and return [(event, command) removed].
    Other hooks keep their order and are never edited."""
    removed = merge_hooks(existing, {"hooks": {event: [] for event, _, _ in _WIRING}})[1]
    table = existing.get("hooks")
    for event in {event for event, _ in removed}:
        if isinstance(table, dict) and table.get(event) == []:
            del table[event]                           # an event this left empty
    return removed


def codex_main(argv: Sequence[str]) -> int:
    """`art hooks codex [--project] [--write] [--from SOURCE] [--standalone]`."""
    import argparse

    p = argparse.ArgumentParser(prog="art hooks codex", description=(
        "Print the ART hooks for Codex, or merge them into its hooks.json. A Codex plugin "
        "install already carries them; with it, --write removes ART's own entries from the "
        "file instead, so they do not fire twice."))
    p.add_argument("--project", action="store_true",
                   help="this repo's .codex/hooks.json instead of $CODEX_HOME/hooks.json")
    p.add_argument("--write", action="store_true", help="merge into the file (else print)")
    p.add_argument("--from", dest="source", default=SOURCE,
                   help="where uvx gets ART (default: the pinned release wheel)")
    p.add_argument("--standalone", action="store_true",
                   help="install into the file even though the ART plugin is installed "
                        "(your Codex does not load plugin hooks)")
    args = p.parse_args(list(argv))
    config = hook_config(args.source)

    plugin = None if args.standalone else plugin_with_art_hooks(codex_home())
    version = codex_version() if plugin else None
    if plugin and version is not None and version < PLUGIN_HOOKS_MIN_CODEX:
        print(f"art hooks: plugin {plugin} bundles the hooks, but Codex "
              f"{'.'.join(map(str, version))} does not load plugin hooks by default (0.131 "
              "and later do); installing them in the file.", file=sys.stderr)
        plugin = None

    if not args.write:
        sys.stdout.write(json.dumps(config, indent=2) + "\n")
        if plugin:
            print(f"art hooks: plugin {plugin} already carries these; you do not need to "
                  "install them.", file=sys.stderr)
        return 0
    path = codex_hooks_path(args.project)
    existing: Dict[str, Any] = {}
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf-8") or "{}")
        except ValueError:
            print(f"art hooks: {path} is not valid JSON; not touching it.", file=sys.stderr)
            return 2

    if plugin:
        # The plugin already runs the hooks: remove ART's own entries from the file so each
        # one fires once. Nothing is added, and a file without any is not created or touched.
        removed = remove_art_hooks(existing)
        if not removed:
            print(f"art hooks: plugin {plugin} already carries the ART hooks and {path} has "
                  "none; nothing changed.", file=sys.stderr)
            return 0
        path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
        for event, command in removed:
            print(f"art hooks: removed {event} hook, plugin {plugin} runs it: {command}",
                  file=sys.stderr)
        print(f"art hooks: plugin {plugin} already carries the ART hooks, so {len(removed)} "
              f"duplicate(s) were removed from {path}.", file=sys.stderr)
        return 0

    added, removed = merge_hooks(existing, config)
    if not (added or removed):
        print(f"art hooks: {path} already has the ART hooks; nothing changed.", file=sys.stderr)
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
    for event, command in removed:
        print(f"art hooks: replaced {event} hook from an earlier install: {command}",
              file=sys.stderr)
    print(f"art hooks: {added} hook group(s) added, {len(removed)} replaced, in {path}. In Codex, "
          "run /hooks to review and trust them.", file=sys.stderr)
    return 0
