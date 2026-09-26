"""The per-metric acceptance rule (issue #113, as specified in docs/harness.md § 5).

Tailoring metrics stay separate and are never combined into one number. Each
has a role:

- **Hard gates** (`preferences`, `faithfulness`, `bullet_lines`): an action may
  not *introduce* a violation. Gates are compared as sets, so a parent that
  already violates one does not block every later node; the finalize step is
  where remaining violations stop a commit.
- **Guards** (`stuffing`, `verb_entropy`, `mtld`, `duplication`): may not
  regress by more than a tolerance. A node may tighten a tolerance, never
  loosen one (`harness/program.py` enforces that).
- **Targets** (`coverage`, `relevance_density`): an action must improve at
  least one, unless it is a delete the user or a preference asked for.
- **Report only** (`ats`): the ATS composite is monotone non-decreasing in
  text (#127), so it is shown and never decides anything.

Deterministic: every float is rounded, every list sorted. Model-free by rule
(`tests/test_harness_boundary.py`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set

from agents.ats_scorer import ATSScoringEngine
from agents.checks import (
    bullet_line_violations, exp_key, faithfulness_drift, proj_key, relevance_density,
)
from agents.redundancy import (
    bullet_texts as all_bullets, bullet_tokens, leading_verb_entropy, mtld,
    term_document_frequency,
)

TARGETS = ("coverage", "relevance_density")
GUARDS = ("stuffing", "verb_entropy", "mtld", "duplication")
GATES = ("preferences", "faithfulness", "bullet_lines")

# How far each guard may move in its bad direction before an action is
# reverted. Provisional: #127 fits these per guard on the human anchor set and
# ships them in the policy artifact. Units: stuffed-term count, bits, a
# *fraction* of MTLD (MTLD scales with text length, so a fixed number of points
# would block every deletion on a short resume), max pairwise token Jaccard.
DEFAULT_TOLERANCES: Dict[str, float] = {
    "stuffing": 0.0,
    "verb_entropy": 0.35,
    "mtld": 0.10,
    "duplication": 0.15,
}
# +1: a larger value is worse. -1: a smaller value is worse.
_BAD_DIRECTION = {"stuffing": 1, "verb_entropy": -1, "mtld": -1, "duplication": 1}
_RELATIVE = {"mtld"}

_ROUND = 4
_EPS = 1e-9


def _r(x: Optional[float]) -> Optional[float]:
    return None if x is None else round(float(x), _ROUND)


@dataclass
class Context:
    """Everything the metrics read besides the content itself."""
    jd_text: str
    keyword_weights: Optional[Dict[str, float]] = None
    source_experiences: List[Dict] = field(default_factory=list)
    # Item keys a hard (strength-5) preference suppresses / emphasizes, and
    # suppressed skill names (lowercased).
    hard_suppress: Set[str] = field(default_factory=set)
    hard_emphasize: Set[str] = field(default_factory=set)
    suppressed_skills: Set[str] = field(default_factory=set)
    matched_skills: Dict = field(default_factory=dict)
    # `{bullet id: lines}` source, or None when no LaTeX engine is available.
    line_counter: Optional[Callable[[Dict], Dict[str, int]]] = None
    max_bullet_lines: int = 2

    @property
    def jd_keywords(self) -> Set[str]:
        return ATSScoringEngine._extract_keywords(self.jd_text or "")


# ── the vector ───────────────────────────────────────────────────────────────

def _item_keys(content: Dict) -> Set[str]:
    return ({exp_key(e) for e in content.get("experiences") or []}
            | {proj_key(p) for p in content.get("projects") or []})


def preference_violations(content: Dict, ctx: Context) -> List[str]:
    keys = _item_keys(content)
    out = [f"suppressed:{k}" for k in sorted(keys & ctx.hard_suppress)]
    out += [f"missing:{k}" for k in sorted(ctx.hard_emphasize - keys)]
    skills = {str(s.get("name", "")).strip().lower()
              for s in content.get("skills_ranked") or []}
    out += [f"suppressed:skill:{s}" for s in sorted(skills & ctx.suppressed_skills)]
    return out


def _max_pair_jaccard(bullets: Sequence[str]) -> float:
    """Model-free duplication: the most similar pair of bullets by token set.

    The fallback docs/harness.md § 4 names for when neither Jev nor an embedding
    encoder is available. Paraphrases escape it; exact and near-exact repeats
    do not.
    """
    sets = [set(bullet_tokens([b])) for b in bullets]
    best = 0.0
    for i in range(len(sets)):
        for j in range(i + 1, len(sets)):
            union = sets[i] | sets[j]
            if union:
                best = max(best, len(sets[i] & sets[j]) / len(union))
    return best


def metric_vector(content: Dict, ctx: Context) -> Dict[str, Any]:
    """`{gates, guards, targets, report}` for one version of the resume."""
    text = ATSScoringEngine.flatten_tailored_text(content)
    bullets = all_bullets(content)

    lines: Optional[Dict[str, int]] = None
    if ctx.line_counter is not None:
        lines = ctx.line_counter(content)
    gates = {
        "preferences": preference_violations(content, ctx),
        "faithfulness": sorted(faithfulness_drift(content, ctx.source_experiences)),
        "bullet_lines": (None if lines is None else sorted(
            v["bullet"] for v in bullet_line_violations(lines, ctx.max_bullet_lines))),
    }
    guards = {
        "stuffing": len(term_document_frequency(content)["stuffed_terms"]),
        "verb_entropy": _r(leading_verb_entropy(bullets)),
        "mtld": _r(mtld(bullet_tokens(bullets))),
        "duplication": _r(_max_pair_jaccard(bullets)),
    }
    targets = {
        "coverage": _r(ATSScoringEngine._keyword_coverage(
            text, ctx.jd_text or "", ctx.keyword_weights)["score"]),
        "relevance_density": _r(relevance_density(" ".join(bullets), ctx.jd_keywords)),
    }
    ats = ATSScoringEngine.score_tailored(
        content, ctx.jd_text or "", ctx.matched_skills, keyword_weights=ctx.keyword_weights)
    report = {"ats": _r(ats.get("composite"))}
    return {"gates": gates, "guards": guards, "targets": targets, "report": report}


# ── the rule ─────────────────────────────────────────────────────────────────

def _guard_regression(name: str, before, after) -> float:
    """How far `after` moved in the guard's bad direction (0 if it did not)."""
    if before is None or after is None:
        return 0.0
    moved = max(0.0, (after - before) * _BAD_DIRECTION[name])
    if name in _RELATIVE:
        return moved / before if before > 0 else 0.0
    return moved


def accept(before: Dict, after: Dict, *, improves: Iterable[str] = (),
           tolerances: Optional[Dict[str, float]] = None,
           requested: bool = False) -> Dict[str, Any]:
    """Apply the three-part rule to one action's before/after vectors.

    1. No hard gate gains a violation.
    2. No guard regresses by more than its tolerance.
    3. At least one of `improves` (every target when empty) strictly improves,
       or the action is `requested` (a delete a preference or the user asked
       for).
    """
    tol = {**DEFAULT_TOLERANCES, **(tolerances or {})}
    new_violations = {}
    for gate in GATES:
        b, a = before["gates"].get(gate), after["gates"].get(gate)
        if a is None:
            continue
        added = sorted(set(a) - set(b or []))
        if added:
            new_violations[gate] = added
    regressed = {}
    for guard in GUARDS:
        moved = _guard_regression(guard, before["guards"].get(guard), after["guards"].get(guard))
        if moved > tol[guard] + _EPS:
            regressed[guard] = _r(moved)
    wanted = list(improves) or list(TARGETS)
    improved = sorted(t for t in wanted
                      if (after["targets"].get(t) or 0.0)
                      > (before["targets"].get(t) or 0.0) + _EPS)
    deltas = {t: _r((after["targets"].get(t) or 0.0) - (before["targets"].get(t) or 0.0))
              for t in TARGETS}

    if new_violations:
        reason = "hard_gate: " + "; ".join(f"{g} {v}" for g, v in new_violations.items())
    elif regressed:
        reason = "guard: " + ", ".join(f"{g} regressed {v}" for g, v in regressed.items())
    elif not improved and not requested:
        reason = "no_target_improved: " + ", ".join(wanted)
    else:
        reason = None
    return {"accepted": reason is None, "reason": reason, "improved": improved,
            "deltas": deltas, "gate_violations": new_violations, "guard_regressions": regressed}


__all__ = ["Context", "DEFAULT_TOLERANCES", "GATES", "GUARDS", "TARGETS", "accept",
           "metric_vector", "preference_violations"]
