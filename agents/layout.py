"""Explicit user arrangement overrides (issue #118).

`tailor()` resolves section order, skill order, and bullet order on every run.
Issue #115 gave that resolution a carry-forward tier so the pipeline stops
clobbering its own prior choice; this module adds the tier above it — an
arrangement the *user* chose and wants respected over the ranker.

Everything here is pure: no DB, no LLM, no I/O. The reconciliation rules are
the whole point of the module and they are the same in all three dimensions:

  1. An override entry naming something no longer present is dropped.
  2. Something present but unnamed by the override is appended in the order the
     pipeline produced.

Rule 2 is the safety property. A stale override must never silently remove
content from a resume, so the reconciler only ever *permutes* — it cannot drop
a bullet, a skill, or a section that the pipeline put there.

Identity is by text, never by ordinal. That is the lesson #121 paid for on the
JD-profile side: matching by position let an edited requirement swallow a
genuinely new one landing at the same index after an insertion. Bullets are
rewritten by the planner between runs, so exact text identity is tried first
and a deterministic token-overlap fallback catches the revised-in-place case.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

# Above this Jaccard overlap, a bullet the planner rewrote is still recognized
# as the bullet the user positioned. Deliberately high: mis-binding two
# distinct bullets reorders content the user never touched, which is worse
# than degrading to "unmatched" and appending in pipeline order.
_REVISION_MATCH_MIN = 0.6

_WORD_RE = re.compile(r"[a-z0-9]+")


def _norm(value: object) -> str:
    """Comparable form of a bullet, skill, or item label.

    Folds case and drops punctuation so a value that round-tripped through
    LaTeX still matches its source: the formatter escapes `%` to `\\%` and the
    editor's `displayText` strips commands back out again, and neither should
    be able to break identity.
    """
    if not isinstance(value, str):
        return ""
    return " ".join(_WORD_RE.findall(value.lower()))


def _tokens(value: object) -> set:
    return set(_WORD_RE.findall(value.lower())) if isinstance(value, str) else set()


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def resolve_section_order(
    override: Optional[List[str]],
    fallback: List[str],
    present: List[str],
) -> List[str]:
    """The section order to render, given a user override and the pipeline's own.

    *fallback* is whatever `tailor()` would have used without an override —
    the carried-forward order (#115) or a freshly ranked one. *present* is the
    section set this content actually has (`_expected_sections`).
    """
    if not override:
        return [k for k in fallback if k in set(present)]

    present_set = set(present)
    out: List[str] = []
    seen = set()
    for key in override:
        if isinstance(key, str) and key in present_set and key not in seen:
            out.append(key)
            seen.add(key)
    # Rule 2: a section the override predates is appended, never dropped.
    for key in fallback:
        if key in present_set and key not in seen:
            out.append(key)
            seen.add(key)
    return out


def resolve_skills(
    override: Optional[List[str]],
    ranked: List[Dict],
) -> List[Dict]:
    """Ranked skill dicts permuted into the user's order.

    *ranked* is `[{name, category, score}, ...]` from `_rank_skills`. The
    override carries names only — the score and category stay the ranker's, so
    an override reorders the section without falsifying what produced it.
    """
    if not override:
        return ranked

    by_name: Dict[str, Dict] = {}
    for item in ranked:
        key = _norm(item.get("name") if isinstance(item, dict) else None)
        if key and key not in by_name:
            by_name[key] = item

    out: List[Dict] = []
    used = set()
    for name in override:
        key = _norm(name)
        item = by_name.get(key)
        if item is not None and key not in used:
            out.append(item)
            used.add(key)
    for item in ranked:
        key = _norm(item.get("name") if isinstance(item, dict) else None)
        if key not in used:
            out.append(item)
            used.add(key)
    return out


def _match_index(target: str, candidates: List[str], claimed: set) -> Optional[int]:
    """Index of the unclaimed candidate that *target* refers to, or None.

    Exact normalized identity first. Falling back to token overlap is what lets
    an override survive a re-tailor that rewrote the bullet in place; the best
    unclaimed candidate wins and ties resolve to the lowest index, so the whole
    function stays deterministic.
    """
    norm_target = _norm(target)
    if not norm_target:
        return None
    for i, cand in enumerate(candidates):
        if i not in claimed and _norm(cand) == norm_target:
            return i

    target_tokens = _tokens(target)
    best_i, best_score = None, 0.0
    for i, cand in enumerate(candidates):
        if i in claimed:
            continue
        score = _jaccard(target_tokens, _tokens(cand))
        if score > best_score:
            best_i, best_score = i, score
    return best_i if best_score >= _REVISION_MATCH_MIN else None


def _reorder(order: List[str], bullets: List[str]) -> List[str]:
    """Bullets permuted into *order*, with unnamed bullets appended in place."""
    claimed: set = set()
    out: List[str] = []
    for wanted in order:
        idx = _match_index(wanted, bullets, claimed)
        if idx is not None:
            claimed.add(idx)
            out.append(bullets[idx])
    for i, bullet in enumerate(bullets):
        if i not in claimed:
            out.append(bullet)
    return out


# Which content list each drag-addressable section reads, and the key that
# identifies one of its items. Mirrors what the formatter renders as the
# heading, so the editor's parsed group label matches without translation:
# `\resumeSubheading{Title}` for experience, `\textbf{Name}` for projects.
_BULLET_SECTIONS = {
    "experience": ("experiences", "title"),
    "projects": ("projects", "name"),
}


def resolve_bullets(
    override: Optional[List[Dict]],
    content: Dict,
) -> Dict:
    """*content* with each overridden item's bullets permuted into the user's order.

    Returns a new dict; the input is not mutated. Each override group is
    `{"section": "experience"|"projects", "item": <heading label>, "order":
    [<bullet text>, ...]}`. Groups naming an item this run no longer has are
    dropped, which is the same rule sections and skills follow.
    """
    if not override:
        return content

    out = dict(content)
    for group in override:
        if not isinstance(group, dict):
            continue
        section = _BULLET_SECTIONS.get(group.get("section"))
        order = group.get("order")
        if not section or not isinstance(order, list):
            continue
        list_key, id_key = section
        items = out.get(list_key)
        if not isinstance(items, list):
            continue

        wanted = _norm(group.get("item"))
        rebuilt = []
        changed = False
        for item in items:
            if (
                isinstance(item, dict)
                and wanted
                and _norm(item.get(id_key)) == wanted
                and isinstance(item.get("bullets"), list)
            ):
                item = dict(item)
                item["bullets"] = _reorder(order, item["bullets"])
                changed = True
            rebuilt.append(item)
        if changed:
            out[list_key] = rebuilt
    return out


def apply_overrides(
    overrides: Optional[Dict],
    content: Dict,
    fallback_order: List[str],
    present: List[str],
) -> Dict:
    """Whole-content resolution: bullets permuted and `_section_order` settled.

    Skills are resolved separately by the caller, because the ranked list is
    computed on a different branch than the one that carries it forward.
    """
    overrides = overrides if isinstance(overrides, dict) else {}
    out = resolve_bullets(overrides.get("bullets"), content)
    out["_section_order"] = resolve_section_order(
        overrides.get("section_order"), fallback_order, present
    )
    return out
