"""The "Active constraints for this job" block (issue #299): a reminder right before the host writes.

PrefEval (ICLR 2025) found that adherence to a stated preference collapses with distance, and
that a reminder just before generation is the single biggest fix. The briefing is read at the
start of a session and the hook restores pins after compaction; this is the third push, at the
two moments a host is about to write: `suggest_actions` (choosing what each item should say)
and an `execute_plan` dry run (the check before it commits).

One structured block, and a short rendered text of the same thing, each entry carrying the
scope it came from:

- `hard_pins`: strength-5 preferences bound to an item, a skill or a term. A plan that breaks
  one is refused.
- `pending_bans`: suppressions of a thing the knowledge graph does not hold yet (no
  `target_key`). A strength-5 one is enforced as a page-wide term pin (#296); a softer one is
  reported only.
- `item_preferences`: every other in-scope preference. Strength 1-4 is never a gate.
- `rules`: `hard` (a plan that loosens one is refused), `soft` (defaults a plan may override)
  and `style_notes` (followed, never checked).

The set is read through `preferences_for` and `rules_for`, the scope `execute_plan` enforces
(#291, #298), so the block cannot list something the executor will not apply, or leave out
something it will. Additive everywhere it appears; it never changes a field that was there.

Model-free by rule (`tests/test_harness_boundary.py`).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from uuid import UUID

from harness import rules as rules_mod
from harness.decisions.negative_pins import pin_target
from harness.tools import PIN_STRENGTH, preferences_for, rules_for

TITLE = "Active constraints for this job"


def scope_label(scope_type: Optional[str], scope_value: Optional[str]) -> str:
    """A scope as a short phrase: `global`, `role family data_science`, `this job`. A job scope
    in a block is always this job's: the block holds only what is in scope for it."""
    scope = scope_type or "global"
    if scope == "role_family":
        return f"role family {scope_value}" if scope_value else "role family"
    if scope == "job":
        return "this job"
    return "global"


def _pref_entry(p: Dict[str, Any]) -> Dict[str, Any]:
    strength = p.get("strength") or 0
    return {"preference_id": str(p.get("preference_id")), "text": p.get("text"),
            "polarity": p.get("polarity"), "strength": strength,
            "target_key": p.get("target_key"), "target_term": p.get("target_term"),
            "scope": p.get("scope_type") or "global", "scope_value": p.get("scope_value"),
            "scope_label": scope_label(p.get("scope_type"), p.get("scope_value"))}


def pending_bans(prefs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The suppressions in `prefs` that name something no item holds (no `target_key`): the
    user's own words, whether the executor enforces them (`hard`, strength 5, as a page-wide
    term pin) or only reports them."""
    out = []
    for p in prefs:
        if p.get("polarity") != "suppress" or (p.get("target_key") or "").strip():
            continue
        entry = _pref_entry(p)
        entry["hard"] = entry["strength"] >= PIN_STRENGTH
        out.append(entry)
    return sorted(out, key=lambda e: (-e["strength"], e["preference_id"]))


def active_constraints(user_id: UUID, job_id: Optional[str] = None,
                       role_family: Optional[str] = None) -> Dict[str, Any]:
    """The block for one job. `job_id` scopes it exactly as `execute_plan` does."""
    prefs, family = preferences_for(user_id, job_id, role_family)
    prefs = sorted(prefs, key=lambda p: (-(p.get("strength") or 0), str(p.get("preference_id"))))
    bans = pending_bans(prefs)
    ban_ids = {b["preference_id"] for b in bans}
    hard_pins: List[Dict[str, Any]] = []
    items: List[Dict[str, Any]] = []
    for p in prefs:
        entry = _pref_entry(p)
        if entry["preference_id"] in ban_ids:
            continue
        if entry["strength"] >= PIN_STRENGTH and (entry["target_key"] or "").strip():
            target = pin_target(p)
            key = (entry["target_key"] or "").strip().lower()
            hard_pins.append({**entry, "kind": target[0] if target else (
                "keep" if p.get("polarity") == "emphasize" and key.startswith(("exp:", "proj:"))
                else None)})
        else:
            items.append(entry)

    page_rules, _ = rules_for(user_id, job_id, family)
    view = rules_mod.active_view(rules_mod.resolve(page_rules))

    def rule(r: Dict[str, Any]) -> Dict[str, Any]:
        return {"rule_id": r.get("rule_id"), "kind": r.get("kind"), "text": r.get("text"),
                "param": r.get("param"), "value": r.get("value"), "strength": r.get("strength"),
                "scope": r.get("scope_type") or "global", "scope_value": r.get("scope_value"),
                "scope_label": scope_label(r.get("scope_type"), r.get("scope_value"))}

    rules = {"hard": [rule(r) for r in view["hard"]],
             "soft": [rule(r) for r in view["soft"]],
             "style_notes": [rule({**r, "param": None, "value": None}) for r in view["notes"]]}
    block: Dict[str, Any] = {
        "title": TITLE, "job_id": str(job_id) if job_id else None, "role_family": family,
        "hard_pins": hard_pins, "pending_bans": bans, "item_preferences": items, "rules": rules,
        "counts": {"hard_pins": len(hard_pins), "pending_bans": len(bans),
                   "item_preferences": len(items), "rules_hard": len(rules["hard"]),
                   "rules_soft": len(rules["soft"]), "style_notes": len(rules["style_notes"])},
    }
    block["text"] = render_text(block)
    return block


def _quote(text: Any) -> str:
    return '"' + " ".join(str(text or "").split()) + '"'


def render_text(block: Dict[str, Any]) -> str:
    """The block as a few lines for the host to read, empty sections left out."""
    head = f"{TITLE}"
    if block.get("role_family"):
        head += f" (role family: {block['role_family']})"
    lines = [head + ":"]
    sections = [
        ("Hard pins (a plan that breaks one is refused)", block["hard_pins"],
         lambda e: f"{_quote(e['text'])} [{e['scope_label']}]"),
        ("Pending bans (not in your knowledge graph yet; a hard one is enforced as a page-wide "
         "term)", block["pending_bans"],
         lambda e: f"{_quote(e['text'])} [{e['scope_label']}, "
                   f"{'hard' if e['hard'] else 'strength ' + str(e['strength'])}]"),
        ("Item preferences (strength 1-4 never blocks; follow them)", block["item_preferences"],
         lambda e: f"{_quote(e['text'])} [{e['scope_label']}, {e['polarity']}, "
                   f"strength {e['strength']}]"),
        ("Page rules, hard (a plan that loosens one is refused)", block["rules"]["hard"],
         lambda e: f"{_quote(e['text'])} ({e['param']}: {e['value']}) [{e['scope_label']}]"),
        ("Page rules, soft (defaults a plan may override)", block["rules"]["soft"],
         lambda e: f"{_quote(e['text'])} ({e['param']}: {e['value']}) [{e['scope_label']}]"),
        ("Style notes (follow; nothing checks them)", block["rules"]["style_notes"],
         lambda e: f"{_quote(e['text'])} [{e['scope_label']}]"),
    ]
    empty = True
    for title, entries, fmt in sections:
        if not entries:
            continue
        empty = False
        lines.append(f"{title}:")
        lines += [f"- {fmt(e)}" for e in entries]
    if empty:
        lines.append("None: nothing the user has said applies to this job.")
    return "\n".join(lines)
