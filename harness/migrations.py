"""Migrate preference rows written before the anchoring model to it (#300).

Phases 1 to 6 of the preference-anchoring plan changed what a preference row means: an item
preference binds to a knowledge-graph key (#295), follows renames and deletes (#297), and a
preference about the page is a `ResumeRule` (#298). Rows written earlier are still in users'
stores: unkeyed topics that now name an item, keys whose item is gone, and `section:` rows that
are enforced as the dead term "section: projects". `migrate_preferences` brings them over.

**Conservative: it never guesses.** Each active row is sorted into exactly one outcome:

| row                                                         | outcome                                                   |
|-------------------------------------------------------------|-----------------------------------------------------------|
| keyed to an item that exists                                | untouched (`unchanged`)                                   |
| unkeyed, names exactly one item (`relink`, alias-aware)     | superseded by a keyed copy (`relinked`)                   |
| keyed to an item that no longer exists                      | superseded by a pending copy, `unlinked_from` (`unlinked`) |
| `section:<name>` suppress, or a page row whose words state  | a `limit` `ResumeRule`; the row is superseded with        |
| one plain limit (`rules.infer_limit`)                       | `provenance.migrated_to` (`converted_to_rules`)           |
| anything else                                               | left exactly as it was, with a reason (`left_alone`)      |

Nothing is deleted: a changed row is `superseded`, as in #297. A converted `section:` suppress
keeps its strength and scope, so a hard one becomes a hard rule and now actually removes the
section. A conversion that `rules.loosens_hard` refuses, or that disagrees with a rule the user
recorded since, is left alone and reported: a rule the user stated later outranks an older row.

**Idempotent by row state, not by a table.** Every row the migration writes carries
`provenance.migration`; a second run finds no row to change (a keyed copy is keyed, a pending
copy has no key to re-resolve, a superseded row is not active) and reports the rows it did before
as `already_done`. The automatic run at startup (`run_once`) is additionally gated by a marker
file next to the local store, so it costs one read after the first run.

Lives in `harness/` and not in `services`: it composes the resolver (`anchors`), `relink` and the
rule validators, and its two writes go through `services` (`reanchor_preferences`,
`supersede_preferences`, `save_resume_rule`). Model-free by rule (`tests/test_harness_boundary.py`).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence
from uuid import UUID

# `services` (and everything that imports it) is imported inside the functions, never here: the
# `art migrate` command reaches this module before `harness.runtime.bootstrap` has pinned
# `DATABASE_URL`, and a module-level import would build the engine against whatever `.env` holds.

log = logging.getLogger(__name__)

MIGRATION_ID = "preference_anchoring_v1"
MARKER_FILE = "migrations.json"

_KEY_KINDS = ("exp", "proj", "skill", "edu", "ach")
_LABEL_PREFIX = re.compile(r"^(?:skill|experience|project|education|achievement)\s*:\s*", re.IGNORECASE)
# A page count under a negator can be the opposite of a limit ("avoid one-page resumes"). It is
# read as a limit only when the negator is part of a plain maximum ("never exceed one page").
_NEGATOR = re.compile(r"\b(?:never|avoid|not|no|without|nor|neither|don\W?t|do\s+not|won\W?t|"
                      r"shouldn\W?t|can\W?t|cannot)\b|n['’]t\b", re.IGNORECASE)
_MAXIMUM = re.compile(
    r"\b(?:never|not|no|don\W?t|do\s+not|shouldn\W?t|can\W?t|cannot)\s+(?:\w+\s+){0,2}?"
    r"(?:exceed|more\s+than|longer\s+than|go\s+(?:over|past|beyond)|run\s+(?:over|past)|"
    r"spill\s+(?:over|onto)|over|past|beyond)\b", re.IGNORECASE)


# ── helpers ──────────────────────────────────────────────────────────────────

def _stamp(p: Dict[str, Any]) -> str:
    return str(p.get("created_at") or "")


def _left(p: Dict[str, Any], code: str, reason: str) -> Dict[str, Any]:
    return {"preference_id": p["preference_id"], "code": code, "reason": reason, "text": p["text"]}


def _dangling_term(p: Dict[str, Any], key: str) -> str:
    """The words a ban on a vanished item keeps. The user's own stated subject first, with a
    role as "title @ company" (#297: a bare title is a thing other roles share and a term pin on
    it would refuse them); else what the key itself spells."""
    kind, rest = key.split(":", 1)
    stated = _LABEL_PREFIX.sub("", (p.get("target_term") or "").strip()).strip()
    if kind == "exp":
        title, _, company = rest.partition("|")
        if "@" in stated:
            return stated
        return f"{stated or title} @ {company}" if company else (stated or title)
    if kind == "edu":
        return stated or rest.partition("|")[0] or rest
    return stated or rest


def _page_words_ok(text: str, params: Dict[str, Any]) -> bool:
    """A page count is converted only when no negator turns it, or the negator is a plain maximum."""
    if "pages" not in params or not _NEGATOR.search(text or ""):
        return True
    return bool(_MAXIMUM.search(text or ""))


def _scope_problem(user_id: UUID, p: Dict[str, Any]) -> Optional[str]:
    """Why this row's scope cannot be carried to a rule as it stands, or None."""
    from harness import rules as rules_mod
    scope = p.get("scope_type") or "global"
    if scope not in rules_mod.SCOPES:
        return f"scope {scope!r} is not one a rule can have"
    if scope == "role_family" and not (p.get("scope_value") or "").strip():
        return "a role_family scope with no role family"
    if scope == "job":
        try:
            UUID(str(p.get("scope_value")))
        except ValueError:
            return "a job scope with no valid job id"
    return None


# ── the migration ────────────────────────────────────────────────────────────

def migrate_preferences(user_id: UUID, dry_run: bool = False) -> Dict[str, Any]:
    """Bring this user's preference rows to the anchored model. See the module docstring.

    Returns `{migration, user_id, dry_run, relinked, unlinked, converted_to_rules, left_alone,
    already_done, unchanged, counts}`. `dry_run` reads everything and writes nothing: the same
    decisions are reported, with `rule_id` None for a rule that would be created."""
    import services
    from harness import anchors, relink as relink_mod, rules as rules_mod

    user_id = user_id if isinstance(user_id, UUID) else UUID(str(user_id))
    marker = {"migration": MIGRATION_ID}
    out: Dict[str, Any] = {
        "migration": MIGRATION_ID, "user_id": str(user_id), "dry_run": bool(dry_run),
        "relinked": [], "unlinked": [], "converted_to_rules": [], "left_alone": [],
        "already_done": 0, "unchanged": 0, "counts": {}}

    everything = services.load_preferences(user_id, include_inactive=True)
    out["already_done"] = sum(1 for p in everything
                              if (p.get("provenance") or {}).get("migration") == MIGRATION_ID)
    if not any(p["status"] == "active" for p in everything):
        out["counts"] = _counts(out, total=0)
        return out

    # 1. Unkeyed rows that now name exactly one item: #297's relink, alias-aware through #295.
    #    `relink` leaves everything it does not bind exactly as it was.
    moved = relink_mod.relink(user_id, dry_run=dry_run, provenance=marker)
    out["relinked"] = moved["relinked"]
    # The old rows, and the keyed copies the real run just wrote: neither is a row to judge again.
    handled = {r["supersedes_id"] if not dry_run else r["preference_id"] for r in moved["relinked"]}
    if not dry_run:
        handled |= {r["preference_id"] for r in moved["relinked"]}

    active = [p for p in services.load_preferences(user_id) if p["preference_id"] not in handled]
    items = anchors.items_for(user_id)
    item_keys = {i["key"].lower() for i in items}
    rules = services.load_resume_rules(user_id)

    unlink_changes: List[Dict[str, Any]] = []
    superseded: List[Dict[str, Any]] = []

    # Strongest first, so of two rows that say the same thing the harder one makes the rule and
    # the softer one reuses it, whatever order they were written in.
    for p in sorted(active, key=lambda r: (-(r.get("strength") or 0), _stamp(r), r["preference_id"])):
        key = (p.get("target_key") or "").strip().lower()
        term = (p.get("target_term") or "").strip()

        if key.startswith("section:") or p.get("target_type") == "section":
            name = key.split(":", 1)[1] if key.startswith("section:") else ""
            if name not in rules_mod.SECTION_NAMES:
                out["left_alone"].append(_left(p, "unknown_section",
                                               f"no section named {name or term!r}"))
                continue
            _convert(user_id, p, key, rules, out, superseded, dry_run, marker)
            continue

        if key:
            kind = key.split(":", 1)[0]
            if kind not in _KEY_KINDS:
                out["left_alone"].append(_left(p, "unrecognized_key", f"{key!r} is not an item key"))
            elif key in item_keys:
                out["unchanged"] += 1
            elif not items:
                out["left_alone"].append(_left(p, "no_items", "the store holds no items to check "
                                               "this key against"))
            else:
                words = _dangling_term(p, key)
                unlink_changes.append({
                    "preference_id": p["preference_id"], "target_key": None, "target_type": "topic",
                    "target_term": words or None,
                    "provenance": {**marker, "unlinked_from": key}})
                out["unlinked"].append({"preference_id": p["preference_id"], "key": key,
                                        "term": words})
            continue

        # Unkeyed. `relink` took what binds; what is left is a thing that is still absent (a term
        # pin, by design), a subject that fits several items, or a subject about the page.
        resolved = anchors.resolve(items, term) if term else {}
        status = resolved.get("status")
        if status == anchors.PENDING:
            out["left_alone"].append(_left(p, "pending", "no item names it yet: still enforced as a "
                                           "term pin, and it re-links when ingestion adds it"))
        elif status == anchors.AMBIGUOUS:
            out["left_alone"].append(_left(p, "ambiguous", "its subject fits several items: say "
                                           "which one it means"))
        elif status == anchors.MULTIPLE:
            out["left_alone"].append(_left(p, "multiple", "it names several things: split it"))
        elif status == anchors.BOUND:
            out["left_alone"].append(_left(p, "not_relinkable", "it names an item but is not a "
                                           "suppress or emphasize preference, or came from a "
                                           "deleted item and re-binds only to that item"))
        else:                                            # a document subject, or no subject at all
            named = resolved.get("key") if str(resolved.get("key", "")).startswith("section:") else None
            _convert(user_id, p, named, rules, out, superseded, dry_run, marker)

    if not dry_run:
        if unlink_changes:
            services.reanchor_preferences(user_id, unlink_changes)
        if superseded:
            services.supersede_preferences(user_id, superseded)
    out["counts"] = _counts(out, total=len(active) + len(moved["relinked"]))
    return out


def _convert(user_id: UUID, p: Dict[str, Any], section_key: Optional[str], rules: List[Dict[str, Any]],
             out: Dict[str, Any], superseded: List[Dict[str, Any]], dry_run: bool,
             marker: Dict[str, Any]) -> None:
    """One row that is about the page. Converted only when the words (or the `section:` target) state
    one plain limit; otherwise left alone with the reason."""
    import services
    from harness import rules as rules_mod

    text, polarity = p["text"], p.get("polarity")
    params = rules_mod.infer_limit(text, section_key or (p.get("target_term") or None), polarity)
    if not params:
        if section_key:
            why = ("an emphasize or reframe on a section is not a limit: record a section_order or "
                   "style_note rule (record_rule) with the order or wording the user means"
                   if polarity != "suppress" else
                   "its words do not state one plain exclusion (negated, or a page count too)")
            out["left_alone"].append(_left(p, "section_not_clear", why))
        else:
            out["left_alone"].append(_left(p, "no_target", "it names nothing to anchor to and states "
                                           "no plain page limit"))
        return
    if not _page_words_ok(text, params):
        out["left_alone"].append(_left(p, "negated_limit", "the words are negated, so a page count "
                                       "in them may be the opposite of a limit: record it with "
                                       "record_rule if the user means one"))
        return
    problem = _scope_problem(user_id, p)
    strength = p.get("strength")
    if problem or not isinstance(strength, int) or isinstance(strength, bool) or not 1 <= strength <= 5:
        out["left_alone"].append(_left(p, "bad_row", problem or f"strength {strength!r} is not 1 to 5"))
        return

    scope = p.get("scope_type") or "global"
    value = (p.get("scope_value") or "").strip() or None
    value = value.lower() if scope == "role_family" else (value if scope == "job" else None)
    candidate = {"kind": "limit", "params": params, "text": text, "scope_type": scope,
                 "scope_value": value, "strength": strength,
                 "provenance": {"source": "migration", **marker, "migrated_from": p["preference_id"],
                                "quote": text}}
    family = None
    if scope == "job":
        try:
            from harness import tools
            family = tools.scope_family(user_id, value)
        except Exception as exc:                          # an unknown family only weakens the check
            log.warning("scope_family failed for job %s: %s", value, exc)
    blocker = rules_mod.loosens_hard(candidate, rules, family)
    if blocker is not None:
        out["left_alone"].append(_left(p, "scope_conflict", (
            f"a {scope} rule may only tighten {rules_mod.describe(blocker)}, which is hard at "
            f"{blocker['scope_type']} scope: converting it would loosen that rule")))
        return

    same = [r for r in rules if rules_mod.identity(r) == rules_mod.identity(candidate)]
    existing = next((r for r in same if r["params"] == params and r["strength"] >= strength), None)
    if same and existing is None:
        out["left_alone"].append(_left(p, "rule_conflict", (
            "a rule of the same kind and scope the user recorded since says otherwise "
            f"({rules_mod.describe(same[0])}): the newer statement stands")))
        return

    reused = existing is not None
    rule_id: Optional[str] = existing["rule_id"] if reused else None
    if not reused and not dry_run:
        stored = services.save_resume_rule(user_id, candidate)
        if stored is None:
            out["left_alone"].append(_left(p, "rule_not_stored", "the rule could not be written"))
            return
        rule_id = stored["rule_id"]
        rules.append(stored)
    elif not reused:
        rules.append({**candidate, "rule_id": None, "status": "active"})
    if rule_id is not None:
        superseded.append({"preference_id": p["preference_id"],
                           "provenance": {**marker, "migrated_to": rule_id}})
    out["converted_to_rules"].append({"preference_id": p["preference_id"], "rule_id": rule_id,
                                      "kind": "limit", "params": params, "reused": reused})


def _counts(out: Dict[str, Any], total: int) -> Dict[str, int]:
    return {"active_rows": total, "relinked": len(out["relinked"]), "unlinked": len(out["unlinked"]),
            "converted_to_rules": len(out["converted_to_rules"]),
            "left_alone": len(out["left_alone"]), "unchanged": out["unchanged"],
            "already_done": out["already_done"]}


# ── when it runs ─────────────────────────────────────────────────────────────

def _marker_path(database_url: str) -> Optional[Path]:
    from harness.runtime import local_sqlite_path
    path = local_sqlite_path(database_url)
    return path.parent / MARKER_FILE if path is not None else None


def _marker_read(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def run_once(user_id: UUID, database_url: str) -> Optional[Dict[str, Any]]:
    """The automatic run: once per user, on a local SQLite store only. Returns the report, or None
    when it did not run (a remote store, or already done for this user). Never raises: it runs at
    startup, and a failed migration leaves every row as it was.

    The marker is a small JSON file beside the store. It is written only when the user held
    preference rows, so a store that gets its rows later (`art import` of an older backup) is still
    migrated at its next start; running the CLI by hand always works whatever the marker says."""
    try:
        path = _marker_path(database_url)
        if path is None:
            return None
        done = _marker_read(path)
        if str(user_id) in (done.get(MIGRATION_ID) or {}):
            return None
        report = migrate_preferences(user_id)
        if report["counts"].get("active_rows"):
            done.setdefault(MIGRATION_ID, {})[str(user_id)] = datetime.now(timezone.utc).isoformat()
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(done, indent=2, sort_keys=True), encoding="utf-8")
            os.replace(tmp, path)
        changed = sum(report["counts"][k] for k in ("relinked", "unlinked", "converted_to_rules"))
        if changed:
            log.info("art: migrated preferences (%d relinked, %d unlinked, %d converted to rules, "
                     "%d left alone)", report["counts"]["relinked"], report["counts"]["unlinked"],
                     report["counts"]["converted_to_rules"], report["counts"]["left_alone"])
        return report
    except Exception as exc:                              # noqa: BLE001 — never into startup
        log.warning("art: preference migration skipped: %s", exc)
        return None


# ── `art migrate preferences` ────────────────────────────────────────────────

def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="art migrate", description="Migrate stored data to a newer model.")
    p.add_argument("what", choices=["preferences"],
                   help="preferences: anchor old preference rows and convert section rows to rules")
    p.add_argument("--dry-run", action="store_true", help="report what would change; write nothing")
    p.add_argument("--user-id", help="ART user id (default: ~/.art pointer file)")
    p.add_argument("--database-url", help="DB URL, or 'dotenv' to use .env's DATABASE_URL")
    p.add_argument("--allow-writes", action="store_true",
                   help="Let this write to a remote database.")
    return p


def _emit(doc: Any) -> None:
    sys.stdout.write(json.dumps(doc, sort_keys=True, ensure_ascii=False, default=str) + "\n")


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    from harness.runtime import bootstrap
    # `auto_migrate=False`: this command is the migration, and --dry-run must write nothing.
    user_id, writes = bootstrap(args.database_url, args.user_id, args.allow_writes, prepare=True,
                                auto_migrate=False)
    if user_id is None:
        _emit({"error": {"code": "no_user", "message": "No user: pass --user-id or set the active "
                         "profile."}})
        return 2
    if not args.dry_run and not writes:
        _emit({"error": {"code": "read_only", "message": "This store is read-only: pass --dry-run to "
                         "see what would change, or --allow-writes to migrate it."}})
        return 2
    try:
        _emit(migrate_preferences(user_id, dry_run=args.dry_run))
    except Exception as exc:                              # noqa: BLE001
        _emit({"error": {"code": "migration_failed", "message": str(exc)}})
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
