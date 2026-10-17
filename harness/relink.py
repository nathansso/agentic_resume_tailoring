"""Re-link pending preferences when the graph gains the item they name (#297).

A preference whose subject the graph did not hold is stored *pending*: `target_key` NULL and
`target_term` the user's words (`harness/anchors.py`, #295). Ingestion can later create that item.
`relink` runs after `upsert_items` and attaches each such preference to the item it now names, the
supersede way (`services.reanchor_preferences`): the pending row is marked superseded and a keyed
copy, with `provenance.relinked_from`, takes its place. Nothing is deleted.

The resolver decides, not this module: only a `bound` result relinks. `ambiguous` (two items fit),
`pending` (still absent), `document` and `multiple` leave the row exactly as it was, so "Kafka"
does not bind to "Kafka Streams" and a subject that fits two items waits for the user.

A preference that was *unlinked* by deleting its item (`provenance.unlinked_from`) comes back only to
that same key. Its words are the deleted item's name, and without that rule deleting one of two
roles with the same title would hand the ban to the other.

Idempotent: a second run finds no pending row that binds. Model-free by rule
(`tests/test_harness_boundary.py`).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from uuid import UUID

import services
from harness import anchors

log = logging.getLogger(__name__)

# Preferences that name a thing. A style or reframe preference has no item to attach to.
_RELINKABLE = ("suppress", "emphasize")

_TARGET_TYPE = {"exp": "experience", "proj": "project", "skill": "skill",
                "edu": "topic", "ach": "topic"}      # an education or award stays a topic (#295)


def relink(user_id: UUID, dry_run: bool = False,
           provenance: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Attach every active pending suppress/emphasize preference whose subject now binds to exactly
    one item. Returns `{relinked: [{preference_id, supersedes_id, text, polarity, key}], count}`;
    the ids are the new row's and the old one's. Never raises: ingestion has already succeeded.

    `dry_run` writes nothing: each entry then carries the old row's id as `preference_id` and
    `supersedes_id` None. `provenance` is merged into every keyed copy (the migration's marker, #300)."""
    out: Dict[str, Any] = {"relinked": [], "count": 0}
    try:
        pending = [p for p in services.load_preferences(user_id)
                   if not (p.get("target_key") or "").strip()
                   and (p.get("target_term") or "").strip()
                   and p.get("polarity") in _RELINKABLE]
        if not pending:
            return out
        items = anchors.items_for(user_id)
        changes: List[Dict[str, Any]] = []
        for pref in pending:
            result = anchors.resolve(items, pref["target_term"])
            if result["status"] != anchors.BOUND:
                continue
            key = result["key"]
            was = (pref.get("provenance") or {}).get("unlinked_from")
            if was and was != key:
                continue
            changes.append({"preference_id": pref["preference_id"], "target_key": key,
                            "target_type": _TARGET_TYPE.get(key.split(":", 1)[0], "topic"),
                            "provenance": {**(provenance or {}),
                                           "relinked_from": pref["preference_id"]}})
        if dry_run:
            by_id = {p["preference_id"]: p for p in pending}
            for change in changes:
                pref = by_id[change["preference_id"]]
                out["relinked"].append({
                    "preference_id": pref["preference_id"], "supersedes_id": None,
                    "text": pref["text"], "polarity": pref["polarity"], "key": change["target_key"]})
            out["count"] = len(out["relinked"])
            return out
        for row in services.reanchor_preferences(user_id, changes):
            out["relinked"].append({
                "preference_id": row["preference_id"], "supersedes_id": row["supersedes_id"],
                "text": row["text"], "polarity": row["polarity"], "key": row["target_key"]})
        out["count"] = len(out["relinked"])
    except Exception as exc:   # a failed re-link leaves the rows pending; it never fails an ingest
        log.warning("relink failed for user %s: %s", user_id, exc)
    return out
