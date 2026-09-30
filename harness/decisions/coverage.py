"""Semantic requirement coverage (#126): does a bullet show the candidate meets a requirement?

The literal `coverage` target (`agents/ats_scorer._keyword_coverage`) is substring matching, so
"built ETL pipelines processing 2M records daily" scores zero against "experience with data
engineering at scale". It is also fooled the other way: a keyword written into a bullet that
says nothing of the requirement counts. This asks Jev one yes/no question per (bullet,
requirement) and turns the answers into a second, separate target, `semantic_coverage`.
Neither is folded into the other, or into any composite (docs/harness.md § 5); their
*disagreement* is the signal (see `disagreement`).

**Question.** One `noul`, `requirement_covered@v1`, per requirement, worded positively: "Does
this bullet show that the candidate meets this requirement: <text>?". The instructions say what
does not count (stated interest or plans, a tool named where the requirement asks for more,
something merely related), because cosine-close text like "eager to learn Kubernetes" entails
nothing. Jev sees ONE bullet at a time, never the page (it is distracted by large state), so a
page of N bullets is N requests, each carrying every eligible requirement as a question.

**Which requirements.** The JD profile's `required` and `preferred` requirements (a missing
`type` is `required`, as in `agents/keyword_weights`); `incidental` ones are mentions in passing,
not qualifications, and are left out. Nothing here changes #151's split or #152's weights.

**Covered.** A requirement is covered when some bullet on the page has p >= `TAU_COVER`. Only
bullets count (experience and project bullets, `agents.redundancy.bullet_texts`): the skills
line names a tool and evidences nothing.

**The metric.** With `w(r)` the requirement's criticality (1..5, default 3):

    semantic_coverage = 100 * sum(w(r) for covered r) / sum(w(r) for every eligible r)

A percentage, like the literal `coverage` beside it. It is ONE metric with its own role
(a target), it is monotone only in what the bullets evidence, and it is never combined with
`coverage` or anything else. With no key, mode `off`, or an API error the result is `unchecked`
and the target is absent from the vectors, so a plan reads exactly as it did before #126.

**What is asked.** Answers are cached per (bullet, requirement) through the engine, so a node
pays only for the bullets it changed: one request per changed bullet, carrying one question per
eligible requirement; an unchanged bullet, and a rerun, ask nothing.

Model-free apart from `engine.decide`, imported only when a check runs.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

from agents.ats_scorer import ATSScoringEngine
from agents.redundancy import bullet_texts
from harness.decisions.questions import Noul, canonical

POINT = "requirement_covered"
VERSION = "requirement_covered@v1"
ELIGIBLE = ("required", "preferred")
TARGET = "semantic_coverage"

# Fitted (#126) on the 97 proposed-label pairs in eval/coverage_labels/ (awaiting the user's
# review), against jev-1.13.0's recorded answers to requirement_covered@v1, scoring by Jev's
# yes-probability. Refit with `python eval/fit_coverage_threshold.py analyze` whenever the model
# or the question changes.
#   TAU_COVER 0.55: 0 of 55 not-covered pairs called covered (aspiration 0/11, near-miss 0/15,
#     partial 0/13, unrelated 0/11, boundary 0/5); the highest of them is p_cicd_owner, a partial
#     at 0.39 (0.16 of headroom), the highest aspiration scores 0.05 and the highest near-miss
#     0.07. Covers 42 of 42 covered pairs: literal 13/13, semantic 16/16, boundary 13/13; the
#     lowest is s_warehouse_models at 0.68 (0.13 under). Every value from 0.45 to 0.65 gives
#     the same counts, so 0.55 is the one farthest from both sides of the gap.
TAU_COVER = 0.55

_ROUND = 4
REQUIREMENT_MAX_CHARS = 300
_CRITICALITY_DEFAULT = 3


def _norm(text: str) -> str:
    return " ".join((text or "").split())


def _short(text: str, n: int = 80) -> str:
    text = _norm(text)
    return text if len(text) <= n else text[: n - 1] + "…"


def question_for(requirement: str) -> Noul:
    """The one question, for one requirement. Rewording it means bumping `VERSION`."""
    requirement = _norm(requirement)[:REQUIREMENT_MAX_CHARS].rstrip(" .;:!?")
    return Noul(
        VERSION,
        f'Does this bullet show that the candidate meets this requirement: "{requirement}"? '
        "Answer yes when the bullet describes work, results or experience that satisfies it, "
        "even in different words or at a more specific level. Stated interest, plans or being "
        "eager to learn do not meet a requirement; naming a tool does not meet a requirement "
        "that asks for more than using it, such as years, leadership, ownership or production "
        "scale; and something related or of the same kind is not the requirement itself.",
        true="The bullet shows the candidate meets the requirement.",
        false="The bullet does not show it: it may be related, only state interest, or fall "
              "short of what the requirement asks.")


def build_state(bullet: str) -> Dict[str, Any]:
    """The whole state: the bullet, and nothing else from the resume or the posting."""
    return {"bullet": _norm(bullet)}


def _weight(value: Any) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = _CRITICALITY_DEFAULT
    return max(1, min(5, n))


def eligible_requirements(requirements: Iterable[Dict]) -> List[Dict[str, Any]]:
    """The required and preferred requirements, in source order, as
    `{requirement (ordinal), text, type, criticality, terms}`."""
    out: List[Dict[str, Any]] = []
    for i, r in enumerate(requirements or []):
        if not isinstance(r, dict):
            continue
        text = _norm(str(r.get("text") or ""))
        rtype = str(r.get("type") or "required")
        if not text or rtype not in ELIGIBLE:
            continue
        ordinal = r.get("ordinal")
        out.append({"requirement": ordinal if isinstance(ordinal, int) else i, "text": text,
                    "type": rtype, "criticality": _weight(r.get("criticality")),
                    "terms": [str(t).strip().lower() for t in r.get("terms") or [] if str(t).strip()]})
    return out


def score_of(rows: Sequence[Dict]) -> Optional[float]:
    """The metric, from rows with `criticality` and `covered`: see the module docstring."""
    total = sum(r["criticality"] for r in rows)
    if not total:
        return None
    return round(100.0 * sum(r["criticality"] for r in rows if r["covered"]) / total, _ROUND)


def make_coverage_checker(requirements: Iterable[Dict],
                          tau: Optional[float] = None) -> Optional[Callable[[Dict], Dict]]:
    """`content -> result` for one job's requirements, or None when none is eligible.

    A result is `{status, reason, score, covered, of, requirements}`: `status` is `checked`
    (Jev or the cache answered every question), `unchecked` (the fallback answered one, and
    `reason` says why), or `none` (the page has no bullet, so there is nothing to ask).
    `requirements` holds one row per eligible requirement: `{requirement, text, type,
    criticality, terms, p (the best bullet's), covered}`.

    Memoized per bullet for the life of the checker, so the many metric vectors one plan
    computes ask each bullet once. After the first unchecked answer the checker stays
    unchecked for its life, so a run with no key or a failing API asks once, not per node.
    """
    reqs = eligible_requirements(requirements)
    if not reqs:
        return None
    questions = [question_for(r["text"]) for r in reqs]
    memo: Dict[str, List[float]] = {}
    down: List[str] = []

    def answers_for(bullet: str) -> Optional[List[float]]:
        from harness.decisions import engine

        state = build_state(bullet)
        key = canonical(state)
        if key not in memo:
            answers = engine.decide(POINT, state, questions, fallback=None)
            failed = next((a for a in answers if a.fell_back), None)
            if failed is not None:
                down.append(failed.reason or "fallback")
                return None
            memo[key] = [round(float(a.p), _ROUND) for a in answers]
        return memo[key]

    def check(content: Dict) -> Dict:
        cutoff = TAU_COVER if tau is None else tau
        if down:
            return {"status": "unchecked", "reason": down[0], "score": None, "covered": 0,
                    "of": len(reqs), "requirements": []}
        bullets = list(dict.fromkeys(_norm(b) for b in bullet_texts(content) if _norm(b)))
        if not bullets:
            return {"status": "none", "reason": "no_bullets", "score": None, "covered": 0,
                    "of": len(reqs), "requirements": []}
        best = [0.0] * len(reqs)
        for bullet in bullets:
            answers = answers_for(bullet)
            if answers is None:
                return {"status": "unchecked", "reason": down[0], "score": None, "covered": 0,
                        "of": len(reqs), "requirements": []}
            best = [max(b, p) for b, p in zip(best, answers)]
        rows = [{**r, "p": p, "covered": p >= cutoff - 1e-9} for r, p in zip(reqs, best)]
        return {"status": "checked", "reason": None, "score": score_of(rows),
                "covered": sum(r["covered"] for r in rows), "of": len(rows), "requirements": rows}

    return check


# ── the literal side, and where the two disagree ─────────────────────────────

def _term_pattern(term: str) -> "re.Pattern":
    """`term` as a whole word or phrase, plural allowed, case-insensitive."""
    return re.compile(r"(?<![a-z0-9])" + re.escape(term.lower()) + r"(?:s|es)?(?![a-z0-9])")


def terms_on_page(content: Dict, terms: Iterable[str]) -> Dict[str, List[str]]:
    """`{present, missing}`: which of `terms` the page's text contains, in the terms' order.
    The page text is the one the literal `coverage` target reads, skills line included."""
    haystack = ATSScoringEngine.flatten_tailored_text(content).lower()
    present, missing = [], []
    for term in terms:
        (present if _term_pattern(term).search(haystack) else missing).append(term)
    return {"present": present, "missing": missing}


def disagreement(result: Dict, content: Dict) -> Dict[str, List[Dict]]:
    """Where the literal and the semantic reading of the page part ways, per requirement.

    - `semantic_only`: covered by some bullet, but some of its terms are not on the page. The
      claim is already true and just not in the posting's words: keyword-weave candidates
      (`missing` lists the words to weave, which the cited evidence must still support).
    - `literal_only`: not covered, yet some of its terms are on the page. The stuffing
      signature: the word is there and no bullet shows the requirement (`present` lists them).

    A requirement with no terms is in neither. Entries are compact: `{requirement (ordinal),
    text, p}` and the terms.
    """
    out: Dict[str, List[Dict]] = {"semantic_only": [], "literal_only": []}
    if result.get("status") != "checked":
        return out
    for r in result["requirements"]:
        if not r["terms"]:
            continue
        seen = terms_on_page(content, r["terms"])
        base = {"requirement": r["requirement"], "text": _short(r["text"]), "p": r["p"]}
        if r["covered"] and seen["missing"]:
            out["semantic_only"].append({**base, "missing": seen["missing"]})
        elif not r["covered"] and seen["present"]:
            out["literal_only"].append({**base, "present": seen["present"]})
    return out


def summary(result: Dict, content: Dict) -> Optional[Dict[str, Any]]:
    """The finalize block: the score, the counts, and both disagreement lists. None unless
    the check answered."""
    if result.get("status") != "checked":
        return None
    return {"score": result["score"], "covered": result["covered"], "of": result["of"],
            "tau_cover": TAU_COVER, **disagreement(result, content)}


def node_detail(before: Dict, after: Dict, content: Dict) -> Optional[Dict[str, Any]]:
    """The compact per-node row: the count, the requirements this node `gained` and `lost`
    (ordinals), and the page's disagreement as ordinals. None unless `after` answered."""
    if after.get("status") != "checked":
        return None
    was = ({r["requirement"] for r in before["requirements"] if r["covered"]}
           if before.get("status") == "checked" else set())
    now = {r["requirement"] for r in after["requirements"] if r["covered"]}
    dis = disagreement(after, content)
    return {"covered": after["covered"], "of": after["of"],
            "gained": sorted(now - was) if before.get("status") == "checked" else [],
            "lost": sorted(was - now),
            "semantic_only": [e["requirement"] for e in dis["semantic_only"]],
            "literal_only": [e["requirement"] for e in dis["literal_only"]]}
