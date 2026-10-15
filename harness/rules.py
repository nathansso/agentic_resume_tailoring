"""Document-level rules (issue #298): what the user said about the page, not an item on it.

A `ResumeRule` (`database.models.ResumeRule`) is typed after a linter's rule kinds, so each kind
has one defined enforcement point and nothing is a free-text instruction the host may forget:

| kind            | params                                                        | enforced as                         |
|-----------------|---------------------------------------------------------------|-------------------------------------|
| `limit`         | `pages`, `bullets_per_item`, `max_skills`, `max_bullet_lines`, | the `Finalize` defaults, which a    |
|                 | `<section>: 0` (leave a section off)                          | program may only tighten            |
| `section_order` | `order`: section names, first to last                         | the default `section_order`         |
| `style_note`    | none                                                          | pushed to the host, never blocks    |

**Strength.** 5 is a hard rule: a program that loosens it is refused (check `rules`, naming the
rule). 1-4 is soft and never a gate: a soft section exclusion, section order and skills limit are
defaults a program may override (`finalize.keep_sections`, its own order or skills list), and a
soft page, bullet-lines or bullets-per-item limit is only measured; the finalize result reports
`applied` or `not_applied` for each. (A soft limit that *widens* a built-in default, two pages
where one is built in, does move the default.)

**Scope precedence** (the user confirms this at review of #298): scopes are `global`,
`role_family` and `job`. A narrower scope may only *tighten* a hard rule. Among hard rules the
tightest value wins whatever its scope, and a looser one is reported `overridden`, naming the
rule that outranks it; `record_rule` refuses to store one (it could never take effect). Among
soft rules the narrower scope overrides the broader one, loosening included, and the newest of one
scope wins. A soft rule never loosens a hard one. A `section_order` rule has no "tighter", so a
narrower hard order may only extend a broader hard order (the broader one is its prefix).

**The user's editor layout outranks a rule's section order** (#277, `executor._apply_layout`): a
drag the user made in the editor is the user's own choice on this resume, so it is laid over the
plan's order after the rule's default and the plan's order were settled, and is never a
violation.

Pure: this module reads no store. `harness/tools.py::rules_for` scopes the rows, the executor
applies the result, `harness/acceptance.py::rule_violations` checks the page. Model-free by rule
(`tests/test_harness_boundary.py`).
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Literal, Optional, Sequence, Tuple

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from agents.arbitration import HARD_STRENGTH
from agents.checks import PAGE_LINE_BUDGET
from agents.preferences import SECTION_NAMES
from agents.skill_scorer import MAX_SKILLS, MIN_SKILLS

KINDS = ("limit", "section_order", "style_note")
SCOPES = ("global", "role_family", "job")
SCOPE_RANK = {"global": 0, "role_family": 1, "job": 2}
STATUS_ACTIVE, STATUS_SUPERSEDED, STATUS_RETRACTED = "active", "superseded", "retracted"
LIMIT_KEYS = ("pages", "bullets_per_item", "max_skills", "max_bullet_lines")
MAX_PAGES = 3

# The content key that holds each section's rows (`agents.checks.expected_sections` names the
# sections; the formatter reads the keys).
CONTENT_KEY = {"education": "education", "experience": "experiences", "projects": "projects",
               "skills": "skills_ranked", "achievements": "achievements"}


class RuleError(ValueError):
    """A rule that cannot be stored, as a message the host can act on."""


# ── params, per kind ─────────────────────────────────────────────────────────

class _P(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LimitParams(_P):
    pages: Optional[int] = Field(None, ge=1, le=MAX_PAGES, description="Page count.")
    bullets_per_item: Optional[int] = Field(None, ge=1, le=12,
                                            description="Most bullets under one role or project.")
    max_skills: Optional[int] = Field(None, ge=1, le=60, description="Most skills listed.")
    max_bullet_lines: Optional[int] = Field(None, ge=1, le=2,
                                            description="Most rendered lines in one bullet.")
    education: Optional[Literal[0]] = None
    experience: Optional[Literal[0]] = None
    projects: Optional[Literal[0]] = None
    skills: Optional[Literal[0]] = None
    achievements: Optional[Literal[0]] = None

    @model_validator(mode="after")
    def _shape(self):
        if not self.model_dump(exclude_none=True):
            raise ValueError("a limit needs at least one of pages, bullets_per_item, max_skills, "
                             "max_bullet_lines, or <section>: 0")
        if self.skills == 0 and self.max_skills is not None:
            raise ValueError("skills: 0 leaves the skills section off; max_skills would count "
                             "skills that are not there")
        return self


class SectionOrderParams(_P):
    order: List[Literal["education", "experience", "projects", "skills", "achievements"]] = Field(
        min_length=1, description="Section names, first to last.")

    @model_validator(mode="after")
    def _unique(self):
        if len(set(self.order)) != len(self.order):
            raise ValueError("order names a section twice")
        return self


class StyleNoteParams(_P):
    pass


_PARAMS = {"limit": LimitParams, "section_order": SectionOrderParams, "style_note": StyleNoteParams}

SHAPES = {
    "limit": '{"pages": 1} | {"bullets_per_item": 3} | {"max_skills": 12} | {"max_bullet_lines": 1} '
             '| {"projects": 0} (a section name set to 0 leaves it off: ' + ", ".join(SECTION_NAMES) + ")",
    "section_order": '{"order": ["skills", "experience", "projects"]}: the named sections first, '
                     "in that order; the rest follow",
    "style_note": "{} (the words are in `text`)",
}


def normalize_params(kind: str, params: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """`params` validated for `kind`, as stored (unset fields dropped). Raises `RuleError`."""
    if kind not in _PARAMS:
        raise RuleError(f"kind must be one of {', '.join(KINDS)}.")
    try:
        return _PARAMS[kind].model_validate(params or {}).model_dump(exclude_none=True)
    except ValidationError as exc:
        detail = "; ".join(f"{'.'.join(str(p) for p in e['loc']) or 'params'}: {e['msg']}"
                           for e in exc.errors())
        raise RuleError(f"params for a {kind} rule are invalid ({detail}). Shape: {SHAPES[kind]}") from None


# ── words to params: only the obvious, deterministic cases ───────────────────

_NUMBER = {"one": 1, "1": 1, "single": 1, "two": 2, "2": 2, "three": 3, "3": 3}
_PAGES = re.compile(r"\b(?P<n>one|two|three|single|1|2|3)[\s-]+pages?\b", re.IGNORECASE)
_DROP = re.compile(r"\b(?:no|without|skip|drop|omit|remove|exclude|hide|delete|leave\s+(?:off|out)|"
                   r"don['’]?t\s+(?:include|show|list|add|want))\b", re.IGNORECASE)
_SECTION_WORD = re.compile(
    r"\b(?P<name>projects?|(?:work\s+)?experiences?|education|skills?|achievements?|awards?|honou?rs)"
    r"\s+section\b", re.IGNORECASE)
_NEGATED = re.compile(r"\b(?:don['’]?t|do\s+not|doesn['’]?t|isn['’]?t|aren['’]?t|no\s+need|"
                      r"not\s+required|not\s+necessary)\b", re.IGNORECASE)
# A negator within two words before a drop verb: "don't skip", "never drop my", "do not remove the".
_NEGATED_DROP = re.compile(
    r"\b(?:don\W?t|do\s+not|doesn\W?t|never|not|won\W?t|shouldn\W?t|can\W?t|cannot|"
    r"no\s+need\s+to)\s+(?:\w+\s+){0,2}?"
    r"(?:skip|drop|omit|remove|exclude|hide|delete|leave\s+(?:off|out))\b",
    re.IGNORECASE)
_SECTION_OF = {"project": "projects", "projects": "projects", "experience": "experience",
               "experiences": "experience", "education": "education", "skill": "skills",
               "skills": "skills", "achievement": "achievements", "achievements": "achievements",
               "award": "achievements", "awards": "achievements", "honor": "achievements",
               "honors": "achievements", "honour": "achievements", "honours": "achievements"}


def infer_limit(text: str, target: Optional[str] = None,
                polarity: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The `limit` params the user's words state outright, or None.

    Only the cases with one plain reading: "one page" / "two pages" / "single page", and "no
    <section> section" / "skip <section>" (also a `section:<name>` target that is suppressed).
    Anything else, two readings in one sentence, or a negated phrasing returns None, so the caller
    asks for explicit params rather than guessing a number."""
    text = " ".join((text or "").split())
    target = " ".join((target or "").split())
    found: List[Dict[str, Any]] = []

    # A section is left off only when the user said to drop it. A negated drop ("don't skip the
    # projects section", "never drop my experience section") says the opposite, and an `emphasize`
    # or `reframe` preference is never an exclusion: either way the host is asked for params.
    drops = polarity in (None, "suppress") and not _NEGATED_DROP.search(text)
    m = re.match(r"^section\s*:\s*(\w+)$", target, re.IGNORECASE)
    if m and polarity == "suppress" and drops and m.group(1).lower() in SECTION_NAMES:
        found.append({m.group(1).lower(): 0})
    elif not m and drops:
        for hit in _SECTION_WORD.finditer(text):
            before = text[:hit.start()]
            name = _SECTION_OF.get(re.sub(r"^work\s+", "", hit.group("name").lower()))
            if name and _DROP.search(before + " " + hit.group(0)):
                found.append({name: 0})
    elif not m and _SECTION_WORD.search(text):
        return None                    # a section is named, and the words do not drop it
    pages = {_NUMBER[p.group("n").lower()] for p in _PAGES.finditer(text)}
    if pages and _NEGATED.search(text):
        return None                    # "don't keep it to one page" is not a limit
    if len(pages) == 1:
        found.append({"pages": pages.pop()})
    elif len(pages) > 1:
        return None
    merged: Dict[str, Any] = {}
    for part in found:
        merged.update(part)
    if len(found) != 1 or not merged:
        return None
    try:
        return normalize_params("limit", merged)
    except RuleError:
        return None


# ── scope ────────────────────────────────────────────────────────────────────

def _norm(value: Any) -> str:
    return str(value or "").strip().lower()


def in_scope(rules: Sequence[Dict[str, Any]], job_id: Optional[str] = None,
             role_family: Optional[str] = None) -> List[Dict[str, Any]]:
    """The active rules that bind on this job: global ones, the family's, the job's. The same
    filter `agents.preferences.preferences_in_scope` applies to preferences. Sorted by
    `rule_id`, so the result does not depend on row order."""
    job, family = _norm(job_id), _norm(role_family)
    out = []
    for r in rules or []:
        if (r.get("status") or STATUS_ACTIVE) != STATUS_ACTIVE:
            continue
        scope, value = r.get("scope_type") or "global", _norm(r.get("scope_value"))
        if scope == "global" or (scope == "role_family" and family and value == family) \
                or (scope == "job" and job and value == job):
            out.append(r)
    return sorted(out, key=lambda r: str(r.get("rule_id") or ""))


def is_hard(rule: Dict[str, Any]) -> bool:
    return (rule.get("strength") or 0) >= HARD_STRENGTH


def _rank(rule: Dict[str, Any]) -> int:
    return SCOPE_RANK.get(rule.get("scope_type") or "global", 0)


def _stamp(rule: Dict[str, Any]) -> str:
    return str(rule.get("created_at") or "")


def _ref(rule: Dict[str, Any]) -> Dict[str, Any]:
    return {"rule_id": str(rule.get("rule_id")), "kind": rule.get("kind"), "text": rule.get("text"),
            "strength": rule.get("strength"), "scope_type": rule.get("scope_type") or "global",
            "scope_value": rule.get("scope_value")}


def describe(rule: Dict[str, Any]) -> str:
    """The rule as one clause for a refusal: its words and its id."""
    return f"{str(rule.get('text'))!r} (rule {str(rule.get('rule_id'))[:8]})"


def _prefix(short: Sequence[str], long: Sequence[str]) -> bool:
    return list(long[:len(short)]) == list(short)


# ── resolution: many rules, one effective set ────────────────────────────────

def resolve(rules: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """The effective rules from the in-scope active `rules` (see the module docstring).

    `{"limits": {key: {"hard": ref+value | None, "soft": ref+value | None}}, "order": {"hard",
    "soft"}, "notes": [...], "overridden": [{rule, key, by, reason}]}`. `hard` is the value a
    program may not loosen; `soft` the default it may override. A rule that cannot take effect
    is listed under `overridden` with the rule that outranks it."""
    limit_rules = [r for r in rules if r.get("kind") == "limit"]
    out: Dict[str, Any] = {"limits": {}, "order": {"hard": None, "soft": None}, "notes": [],
                           "overridden": []}

    def over(rule, key, by, reason):
        out["overridden"].append({"rule": _ref(rule), "key": key, "by": _ref(by), "reason": reason})

    keys = sorted({k for r in limit_rules for k in (r.get("params") or {})},
                  key=lambda k: (k not in LIMIT_KEYS, LIMIT_KEYS.index(k) if k in LIMIT_KEYS else 0, k))
    for key in keys:
        cands = [r for r in limit_rules if key in (r.get("params") or {})]
        hard = sorted((r for r in cands if is_hard(r)),
                      key=lambda r: (r["params"][key], -_rank(r), _stamp(r), str(r.get("rule_id"))))
        soft = [r for r in cands if not is_hard(r)]
        h = hard[0] if hard else None
        for r in hard[1:]:
            # A looser rule at a broader scope is just not the tightest: nothing to report.
            if r["params"][key] > h["params"][key] and _rank(r) >= _rank(h):
                over(r, key, h, "a narrower scope may only tighten a hard rule" if _rank(r) > _rank(h)
                     else "a tighter hard rule outranks it")
        s = max(soft, key=lambda r: (_rank(r), _stamp(r), str(r.get("rule_id")))) if soft else None
        if s is not None and h is not None and s["params"][key] > h["params"][key]:
            over(s, key, h, "a soft rule never loosens a hard one")
            s = None
        for r in soft:
            if r is not s and s is not None:
                over(r, key, s, "a soft rule at a narrower scope, or a newer one, overrides it")
        out["limits"][key] = {
            "hard": {**_ref(h), "value": h["params"][key]} if h else None,
            "soft": {**_ref(s), "value": s["params"][key]} if s else None}

    orders = [r for r in rules if r.get("kind") == "section_order"]
    cur = None
    for r in sorted((r for r in orders if is_hard(r)),
                    key=lambda r: (_rank(r), _stamp(r), str(r.get("rule_id")))):
        if cur is None or _prefix(cur["params"]["order"], r["params"]["order"]):
            cur = r
        else:
            over(r, "order", cur, "a narrower scope may only tighten a hard rule: its order must "
                                  "begin with the broader rule's")
    soft_orders = [r for r in orders if not is_hard(r)]
    s = max(soft_orders, key=lambda r: (_rank(r), _stamp(r), str(r.get("rule_id")))) if soft_orders else None
    if s is not None and cur is not None and not _prefix(cur["params"]["order"], s["params"]["order"]):
        over(s, "order", cur, "a soft rule never loosens a hard one")
        s = None
    for r in soft_orders:
        if r is not s and s is not None:
            over(r, "order", s, "a soft rule at a narrower scope, or a newer one, overrides it")
    out["order"] = {"hard": {**_ref(cur), "value": cur["params"]["order"]} if cur else None,
                    "soft": {**_ref(s), "value": s["params"]["order"]} if s else None}

    notes = [r for r in rules if r.get("kind") == "style_note"]
    out["notes"] = [_ref(r) for r in sorted(
        notes, key=lambda r: (-(r.get("strength") or 0), -_rank(r), _stamp(r), str(r.get("rule_id"))))]
    return out


def active_view(resolved: Dict[str, Any]) -> Dict[str, Any]:
    """What the host is shown of the effective rules (briefing, pins, finalize): hard rules and
    style notes verbatim, soft rules marked as defaults, and what could not take effect."""
    hard: List[Dict[str, Any]] = []
    soft: List[Dict[str, Any]] = []
    for key, slot in resolved["limits"].items():
        for bucket, entry in ((hard, slot["hard"]), (soft, slot["soft"])):
            if entry:
                bucket.append({**{k: entry[k] for k in ("rule_id", "kind", "text", "strength",
                                                         "scope_type", "scope_value")},
                               "param": key, "value": entry["value"]})
    for bucket, entry in ((hard, resolved["order"]["hard"]), (soft, resolved["order"]["soft"])):
        if entry:
            bucket.append({**{k: entry[k] for k in ("rule_id", "kind", "text", "strength",
                                                     "scope_type", "scope_value")},
                           "param": "order", "value": entry["value"]})
    return {"hard": hard, "soft": soft, "notes": resolved["notes"], "overridden": resolved["overridden"]}


# ── recording ────────────────────────────────────────────────────────────────

def identity(rule: Dict[str, Any]) -> Tuple:
    """What makes a new rule *the same rule* as an old one, so recording it supersedes: its kind,
    its scope, and for a limit the set of things it limits, for a note its words."""
    kind = rule.get("kind")
    detail: Any = ()
    if kind == "limit":
        detail = tuple(sorted((rule.get("params") or {})))
    elif kind == "style_note":
        detail = " ".join(str(rule.get("text") or "").lower().split())
    return (kind, rule.get("scope_type") or "global", _norm(rule.get("scope_value")) or None, detail)


def loosens_hard(candidate: Dict[str, Any], active: Sequence[Dict[str, Any]],
                 family: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The broader hard rule `candidate` would loosen, or None (the scope-precedence rule).

    A candidate at `role_family` scope is compared with the global rules; at `job` scope with the
    global rules and, when the job's `family` is known, that family's. A rule at the same scope
    with the same identity is the one being replaced, so it does not count."""
    scope = candidate.get("scope_type") or "global"
    mine = SCOPE_RANK.get(scope, 0)

    def broader(r):
        if r.get("status") not in (None, STATUS_ACTIVE) or not is_hard(r):
            return False
        if SCOPE_RANK.get(r.get("scope_type") or "global", 0) >= mine:
            return False
        return (r.get("scope_type") == "global"
                or (r.get("scope_type") == "role_family" and family
                    and _norm(r.get("scope_value")) == _norm(family)))

    for b in sorted((r for r in active if broader(r)), key=lambda r: str(r.get("rule_id"))):
        if b.get("kind") != candidate.get("kind"):
            continue
        if candidate["kind"] == "limit":
            for key, value in (candidate.get("params") or {}).items():
                if key in (b.get("params") or {}) and value > b["params"][key]:
                    return b
        elif candidate["kind"] == "section_order":
            if not _prefix(b["params"]["order"], candidate["params"]["order"]):
                return b
    return None


# ── the effective finalize ───────────────────────────────────────────────────

_FIELDS = (
    # (finalize field, limit key, key value -> field value, the built-in default or None)
    ("line_budget", "pages", lambda v: float(v) * PAGE_LINE_BUDGET, float(PAGE_LINE_BUDGET)),
    ("max_skills", "max_skills", int, MAX_SKILLS),
    ("max_bullet_lines", "max_bullet_lines", int, 2),
    ("bullets_per_item", "bullets_per_item", int, None),
)


def _slot(resolved: Dict[str, Any], key: str) -> Tuple[Optional[Dict], Optional[Dict]]:
    slot = resolved["limits"].get(key) or {}
    return slot.get("hard"), slot.get("soft")


def effective_finalize(fin: Dict[str, Any], resolved: Dict[str, Any],
                       expected: Sequence[str] = ()) -> Dict[str, Any]:
    """The program's `finalize` settled against the effective rules.

    `fin` is the program's `finalize` with `None` for what it did not set. Returns `values` (the
    numbers to enforce), `excluded` (section -> the rule that leaves it off), `order` (the default
    order, or None), `violations` (a hard rule the program loosens), `soft` (a soft rule the
    program overrides or leaves applied, as entries for the finalize report) and `errors` (a
    value past anything a rule allows, which is an invalid program as it always was)."""
    values: Dict[str, Any] = {}
    violations: List[Dict[str, Any]] = []
    soft: List[Dict[str, Any]] = []
    errors: List[str] = []
    applied: List[Dict[str, Any]] = []

    for field, key, convert, builtin in _FIELDS:
        hard, softer = _slot(resolved, key)
        h = convert(hard["value"]) if hard else None
        s = convert(softer["value"]) if softer else None
        p = fin.get(field)
        caps = [c for c in (builtin, h, s) if c is not None]
        if p is not None and h is None and builtin is not None and p > max(caps):
            errors.append(f"finalize.{field}={p} is above the most this plan may use ({max(caps)}).")
        # The enforced default is the hard rule's value; a soft rule only moves it when it
        # *widens* the built-in default (the user said two pages are fine). A soft rule that is
        # tighter is never a gate: it is reported, by the executor, from what the page measures.
        widen = s is not None and builtin is not None and s > builtin
        default = h if h is not None else (s if widen else builtin)
        value = p if p is not None else default
        if h is not None and value is not None and value > h:
            violations.append({
                "check": "rules", "rule_id": hard["rule_id"],
                "detail": f"finalize.{field}={p} loosens {describe(hard)}: at most {h}",
                "hint": f"leave finalize.{field} unset, or set it to {h} or lower; changing the rule "
                        "is the user's call (record_rule / retract_rule)"})
            value = h
        values[field] = value
        for entry, strength_hard in ((hard, True), (softer, False)):
            if not entry:
                continue
            bound_value = convert(entry["value"])
            record = {"rule_id": entry["rule_id"], "kind": "limit", "text": entry["text"],
                      "strength": entry["strength"], "param": key, "value": entry["value"]}
            if strength_hard or (widen and p is None):
                applied.append(record)
            if not strength_hard:
                soft.append({**record, "source": "rule", "field": field, "bound": bound_value})

    # The floor never exceeds a cap that will be applied, soft or hard: a skills limit that cut the
    # default list must not be what trips `skills_floor`.
    min_skills = fin.get("min_skills")
    caps = [values["max_skills"]] + [e["value"] for e in _slot(resolved, "max_skills") if e]
    values["min_skills"] = min(MIN_SKILLS if min_skills is None else min_skills, *caps)

    excluded: Dict[str, Dict[str, Any]] = {}
    keep = {str(s) for s in fin.get("keep_sections") or []}
    for section in SECTION_NAMES:
        hard, softer = _slot(resolved, section)
        for entry, strength_hard in ((hard, True), (softer, False)):
            if not entry:
                continue
            record = {"rule_id": entry["rule_id"], "kind": "limit", "text": entry["text"],
                      "strength": entry["strength"], "param": section, "value": 0}
            if section in keep and strength_hard:
                violations.append({
                    "check": "rules", "rule_id": entry["rule_id"],
                    "detail": f"finalize.keep_sections names {section}, which {describe(entry)} leaves off",
                    "hint": f"drop {section} from keep_sections; only the user can change the rule"})
            if section in keep and not strength_hard:
                soft.append({**record, "source": "rule", "status": "not_applied",
                             "reason": f"the plan keeps {section} (finalize.keep_sections)"})
                continue
            excluded.setdefault(section, entry)
            if strength_hard:
                applied.append(record)
            else:
                soft.append({**record, "source": "rule", "status": "applied"})
                applied.append(record)

    return {"values": values, "excluded": excluded, "violations": violations, "soft": soft,
            "errors": errors, "applied": applied, "order": default_order(resolved, expected)}


# ── section order ────────────────────────────────────────────────────────────

def default_order(resolved: Dict[str, Any], expected: Sequence[str]) -> Optional[List[str]]:
    """The section order the rules set for a page whose sections are `expected`, or None: the
    rule's sections (those the page has) first in its order, then the others as given. A soft
    rule that is tighter than the hard one is the default; else the hard rule's."""
    if not expected:
        return None
    entry = resolved["order"]["soft"] or resolved["order"]["hard"]
    if not entry:
        return None
    first = [s for s in entry["value"] if s in expected]
    return first + [s for s in expected if s not in first]


def order_check(program_order: Optional[Sequence[str]], resolved: Dict[str, Any],
                expected: Sequence[str]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """`(violations, soft entries)` for a plan's own `section_order` against the rules. A plan
    that sets none is on the default and passes. A hard rule needs the plan's order to begin with
    the rule's sections that the page has; a soft rule is reported `not_applied` instead."""
    violations: List[Dict[str, Any]] = []
    soft: List[Dict[str, Any]] = []
    for strength_hard, entry in ((True, resolved["order"]["hard"]), (False, resolved["order"]["soft"])):
        if not entry:
            continue
        want = [s for s in entry["value"] if s in expected]
        ok = program_order is None or _prefix(want, list(program_order))
        if ok:
            if not strength_hard:
                soft.append({"rule_id": entry["rule_id"], "kind": "section_order", "text": entry["text"],
                             "strength": entry["strength"], "param": "order", "value": entry["value"],
                             "source": "rule", "status": "applied"})
            continue
        if strength_hard:
            violations.append({
                "check": "rules", "rule_id": entry["rule_id"],
                "detail": f"section_order {list(program_order)} does not begin with {want}, which "
                          f"{describe(entry)} requires",
                "hint": f"begin section_order with {want}, or omit it to take the rule's order. "
                        "The user's editor layout still outranks the rule"})
        else:
            soft.append({"rule_id": entry["rule_id"], "kind": "section_order", "text": entry["text"],
                         "strength": entry["strength"], "param": "order", "value": entry["value"],
                         "source": "rule", "status": "not_applied",
                         "reason": f"the plan sets section_order {list(program_order)}"})
    return violations, soft


__all__ = ["CONTENT_KEY", "KINDS", "LIMIT_KEYS", "MAX_PAGES", "RuleError", "SCOPES", "SCOPE_RANK",
           "SHAPES", "STATUS_ACTIVE", "STATUS_RETRACTED", "STATUS_SUPERSEDED", "active_view",
           "default_order", "describe", "effective_finalize", "identity", "in_scope", "infer_limit",
           "is_hard", "loosens_hard", "normalize_params", "order_check", "resolve"]
