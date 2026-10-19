"""The quoted-approval gate (issue #393): a plan commits only with the user's own approving words.

The tailoring plan is the one consent point (docs/harness.md § 2.1). Showing it and waiting
used to be a skill instruction; this makes it structural:

- **The plan token.** An `execute_plan` (or `patch_plan`) dry run returns `plan_token`, a
  fingerprint of the exact normalised program: job, parent, nodes, skills, section order,
  finalize and `discard_editor_edits`, hashed as canonical JSON after pydantic validation, so
  key order, JSON whitespace and defaults left out do not change it. `host` is left out: who
  is driving is not part of what the user approves. The dry run is recorded here as *shown*.
- **The messages.** The `UserPromptSubmit` hook (`harness/hooks.py`) calls `hook_seen` on
  every prompt and `note_message` with the user's text. A message is kept only while some
  shown plan of that user is waiting for approval, and only until that plan commits or
  expires (`TTL`, 24 h).
- **The commit check** (`check`). A commit through the tool contract must carry the token
  of the program it commits and `approval_quote`, the user's reply; compared after
  normalising case, whitespace and the punctuation, quotes and emoji around it (`core`), the
  quote must be the **whole** of a message recorded after that plan was shown ("Looks good,
  go!" approves), or a contiguous span of at least `MIN_SPAN_CHARS` (20) characters of one,
  so a long reply that mixes approval with other requests still counts. A short span inside a
  longer message ("ok" out of "ok, but change the second bullet") is refused: the agent must
  pass the whole reply. Otherwise `approval_required`, saying what is missing. "Just do it,
  don't ask" is itself a quote and is checked the same way.
- **Without hooks** (a manual MCP registration, or a Codex without its hooks), nothing could
  ever be quoted, so a store whose hook has never run (`hooks_present`: no heartbeat file)
  accepts the token alone and the reply says `approval: "token_only"`.
- **Sessions.** An MCP tool call does not carry the host's session id (Claude Code passes it
  to hooks, not to MCP servers), so a plan is tied to its job and the time it was shown, and
  any session's messages after that count. A host that knows its session id may pass it on
  the dry run (`session_id`); then only that session's messages count.
- **A job's first, empty plan** (`parent` null, no nodes, skills, section order or finalize)
  commits the track baseline copy or the whole graph, ART's own starting content, so the agent
  can read it before planning (the `artie-tailor` skill). It changes nothing the user chose and
  is not gated (`is_bootstrap`).

Where it lives: `$ART_DATA_DIR/approvals/` (`plans/<token>.json`, `messages/<n>.json`, and
the `hooks_seen` heartbeat), beside the hook's per-session state. Never in the database, so
never in an export (`harness/export_import.py` bundles tables and `applications/` only), and
the message text is never logged. The decision log, the committed node's provenance, records
the approval: its mode, the token and the quote.

Model-free and network-free by rule (`tests/test_harness_boundary.py`).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import unicodedata
import uuid
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

log = logging.getLogger(__name__)

TTL_SECONDS = 24 * 3600
# A quote that is not a whole message must be a span of one at least this long (#393 review):
# "looks good, and save it to Downloads" may be quoted in part, "ok" from "ok, but ..." not.
MIN_SPAN_CHARS = 20
# A message longer than this is pasted material; only its first part is kept.
MAX_MESSAGE_CHARS = 20_000

MODES = ("quoted", "token_only")

_NO_TOKEN = (
    "approval_required: no plan_token. Run this plan with dry_run true, show the user "
    "plan_summary.text and end your turn; when they approve, commit with the plan_token the dry "
    "run returned and approval_quote set to their approving words, exactly as they wrote them.")
_UNKNOWN = (
    "approval_required: no dry run of this plan was shown: plan_token {token} is unknown or "
    "expired (a plan expires 24 h after its dry run). Run it with dry_run true, show the user "
    "plan_summary.text, and wait for their approval.")
_CHANGED = (
    "approval_required: the program changed since the dry run that returned plan_token {token}. "
    "The user approved that plan, not this one: run this one with dry_run true, show them the "
    "new plan_summary.text, and ask again.")
_NO_QUOTE = (
    "approval_required: approval_quote is missing. Set it to the user's whole reply approving "
    "the plan, copied exactly from their message.")
_PARTIAL = (
    "approval_required: approval_quote is only part of the user's message. A quote shorter "
    "than {n} characters must be their whole reply, so that \"ok\" cannot be taken from \"ok, "
    "but change the second bullet\". Pass the user's whole reply as approval_quote; if it asks "
    "for changes, make them, dry-run the plan again and show it.")
# When this data directory has hooks but the plan got no message at all since it was shown, the
# host making the call may be one that runs without ART's hooks.
_NO_HOOK_HINT = (
    " No message at all was recorded since this plan was shown. If the user did reply, this "
    "host may run without ART's prompt hook: for Codex run `artie hooks codex --write` (or "
    "install the artie plugin), for Claude Code update the artie plugin, then show the plan "
    "again.")
_NOT_FOUND = (
    "approval_required: approval_quote was not found in the user's messages since the plan was "
    "shown (only messages sent after this plan's dry run count). Show the user "
    "plan_summary.text, end your turn, and quote their reply word for word. Do not commit "
    "without their approval.")


# ── the token ────────────────────────────────────────────────────────────────

def token(prog: Dict[str, Any]) -> str:
    """The fingerprint of a validated program (`Program.model_dump(mode="json")`)."""
    from harness.program import canonical

    body = {k: v for k, v in prog.items() if k != "host"}
    return "plan_" + hashlib.sha256(canonical(body).encode("utf-8")).hexdigest()[:24]


def is_bootstrap(prog: Dict[str, Any]) -> bool:
    """A job's first, empty plan: it commits ART's starting content and nothing the user chose."""
    finalize = prog.get("finalize") or {}
    return (not prog.get("parent") and not prog.get("nodes") and prog.get("skills") is None
            and prog.get("section_order") is None
            and all(v in (None, [], False) for v in finalize.values()))


# ── normalising and matching ─────────────────────────────────────────────────

_QUOTES = str.maketrans({"‘": "'", "’": "'", "‛": "'", "′": "'",
                         "“": '"', "”": '"', "‟": '"', "″": '"'})


def normalise(text: str) -> str:
    """Case- and whitespace-insensitive: NFKC, curly quotes made straight, casefolded, every run
    of whitespace one space, trimmed."""
    text = unicodedata.normalize("NFKC", text or "").translate(_QUOTES).casefold()
    return re.sub(r"\s+", " ", text).strip()


_EDGE = re.compile(r"^[\W_]+|[\W_]+$")


def core(text: str) -> str:
    """The normalised text without the punctuation, quotation marks, symbols and emoji around
    it: what a quote and a message are compared on ("“Looks good, go!” 👍" is
    "looks good, go")."""
    return _EDGE.sub("", normalise(text))


def match(quote: str, message: str) -> Optional[str]:
    """How a quote stands to one message: `whole` (it is the whole message), `span` (a
    contiguous span of at least `MIN_SPAN_CHARS` characters of it, on word boundaries),
    `short` (a shorter span: refused), or None."""
    q, m = core(quote), core(message)
    if not q:
        return None
    if q == m:
        return "whole"
    if re.search(r"(?<!\w)" + re.escape(q) + r"(?!\w)", m) is None:
        return None
    return "span" if len(q) >= MIN_SPAN_CHARS else "short"


def quote_in(quote: str, message: str) -> bool:
    """Whether the quote approves on this message: the whole of it, or a long enough span."""
    return match(quote, message) in ("whole", "span")


# ── the local store ──────────────────────────────────────────────────────────

def _root() -> Path:
    from config import APP_DATA_DIR
    return Path(APP_DATA_DIR) / "approvals"


def _now() -> int:
    return time.time_ns()


def _expired(at_ns: int, now: int) -> bool:
    return now - int(at_ns) > TTL_SECONDS * 1_000_000_000


def _write(path: Path, doc: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f"{path.name}.{uuid.uuid4().hex[:8]}.tmp"
    tmp.write_text(json.dumps(doc), encoding="utf-8")
    tmp.replace(path)


def _read(path: Path) -> Optional[Dict[str, Any]]:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else None
    except (OSError, ValueError):
        return None


def _remove(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def _plan_path(tok: str) -> Path:
    return _root() / "plans" / f"{re.sub(r'[^A-Za-z0-9_]', '_', tok)[:64]}.json"


def _plans() -> Iterator[Tuple[Path, Dict[str, Any]]]:
    for path in sorted((_root() / "plans").glob("*.json")):
        doc = _read(path)
        if doc is not None:
            yield path, doc


def _messages() -> Iterator[Tuple[Path, Dict[str, Any]]]:
    for path in sorted((_root() / "messages").glob("*.json")):
        doc = _read(path)
        if doc is not None:
            yield path, doc


def _stamp(now: int) -> int:
    """A time after every plan and message already recorded. A record made now happened after
    those, but the wall clock can tie with them (Windows ticks in steps of up to 16 ms), and a
    message must order strictly after the dry run it answers."""
    latest = 0
    for _, plan in _plans():
        latest = max(latest, int(plan.get("shown_ns") or 0), int(plan.get("last_shown_ns") or 0))
    for _, msg in _messages():
        latest = max(latest, int(msg.get("at_ns") or 0))
    return max(now, latest + 1)


def _live_plan(doc: Dict[str, Any], now: int) -> bool:
    return not _expired(doc.get("last_shown_ns") or 0, now)


def _waits_on(plan: Dict[str, Any], user_id: str, session_id: Optional[str]) -> bool:
    """Whether a message from `session_id` can approve `plan`."""
    sessions = plan.get("sessions")
    return plan.get("user_id") == user_id and (sessions is None or session_id in sessions)


def prune(now: Optional[int] = None) -> None:
    """Drop expired plans, and every message no live plan could still be approved by. Never
    raises."""
    now = _now() if now is None else now
    try:
        live = []
        for path, plan in list(_plans()):
            if _live_plan(plan, now):
                live.append(plan)
            else:
                _remove(path)
        for path, msg in list(_messages()):
            needed = not _expired(msg.get("at_ns") or 0, now) and any(
                _waits_on(p, msg.get("user_id"), msg.get("session_id"))
                and int(msg.get("at_ns") or 0) > int(p.get("shown_ns") or 0) for p in live)
            if not needed:
                _remove(path)
    except OSError as exc:
        log.warning("approval store: prune failed (%s)", type(exc).__name__)


# ── the dry run ──────────────────────────────────────────────────────────────

def record_shown(user_id, job_id: str, tok: str, session_id: Optional[str] = None) -> None:
    """A dry run returned `tok`: the plan is shown and waits for approval. A plan shown again
    keeps the first time it was shown (a message after either showing approves the same
    program) and its expiry restarts. Never raises: the dry run already succeeded."""
    now = _now()
    try:
        path = _plan_path(tok)
        held = _read(path)
        at = _stamp(now)
        if held is not None and held.get("user_id") == str(user_id) and _live_plan(held, now):
            if held.get("sessions") is not None:
                held["sessions"] = (None if session_id is None
                                    else sorted(set(held["sessions"]) | {session_id}))
            held["last_shown_ns"] = at
            _write(path, held)
            return
        _write(path, {"token": tok, "user_id": str(user_id), "job_id": str(job_id),
                      "shown_ns": at, "last_shown_ns": at,
                      "sessions": None if session_id is None else [session_id]})
    except OSError as exc:
        log.warning("approval store: could not record a dry run (%s)", type(exc).__name__)


# ── the hook ─────────────────────────────────────────────────────────────────

def hook_seen() -> None:
    """The prompt hook ran: the host has hooks, so a commit must be quoted, not token-only."""
    path = _root() / "hooks_seen"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(_now()), encoding="utf-8")


def hooks_present() -> bool:
    return (_root() / "hooks_seen").is_file()


def note_message(user_id, session_id: Optional[str], text: Any) -> bool:
    """Keep one user message while a shown plan waits on it. Returns whether it was kept."""
    if not isinstance(text, str) or not text.strip():
        return False
    now = _now()
    prune(now)
    uid = str(user_id)
    if not any(_live_plan(p, now) and _waits_on(p, uid, session_id) for _, p in _plans()):
        return False
    at = _stamp(now)
    _write(_root() / "messages" / f"{at:020d}_{uuid.uuid4().hex[:8]}.json",
           {"user_id": uid, "session_id": session_id, "at_ns": at,
            "text": text[:MAX_MESSAGE_CHARS]})
    return True


# ── the commit ───────────────────────────────────────────────────────────────

def check(user_id, prog: Dict[str, Any], plan_token: Optional[str],
          approval_quote: Optional[str]) -> Dict[str, Any]:
    """Whether a commit of `prog` may go ahead. `{"ok": True, "approval": {...} or None}` (None
    for a job's first empty plan), or `{"ok": False, "message": ...}` saying what is missing."""
    if is_bootstrap(prog):
        return {"ok": True, "approval": None}
    if not plan_token:
        return {"ok": False, "message": _NO_TOKEN}
    now = _now()
    prune(now)
    uid = str(user_id)
    plan = _read(_plan_path(plan_token))
    if plan is None or plan.get("user_id") != uid or plan.get("token") != plan_token \
            or not _live_plan(plan, now):
        return {"ok": False, "message": _UNKNOWN.format(token=plan_token)}
    if token(prog) != plan_token:
        return {"ok": False, "message": _CHANGED.format(token=plan_token)}
    quote = approval_quote if isinstance(approval_quote, str) else None
    if not hooks_present():
        return {"ok": True, "approval": {"mode": "token_only", "plan_token": plan_token,
                                         "quote": quote or None}}
    if quote is None or not core(quote):
        return {"ok": False, "message": _NO_QUOTE}
    since = [msg for _, msg in _messages()
             if _waits_on(plan, msg.get("user_id"), msg.get("session_id"))
             and int(msg.get("at_ns") or 0) > int(plan.get("shown_ns") or 0)
             and not _expired(msg.get("at_ns") or 0, now)]
    found = {match(quote, str(msg.get("text") or "")) for msg in since}
    if found & {"whole", "span"}:
        return {"ok": True, "approval": {"mode": "quoted", "plan_token": plan_token,
                                         "quote": quote}}
    if "short" in found:
        return {"ok": False, "message": _PARTIAL.format(n=MIN_SPAN_CHARS)}
    return {"ok": False, "message": _NOT_FOUND + ("" if since else _NO_HOOK_HINT)}


def consume(user_id, job_id: str) -> None:
    """A plan for `job_id` committed: forget every plan of that job waiting for approval (its
    parent moved, so none can commit as shown), then every message no other plan needs. Never
    raises."""
    try:
        uid, jid = str(user_id), str(job_id)
        for path, plan in list(_plans()):
            if plan.get("user_id") == uid and plan.get("job_id") == jid:
                _remove(path)
    except OSError as exc:
        log.warning("approval store: could not clear a committed plan (%s)", type(exc).__name__)
    prune()


def pending(user_id) -> List[Dict[str, Any]]:
    """The plans of this user waiting for approval (no message text): for tests and support."""
    now, uid = _now(), str(user_id)
    return [{k: p.get(k) for k in ("token", "job_id", "sessions")}
            for _, p in _plans() if p.get("user_id") == uid and _live_plan(p, now)]
