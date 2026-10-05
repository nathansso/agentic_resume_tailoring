"""The memory gate's tools (#202): `observe` and `record_preference`.

`harness/decisions/memory_gate.py` decides; this module acts. It is the only place a preference is
written from a message.

- **`observe(text)`**: runs the gate on one user message (`art hook user-prompt` calls it on every
  prompt) and returns the decision: `drop`, `host` (it may be a preference: confirm with the user
  and call `record_preference`) or `write` (a clear, low-stakes preference ART saved itself).
  Without a key, in mode `off`, or on an API error, the prefilter alone decides and nothing is
  written.
- **`record_preference(...)`**: the host's path, and the confirmation path for what the gate hands
  over. It validates, resolves the target against the user's knowledge graph, and stores or
  refuses with a reason. Strength 5 and negative pins are allowed here, because here the user has
  confirmed them.

**The write barrier (#129, services.py).** That barrier says a pipeline able to write the user's
tier launders its own output into a counterfeit user choice. The gate's input is the user's own
message and never ART's or the host's output, so this is a user decision made by a classifier; but
it is bounded accordingly. It writes only a preference at strength 4 or less, never replaces a
preference the user holds, never writes a strength-5 preference or a negative pin (those become
gates that refuse plans), and records what it wrote (`provenance.source == "memory_gate"`, with
its p and the message) so a stored preference can be told from a confirmed one.

Every writing path goes through `agents.preferences.compile_preferences`,
`resolve_against_existing` and `services.apply_preference_decision`. Model-free by rule
(`tests/test_harness_boundary.py`): the target catalog is built here from the knowledge graph
instead of with `agents.preferences.target_catalog`, which imports the LLM stack.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

import services
from agents.preferences import (
    POLARITIES, STATUS_ACTIVE, compile_preferences, resolve_against_existing,
)
from harness import transcript
from harness.decisions import memory_gate as mg

log = logging.getLogger(__name__)

SCOPES = ("global", "role_family", "job")
_KEY = re.compile(r"^(?:exp|proj|skill|section):", re.IGNORECASE)
HOOK_TIMEOUT = 8.0           # seconds: a hook must not stall the user's prompt


# ── the catalog ──────────────────────────────────────────────────────────────

def memory_catalog(user_id: UUID) -> List[Dict[str, str]]:
    """What a preference may be about: the user's skills, roles and projects, and the sections, in
    `agents.preferences.target_catalog`'s shape and keys."""
    return mg.make_catalog(
        skills=[s["name"] for s in services.get_skills(user_id)],
        experiences=[(e["title"], e["company"]) for e in services.get_experiences(user_id)],
        projects=[p["name"] for p in services.get_projects(user_id)])


def _find_target(target: str, catalog: List[Dict[str, str]]) -> Optional[Dict[str, str]]:
    """The catalog entry `target` exactly names: its key, its label, or its bare name. Never a
    partial match: a wrong binding silently suppresses the wrong item."""
    want = " ".join(target.lower().split())
    if not want:
        return None
    for entry in catalog:
        if want in (entry["key"].lower(), entry["label"].lower()):
            return entry
    named = [e for e in catalog if want == e["label"].partition(": ")[2].lower()]
    return named[0] if len(named) == 1 else None


def _suggestions(target: str, catalog: List[Dict[str, str]], limit: int = 5) -> List[str]:
    want = target.lower().split(":", 1)[-1].split("|")[0].strip()
    if not want:
        return []
    return [e["key"] for e in catalog if want in e["label"].lower() or want in e["key"].lower()][:limit]


# ── the decision log ─────────────────────────────────────────────────────────

def _log_decision(decision: Dict[str, Any], session_id: Optional[str]) -> None:
    """Every routing decision, with its source and p, to the log and `memory_gate.jsonl` under the
    data directory. The message itself is not stored."""
    g = decision.get("guess") or {}
    row = {"ts": round(time.time(), 3), "session": session_id, "action": decision["action"],
           "reason": decision["reason"], "source": decision["source"], "p": decision.get("p"),
           "version": decision.get("version"), "context": decision.get("context"),
           "candidate": decision["candidate"], "negated": decision["negated"],
           "direction": g.get("direction"), "strength": g.get("strength"), "target": g.get("target")}
    log.info("memory_gate %s", json.dumps(row, sort_keys=True))
    try:
        from config import APP_DATA_DIR
        path = Path(APP_DATA_DIR) / "memory_gate.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
    except Exception as exc:                       # logging must never fail the decision
        log.debug("memory_gate: could not append to the decision log: %s", exc)


def _session_job(session_id: Optional[str]) -> Optional[str]:
    """The job the host's session is working on, from the hook's per-session state."""
    if not session_id:
        return None
    from harness import hooks
    state = hooks.load_state(session_id) or {}
    return state.get("job_id") or None


# ── observe ──────────────────────────────────────────────────────────────────

def _client(bounded: bool):
    """The engine's client; for a hook, one with a short timeout and a single retry."""
    from harness.decisions import engine
    client = engine.get_client()
    if client is not None and bounded:
        client.timeout = min(client.timeout, HOOK_TIMEOUT)
        client.max_retries = min(client.max_retries, 1)
    return client


def previous_turn_for(text: str, catalog: List[Dict[str, str]], previous_turn: Optional[str] = None,
                      transcript_path: Optional[str] = None) -> Tuple[Optional[str], str]:
    """`(turn, context)`: the previous assistant turn to send with `text`, and why.

    Only a message with an unresolved reference ("that", "it", "again", a short yes or no) gets one, so the
    transcript is not even opened for the rest and their answers stay under v1's cache keys. `context` is
    `none` (the message reads on its own), `used` (a turn was found: the gate runs v2) or `missing` (the
    message needs a turn and none could be read, so the gate runs v1). The turn is the caller's
    `previous_turn` when given, else the last assistant text in the host's transcript."""
    if not mg.unresolved_reference(text, catalog):
        return None, "none"
    turn = " ".join((previous_turn or "").split()) or transcript.previous_assistant_turn(transcript_path, text)
    return (turn, "used") if turn and turn.strip() else (None, "missing")


def observe(user_id: UUID, text: str, session_id: Optional[str] = None, *, write: bool = True,
            bounded: bool = False, previous_turn: Optional[str] = None,
            transcript_path: Optional[str] = None) -> Dict[str, Any]:
    """Run the memory gate on one user message. See the module docstring. `write` False
    (a read-only store) keeps the gate from writing; `bounded` shortens Jev's timeout for a hook.
    `previous_turn` (a host passing the assistant's last reply) or `transcript_path` (what the hook has)
    give a message that depends on the turn before it (`memory_gate@v2`); the result says which question
    version ran (`version`) and whether a turn was used (`context`)."""
    pre = mg.prefilter(text)
    decision = mg.route_prefilter(text, pre)
    context = "none"
    if decision is None:
        catalog = memory_catalog(user_id)
        turn, context = previous_turn_for(text, catalog, previous_turn, transcript_path)
        version = mg.version_for(turn)
        answers = mg.ask(text, catalog, turn, client=_client(bounded))
        guess = mg.parse_answers(answers, catalog, text, version)
        job_id = _session_job(session_id)
        decision = mg.route(text, pre, guess, job_known=bool(job_id), write_ok=write,
                            unavailable=mg.unavailable_reason(answers) if guess is None else None)
        if decision["action"] == "write":
            decision = _auto_write(user_id, decision, catalog, job_id, session_id)
    decision["context"] = context
    decision["note"] = mg.explain(decision)
    _log_decision(decision, session_id)
    return decision


def _auto_write(user_id: UUID, decision: Dict[str, Any], catalog: List[Dict[str, str]],
                job_id: Optional[str], session_id: Optional[str]) -> Dict[str, Any]:
    """Store what the gate decided to write, unless the user already holds it (nothing to do) or
    it would replace something they hold (their call)."""
    g = decision["guess"]
    entry = next(c for c in catalog if c["key"] == g["target"])
    scoped = decision["scope"] == "job"
    note = {"text": decision["text"], "polarity": g["direction"], "target_label": entry["label"],
            "scope_type": "job" if scoped else "global", "scope_value": job_id if scoped else None,
            "strength": g["strength"], "evidence": decision["text"], "confidence": g["p"]}
    compiled = compile_preferences([note], [entry], provenance={
        "source": "memory_gate", "job_id": job_id if scoped else None, "session_id": session_id,
        "gate": {"p": g["p"], "source": decision["source"], "version": decision.get("version"),
                 "direction": g["direction"],
                 "strength": g["strength"], "target": g["target"]}})
    [proposal] = resolve_against_existing(compiled, services.load_preferences(user_id))
    if proposal["decision"] == "no_op":
        return {**decision, "action": "drop", "reason": "already_held"}
    if proposal["decision"] == "supersede":
        return {**decision, "action": "host", "reason": "would_supersede",
                "supersedes_text": proposal.get("supersedes_text")}
    result = services.apply_preference_decision(user_id, proposal)
    stored = _find_stored(user_id, proposal)
    if stored is None:
        log.warning("memory_gate: could not store the preference: %s", result)
        return {**decision, "action": "host", "reason": "write_failed"}
    return {**decision, "preference_id": stored["preference_id"]}


def _find_stored(user_id: UUID, proposal: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The active preference `apply_preference_decision` just stored (it returns text, not the row)."""
    rows = [p for p in services.load_preferences(user_id)
            if p["status"] == STATUS_ACTIVE and p["text"] == proposal["text"]
            and p["scope_type"] == proposal["scope_type"]
            and (p.get("scope_value") or None) == (proposal.get("scope_value") or None)
            and p["polarity"] == proposal["polarity"] and p.get("target_key") == proposal.get("target_key")]
    return rows[-1] if rows else None


# ── record_preference ────────────────────────────────────────────────────────

def _refuse(reason: str, **extra: Any) -> Dict[str, Any]:
    return {"status": "refused", "reason": reason, **extra}


def _summary(pref: Dict[str, Any]) -> Dict[str, Any]:
    strength = pref.get("strength") or 0
    return {"preference_id": pref.get("preference_id"), "text": pref["text"],
            "polarity": pref["polarity"], "target_key": pref.get("target_key"),
            "target_term": pref.get("target_term"), "scope_type": pref["scope_type"],
            "scope_value": pref.get("scope_value"), "strength": pref.get("strength"),
            "target_resolved": bool(pref.get("target_key")),
            "pinned": strength >= mg.PIN_STRENGTH,
            "negative_pin": strength >= mg.PIN_STRENGTH and pref["polarity"] == "suppress"}


def record_preference(user_id: UUID, text: str, polarity: str, target: Optional[str] = None,
                      strength: int = 3, scope: str = "global", scope_value: Optional[str] = None,
                      quote: Optional[str] = None) -> Dict[str, Any]:
    """Store one preference the user stated or confirmed, or refuse it with a reason.

    Resolves `target` against the knowledge graph (an exact key, label or name; never a partial
    match), validates, and resolves against what the user already holds: a new subject is stored,
    a different polarity or strength for the same subject and scope replaces the old one (which is
    kept, superseded), the same again is `already_recorded`. A strength of 5 is stored as a pin."""
    text = " ".join((text or "").split())
    if not text:
        return _refuse("text is empty: give the preference as a standalone instruction.")
    if polarity not in POLARITIES:
        return _refuse(f"polarity must be one of {', '.join(POLARITIES)}.")
    if not isinstance(strength, int) or not 1 <= strength <= 5:
        return _refuse("strength must be 1 (a passing remark) to 5 (an absolute rule).")
    if scope not in SCOPES:
        return _refuse(f"scope must be one of {', '.join(SCOPES)}.")
    scope_value = (scope_value or "").strip() or None
    if scope == "job":
        try:
            UUID(str(scope_value))
        except ValueError:
            return _refuse("a job-scoped preference needs scope_value: the job id (see list_jobs).")
    elif scope == "role_family":
        if not scope_value:
            return _refuse("a role_family preference needs scope_value: the role family, e.g. "
                           "data_science.")
        scope_value = scope_value.lower()
    else:
        scope_value = None
    target = " ".join((target or "").split())
    if polarity in ("emphasize", "suppress") and not target:
        return _refuse(f"name what to {polarity} in `target`: a key from list_items, a "
                       "section:<name>, or a topic such as 'GPA'.")

    catalog = memory_catalog(user_id)
    entry = _find_target(target, catalog) if target else None
    suggestions = [] if entry or not target else _suggestions(target, catalog)
    term = None if entry else (re.sub(r"^[a-z]+:", "", target, flags=re.IGNORECASE).split("|")[0].strip()
                               if _KEY.match(target) else target)
    note = {"text": text, "polarity": polarity, "target_label": entry["label"] if entry else (term or None),
            "scope_type": scope, "scope_value": scope_value, "strength": strength,
            "evidence": (quote or text), "confidence": None}
    compiled = compile_preferences([note], [entry] if entry else [], provenance={
        "source": "host", "job_id": scope_value if scope == "job" else None})
    existing = services.load_preferences(user_id)
    [proposal] = resolve_against_existing(compiled, existing)

    if proposal["decision"] == "no_op":
        prior = next((p for p in existing if str(p["preference_id"]) == str(proposal.get("supersedes_id"))), None)
        if prior is not None and prior.get("strength") == strength:
            return {"status": "already_recorded", **_summary(prior), "suggestions": suggestions}
        proposal["decision"] = "supersede"            # the same subject, a different strength: the user changed it
    result = services.apply_preference_decision(user_id, proposal)
    stored = _find_stored(user_id, proposal)
    if stored is None:
        log.warning("record_preference: could not store: %s", result)
        return _refuse("the preference could not be stored.")
    return {"status": "superseded" if proposal["decision"] == "supersede" else "stored",
            **_summary(stored), "supersedes": proposal.get("supersedes_text"), "suggestions": suggestions}
