"""Deterministic skill matching (issue #233). No model.

Two consumers share one definition of "this text names that skill":

- `SkillMatcher` finds a skill's name in free text (a bullet, a project
  description) for the knowledge graph's Skill -> Experience / Project links.
- `match_requirement_terms` maps a job posting's per-requirement keyword
  `terms` onto the user's skills, for ranking and for reporting what did not map.

Both apply the alias map (`agents/skill_postprocessor.SKILL_ALIASES`) on both
sides: a skill and the text spell it any way the map knows ("torch" and
"PyTorch"), and both reduce to one canonical name.

Model-free by rule (`tests/test_harness_boundary.py`).
"""

from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional, Pattern, Sequence, Set, Tuple

from agents.skill_postprocessor import SKILL_ALIASES, normalize_skill_name

# A name of at most this many characters (R, C, Go, TF) is a "short name". It is
# too easily a fragment or an English word to match case-insensitively, so it
# matches only in its own capitalization, as a whole token (see SkillMatcher).
SHORT_NAME_MAX = 2

# Short names that are also ordinary English words. These never match at the
# start of a sentence, where a capitalized "Go" is far more likely the verb.
_ENGLISH_WORDS = frozenset({"go"})

# Boundaries that treat `+`, `#` and `.` as part of a name. A match may not be
# preceded by a word character, `+`, `#` or `.`, nor followed by a word
# character or `+` / `#`, so Java is not inside JavaScript, C is not inside C++
# or C#, NET is not inside ".NET", and R is not inside "Research". A `.` after
# the name joins it to what follows only when a word character comes next, so
# "Node.js" is one token while the full stop in "used Python." is not part of
# "Python". A hyphen is not a name character ("Java-based" names Java).
_LEFT = r"(?<![\w+#.])"
_RIGHT = r"(?![\w+#])(?!\.\w)"

# `&` glues a letter to its neighbour in R&D and Q&A; a short name beside one
# is not the skill.
_SHORT_LEFT = r"(?<!&)"
_SHORT_RIGHT = r"(?!&)"

_SENTENCE_END = re.compile(r"(?:^\s*|[.!?:;]\s+|\n\s*)$")

_REVERSE: Optional[Dict[str, Set[str]]] = None


def _squash(text: str) -> str:
    return " ".join((text or "").split())


def _reverse_aliases() -> Dict[str, Set[str]]:
    """canonical name (lowercased) -> every alias key that maps onto it."""
    global _REVERSE
    if _REVERSE is None:
        rev: Dict[str, Set[str]] = {}
        for alias, canon in SKILL_ALIASES.items():
            rev.setdefault(canon.lower(), set()).add(alias)
        _REVERSE = rev
    return _REVERSE


def canonical_name(name: str) -> str:
    """The skill's canonical spelling: whitespace squashed, alias map applied."""
    return normalize_skill_name(_squash(name))


def canonical_key(name: str) -> str:
    """Lowercased canonical name, the identity two spellings compare on."""
    return canonical_name(name).lower()


def skill_forms(name: str) -> Tuple[Set[str], Set[str]]:
    """Every spelling that names this skill, as `(long, short)`.

    Long forms match case-insensitively; short forms (<= SHORT_NAME_MAX chars)
    match only in their own spelling. The skill's stored name and its canonical
    name are always included; alias spellings are included when they are long
    enough to be safe on their own ("torch" for PyTorch, but not "tf" for
    TensorFlow, which is also a fragment of TF-IDF).
    """
    stored = _squash(name)
    canon = canonical_name(name)
    long_forms: Set[str] = set()
    short_forms: Set[str] = set()

    def add(form: str, alias: bool = False) -> None:
        if not form:
            return
        if len(form) <= SHORT_NAME_MAX:
            if alias:
                return
            spellings = {form}
            if form.islower():
                # A skill stored as "go" is the language: accept "Go" and "GO"
                # but never the lowercase English word.
                spellings = {form.capitalize(), form.upper()}
            short_forms.update(spellings)
        else:
            long_forms.add(form.lower())

    add(stored)
    add(canon)
    for alias in _reverse_aliases().get(canon.lower(), ()):
        add(alias, alias=True)
    return long_forms, short_forms


def _form_pattern(form: str) -> str:
    parts = [p for p in re.split(r"[\s\-]+", form) if p]
    return r"[\s\-]+".join(re.escape(p) for p in parts)


class SkillMatcher:
    """Whether a text names one skill. Compile once, call `mentions` per text.

    The rule, in full:

    - Boundaries treat `+`, `#` and `.` as part of a name (`_LEFT`, `_RIGHT`).
    - Names longer than SHORT_NAME_MAX match case-insensitively; whitespace and
      hyphens inside a name are interchangeable ("scikit-learn", "scikit learn").
    - Short names (R, C, Go) match only in their own capitalization, never beside
      `&` (R&D), and "Go" never opens a sentence.
    - Every alias of the skill's canonical name matches too (`skill_forms`).
    """

    __slots__ = ("name", "_long", "_short")

    def __init__(self, name: str):
        self.name = name
        long_forms, short_forms = skill_forms(name)
        self._long: Optional[Pattern[str]] = None
        self._short: Optional[Pattern[str]] = None
        if long_forms:
            alts = "|".join(_form_pattern(f) for f in sorted(long_forms, key=lambda f: (-len(f), f)))
            self._long = re.compile(f"{_LEFT}(?:{alts}){_RIGHT}", re.IGNORECASE)
        if short_forms:
            alts = "|".join(_form_pattern(f) for f in sorted(short_forms))
            self._short = re.compile(f"{_LEFT}{_SHORT_LEFT}(?:{alts}){_SHORT_RIGHT}{_RIGHT}")

    def mentions(self, text: Optional[str]) -> bool:
        if not text:
            return False
        if self._long is not None and self._long.search(text):
            return True
        if self._short is not None:
            for m in self._short.finditer(text):
                if m.group(0).lower() in _ENGLISH_WORDS and _SENTENCE_END.search(text[:m.start()]):
                    continue
                return True
        return False


# ── requirement terms -> skills ──────────────────────────────────────────────

_TYPE_RANK = {"required": 0, "preferred": 1}


def _requirement_order(requirements: Sequence[Dict]) -> List[Tuple[int, Dict]]:
    """Required requirements first, then preferred; within a type the most
    critical first, then source order. Incidental ones take no part."""
    rows: List[Tuple[Tuple[int, int, int], int, Dict]] = []
    for i, r in enumerate(requirements or []):
        if not isinstance(r, dict) or r.get("type", "required") not in _TYPE_RANK:
            continue
        ordinal = r.get("ordinal")
        ordinal = ordinal if isinstance(ordinal, int) else i
        try:
            crit = int(r.get("criticality") if r.get("criticality") is not None else 3)
        except (TypeError, ValueError):
            crit = 3
        rows.append(((_TYPE_RANK[r.get("type", "required")], -crit, ordinal), ordinal, r))
    rows.sort(key=lambda t: t[0])
    return [(ordinal, r) for _, ordinal, r in rows]


def match_requirement_terms(skill_names: Iterable[str],
                            requirements: Optional[Sequence[Dict]]) -> Dict[str, List[Dict]]:
    """Map each required and preferred requirement's `terms` onto the user's skills.

    A term matches a skill when their canonical names are equal: exactly, or
    through the alias map ("torch" reaches the skill PyTorch, "pytorch" reaches a
    skill stored as "torch"). Nothing fuzzier is tried: a term that names no skill
    is reported in `unmatched` for the host to resolve, never guessed.

    Returns `{"matches": [...], "unmatched": [...]}`, both in priority order
    (required before preferred, then criticality, then posting order). A match is
    `{"skill", "term", "requirement", "type"}` where `requirement` is the
    requirement's ordinal; an unmatched term is `{"term", "requirement", "type"}`.
    A skill matched by several requirements appears once per (skill, requirement).
    """
    by_key: Dict[str, str] = {}
    for name in skill_names or ():
        if name:
            by_key.setdefault(canonical_key(name), name)
    matches: List[Dict] = []
    unmatched: List[Dict] = []
    seen_match: Set[Tuple[str, int]] = set()
    seen_unmatched: Set[str] = set()
    for ordinal, req in _requirement_order(requirements or []):
        rtype = req.get("type", "required")
        for term in req.get("terms") or []:
            key = canonical_key(str(term))
            if not key:
                continue
            skill = by_key.get(key)
            if skill is not None:
                if (skill, ordinal) not in seen_match:
                    seen_match.add((skill, ordinal))
                    matches.append({"skill": skill, "term": str(term), "requirement": ordinal,
                                    "type": rtype})
            elif key not in seen_unmatched:
                seen_unmatched.add(key)
                unmatched.append({"term": str(term), "requirement": ordinal, "type": rtype})
    return {"matches": matches, "unmatched": unmatched}


def priority_skills(matches: Sequence[Dict]) -> List[str]:
    """Matched skill names, each once, in priority order."""
    out: List[str] = []
    for m in matches:
        if m["skill"] not in out:
            out.append(m["skill"])
    return out
