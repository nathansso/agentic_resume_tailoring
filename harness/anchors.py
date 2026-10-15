"""The preference anchor resolver (#295): what a preference's subject *denotes*.

One deterministic, model-free decision, in one place. Given the user's resume items and a subject
("Kafka", "torch", "section: projects", "keep it to one page"), `resolve` says where the preference
attaches:

| status      | meaning                                                                          |
|-------------|----------------------------------------------------------------------------------|
| `bound`     | exactly one item: `key` is its planner key (`exp:` `proj:` `skill:` `edu:` `ach:`) |
| `ambiguous` | several items at the same step: `candidates` lists them, none is picked           |
| `pending`   | a nameable thing the graph does not hold (yet): stored with `target_term` only    |
| `document`  | about the page (`section:`, length, order, tone, wording, summary), not an item   |
| `multiple`  | several subjects ("Kafka or Spark"): refused, the user splits it                  |

**The steps, in order.** The first step that matches anything decides; a step with one match binds,
a step with several is ambiguous and never picks one.

1. the exact key (`skill:python`, `exp:data science intern|idx exchange`);
2. the label (`skill: Python`);
3. the bare name, whole-string and case-insensitive (`Python`; a role by `title @ company` or its
   title; a degree by its institution or its degree name). A company alone is not a role's name:
   "IDX Exchange" is a thing several items may mention, so it stays pending (a term pin, which
   catches every mention) and is not bound to one role, whose item pin would not;
4. an alias, skills only: the subject and the skill reduce to one canonical name through
   `agents/skill_postprocessor.SKILL_ALIASES` (`torch` is PyTorch), or the subject is one of the
   spellings `agents/skill_matching.skill_forms` knows for it.

**A substring never binds.** "Kafka" is not "Kafka Streams", "R" is not "React", "Go" is not
"Google": every step compares the whole subject. Short names (at most `SHORT_NAME_MAX` characters:
R, C, Go) bind only by their own name, never through an alias, as in `skill_forms`.

`mentions` is the same decision run on a whole message, which is what the memory gate's automatic
write is checked against (`harness/memory.py::_auto_write`): the gate may write only a key this
module also finds in the message.

Nothing here enforces anything. Enforcement is `harness/decisions/negative_pins.py::pin_target`
and the executor's gates; this module only decides which key (if any) a preference carries.

Model-free by rule (`tests/test_harness_boundary.py`).
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Union
from uuid import UUID

import services
from agents.checks import exp_key, proj_key
from agents.skill_matching import SHORT_NAME_MAX, SkillMatcher, canonical_key, skill_forms
from agents.preferences import SECTION_NAMES
from harness.tools import ach_key, edu_key, skill_key

BOUND, AMBIGUOUS, PENDING, DOCUMENT, MULTIPLE = "bound", "ambiguous", "pending", "document", "multiple"
STATUSES = (BOUND, AMBIGUOUS, PENDING, DOCUMENT, MULTIPLE)

STEPS = ("key", "label", "name", "alias")

# A subject about the page and not about a thing on it. Whole words, lowercased.
DOCUMENT_WORDS = frozenset(
    "page pages length order ordering tone wording summary layout format formatting font fonts "
    "margin margins spacing style voice phrasing".split())

# What joins two subjects. `/`, `&` and `+` count only with spaces around them ("R&D", "C/C++" and
# "Q&A" are one name each). "and" is the exception: it is also inside names ("Research and
# Development"), so it splits only when every piece is something on its own (`_subjects`).
_SPLIT = re.compile(r"\s*(?:,|;|\s&\s|\s/\s|\s\+\s|\band/or\b|\bor\b)\s*", re.IGNORECASE)
_AND = re.compile(r"\s+and\s+", re.IGNORECASE)
_SECTION = re.compile(r"^section\s*:\s*", re.IGNORECASE)
_PREFIX_KEY = re.compile(r"^(exp|proj|skill|edu|ach|section):", re.IGNORECASE)
_COMPANY_STOP = frozenset("the of and for inc llc ltd corp co company at a an".split())

Source = Union[UUID, str, Sequence[Dict[str, Any]]]


def _norm(text: Any) -> str:
    return " ".join(str(text or "").split()).lower()


def _loose(text: Any) -> str:
    """Case, whitespace and hyphens folded: the alias step compares "scikit-learn" and "scikit learn"."""
    return " ".join(re.sub(r"[-_]+", " ", str(text or "")).split()).lower()


# ── the items ────────────────────────────────────────────────────────────────

def _item(kind: str, key: str, label: str, names: Iterable[str], skill: Optional[str] = None,
          title: str = "", company: str = "") -> Dict[str, Any]:
    seen: List[str] = []
    for n in names:
        n = _norm(n)
        if n and n not in seen:
            seen.append(n)
    return {"kind": kind, "key": key, "label": label, "names": seen, "skill": skill,
            "title": title, "company": company}


def build_items(skills: Sequence[Dict] = (), experiences: Sequence[Dict] = (), projects: Sequence[Dict] = (),
                education: Sequence[Dict] = (), achievements: Sequence[Dict] = ()) -> List[Dict[str, Any]]:
    """The resolvable items from the records `services.get_*` return, one entry per key. Each is
    `{kind, key, label, names, skill, title, company}`: `names` are the whole-string spellings step 3 accepts."""
    out: List[Dict[str, Any]] = []
    for s in skills:
        name = str(s.get("name") or "").strip()
        if name:
            out.append(_item("skill", skill_key(s), f"skill: {name}", [name], skill=name))
    for e in experiences:
        title, company = str(e.get("title") or "").strip(), str(e.get("company") or "").strip()
        if title:
            both = f"{title} @ {company}" if company else title
            out.append(_item("experience", exp_key(e), f"experience: {both}", [both, title],
                             title=title, company=company))
    for p in projects:
        name = str(p.get("name") or "").strip()
        if name:
            out.append(_item("project", proj_key(p), f"project: {name}", [name]))
    for e in education:
        inst = str(e.get("institution") or "").strip()
        degree = "" if e.get("degree") in (None, "—") else str(e.get("degree")).strip()
        if inst:
            both = f"{inst} — {degree}" if degree else inst
            out.append(_item("education", edu_key(e), f"education: {both}", [both, inst, degree]))
    for a in achievements:
        title = str(a.get("title") or "").strip()
        if title:
            out.append(_item("achievement", ach_key(a), f"achievement: {title}", [title]))
    seen, unique = set(), []
    for item in out:                                  # a repeated key is one item
        if item["key"] not in seen:
            seen.add(item["key"])
            unique.append(item)
    return unique


def items_for(user_id: UUID) -> List[Dict[str, Any]]:
    """The user's resolvable items, read through `services`."""
    return build_items(
        skills=services.get_skills(user_id), experiences=services.get_experiences(user_id),
        projects=services.get_projects(user_id), education=services.get_education(user_id),
        achievements=services.get_achievements(user_id))


def _items(source: Source) -> List[Dict[str, Any]]:
    if isinstance(source, (UUID, str)):
        return items_for(source if isinstance(source, UUID) else UUID(str(source)))
    return list(source)


def _candidate(item: Dict[str, Any]) -> Dict[str, str]:
    return {"key": item["key"], "label": item["label"], "kind": item["kind"]}


# ── the subject ──────────────────────────────────────────────────────────────

def _alias_match(subject: str, item: Dict[str, Any]) -> bool:
    """Whether `subject` is a spelling of this skill: one canonical name, or a form `skill_forms` knows."""
    name = item.get("skill")
    if not name or len(subject) <= SHORT_NAME_MAX:
        return False
    if canonical_key(subject) == canonical_key(name):
        return True
    long_forms, _ = skill_forms(name)
    return _loose(subject) in {_loose(f) for f in long_forms}


def _step_matches(step: str, subject: str, items: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    want = _norm(subject)
    if step == "key":
        return [i for i in items if i["key"].lower() == want]
    if step == "label":
        return [i for i in items if _norm(i["label"]) == want]
    if step == "name":
        return [i for i in items if want in i["names"]]
    return [i for i in items if _alias_match(subject.strip(), i)]


def _is_document(subject: str) -> bool:
    words = re.findall(r"[a-z]+", subject.lower())
    return bool(words) and any(w in DOCUMENT_WORDS for w in words)


def _is_known(piece: str, items: Sequence[Dict[str, Any]]) -> bool:
    """Whether `piece` is, on its own, an item (bound or ambiguous), a section or a document subject."""
    piece = piece.strip()
    return (any(_step_matches(step, piece, items) for step in STEPS) or piece.lower() in SECTION_NAMES
            or bool(_SECTION.match(piece)) or _is_document(piece))


def _subjects(subject: str, items: Sequence[Dict[str, Any]]) -> List[str]:
    """The subjects a phrase states. `or`, `and/or`, commas, semicolons and spaced `/`, `&`, `+` always
    separate; `and` separates only when every piece around it is known on its own, so "Kafka and
    PyTorch" (both held) is two and "Research and Development" (neither held) is one."""
    out: List[str] = []
    for part in (p for p in _SPLIT.split(subject) if p.strip()):
        pieces = [p for p in _AND.split(part) if p.strip()]
        if len(pieces) > 1 and all(_is_known(p, items) for p in pieces):
            out.extend(p.strip() for p in pieces)
        else:
            out.append(part.strip())
    return out


def resolve(source: Source, subject: Optional[str]) -> Dict[str, Any]:
    """Where a preference about `subject` attaches. `source` is a user id (the items are read from
    the store) or a list of items from `build_items`. See the module docstring.

    Always `{status, subject}`, plus `key` and `step` when bound (also `key` for a document subject
    that is a section, `section:<name>`), `candidates` and `step` when ambiguous, `subjects` when
    multiple."""
    subject = " ".join(str(subject or "").split())
    out: Dict[str, Any] = {"status": PENDING, "subject": subject}
    if not subject:
        return out
    if _SECTION.match(subject):
        name = _SECTION.sub("", subject).strip().lower()
        out["status"] = DOCUMENT
        if name in SECTION_NAMES:
            out["key"] = f"section:{name}"
        return out

    items = _items(source)
    for step in STEPS:
        found = _step_matches(step, subject, items)
        if len(found) == 1:
            return {**out, "status": BOUND, "key": found[0]["key"], "step": step}
        if len(found) > 1:
            return {**out, "status": AMBIGUOUS, "step": step, "candidates": [_candidate(i) for i in found]}

    name = subject.lower()
    if name in SECTION_NAMES:                         # a bare section name: "achievements"
        return {**out, "status": DOCUMENT, "key": f"section:{name}"}
    parts = _subjects(subject, items)
    if len(parts) > 1:
        if all(_is_document(p) for p in parts):
            return {**out, "status": DOCUMENT}
        return {**out, "status": MULTIPLE, "subjects": parts}
    if _is_document(subject):
        return {**out, "status": DOCUMENT}
    return out


# ── a message ────────────────────────────────────────────────────────────────

def _phrase(words: str) -> "re.Pattern":
    return re.compile(r"(?<![a-z0-9])" + re.escape(_norm(words)) + r"(?:s|es)?(?![a-z0-9])")


def _first_word(company: str) -> str:
    return next((w for w in re.findall(r"[a-z0-9]+", company.lower()) if w not in _COMPANY_STOP), "")


def mentions(source: Source, text: str) -> Dict[str, Any]:
    """The items a message names in words, as a resolution of the message.

    `bound` with `key` when exactly one item is named, `ambiguous` with `candidates` when several are,
    `pending` when none is. Items are matched as `resolve` matches a subject, but inside free text:
    a skill by `SkillMatcher` (aliases, short names in their own capitalization), a project, degree
    or award by its whole name, a role by its title or its company (whole name or first word). A
    name inside a longer one ("Kafka" in "Kafka Streams") is not told apart: both are named, so the
    message is ambiguous and a caller that needs one key asks the user."""
    hay = _norm(text)
    named: List[Dict[str, Any]] = []
    for item in _items(source):
        if item["kind"] == "skill":
            hit = SkillMatcher(item["skill"]).mentions(text)
        elif item["kind"] == "experience":
            company = item.get("company") or ""
            hit = any(_phrase(n).search(hay)
                      for n in (item.get("title"), company, _first_word(company)) if n)
        else:
            hit = any(_phrase(n).search(hay) for n in item["names"])
        if hit:
            named.append(item)
    if len(named) == 1:
        return {"status": BOUND, "key": named[0]["key"], "step": "name", "subject": _norm(text)}
    if named:
        return {"status": AMBIGUOUS, "step": "name", "subject": _norm(text),
                "candidates": [_candidate(i) for i in named]}
    return {"status": PENDING, "subject": _norm(text)}
