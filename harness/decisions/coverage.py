"""Semantic requirement coverage (#126): does a bullet show the candidate meets a requirement?

The literal `coverage` target (`agents/ats_scorer._keyword_coverage`) is substring matching, so
"built ETL pipelines processing 2M records daily" scores zero against "experience with data
engineering at scale". It is also fooled the other way: a keyword written into a bullet that
says nothing of the requirement counts. This asks Jev one yes/no question per (bullet,
requirement) and turns the answers into a second, separate target, `semantic_coverage`.
Neither is folded into the other, or into any composite (docs/harness.md § 5); their
*disagreement* is the signal (see `disagreement`).

**Question.** One `noul`, `requirement_covered@v2`, per requirement, worded positively: "Does
this bullet show that the candidate meets this requirement: <text>?". The instructions say what
does not count (stated interest or plans, a tool named where the requirement asks for more,
something merely related), because cosine-close text like "eager to learn Kubernetes" entails
nothing. v2 adds that a working-style requirement (deadlines, process, communication,
collaboration, ownership, attention to detail) needs a stated instance: v1 covered "tight
deadlines" at 0.58 and "established processes" at 0.67 from bullets that were merely adjacent
(a generic automation, a notebook), overlapping genuine evidence at 0.66. Jev sees ONE bullet at a time, never the page (it is distracted by large state), so a
page of N bullets is N requests, each carrying every eligible requirement as a question.

**Which requirements.** The JD profile's `required` and `preferred` requirements (a missing
`type` is `required`, as in `agents/keyword_weights`); `incidental` ones are mentions in passing,
not qualifications, and are left out. Nothing here changes #151's split or #152's weights.

**Covered.** A requirement is covered when some piece of evidence on the page has p >= `TAU_COVER`.
Evidence is the experience and project bullets (`agents.redundancy.bullet_texts`) and each
education entry as text (`education_text`: degree, institution, dates), so "Bachelor's degree in
Computer Science" can be covered by the degree line. The skills line names a tool and evidences
nothing.

**Education entries** are asked a separate question, `education_covered@v1`, because the
bullet wording ("does this bullet show...") does not fit and rewording it would invalidate every
bullet recording. It asks what the entry STATES: a degree, field, institution or enrollment. A
degree listed as expected or in progress is not yet earned (and is enrollment in it), a related
field, a minor or another level is not the requirement, and Jev is not asked to compare dates or
levels beyond what is written (it is weak at both), so a requirement that needs today's date
("currently enrolled" against a finished degree) is left uncovered. One request per entry, state
`{text, kind: "education"}`, cached like a bullet.

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
VERSION = "requirement_covered@v2"
LEGACY_VERSION = "requirement_covered@v1"      # no trait clause; the benchmark was recorded with it
EDU_POINT = "education_covered"
EDU_VERSION = "education_covered@v1"
ELIGIBLE = ("required", "preferred")
TARGET = "semantic_coverage"

# Fitted (#126) on the 131 pairs in eval/coverage_labels/ (the first 97 labels user-confirmed,
# the soft_skill and education ones proposed), against jev-1.13.0's recorded answers to
# requirement_covered@v2 (bullets) and education_covered@v1 (education entries), scoring by Jev's
# yes-probability. Refit with `python eval/fit_coverage_threshold.py analyze` whenever the model or
# a question changes.
#   TAU_COVER 0.65: 0 of 72 not-covered pairs called covered (aspiration 0/11, near-miss 0/15,
#     soft_skill 0/8, partial 0/13, unrelated 0/11, boundary 0/5, education 0/9). The highest of
#     them is ss_process_benchmark, a soft skill at 0.58 (0.07 of headroom); the highest aspiration
#     scores 0.06 and the highest near-miss 0.08. Covers 58 of 59 covered pairs: literal 13/13,
#     semantic 16/16, boundary 13/13, education 6/6, soft_skill 10/11. The one miss is
#     ss_ownership_run at 0.61, which sits 0.03 over the highest false candidate: soft-skill
#     scores are the thin part of the gap (under v1 of the bullet question the two overlapped:
#     0.67 not covered against 0.66 covered). 0.70 gives the same counts with 0.02 of room under
#     the next covered score, so 0.65 is the one farthest from both sides.
TAU_COVER = 0.65

_ROUND = 4
REQUIREMENT_MAX_CHARS = 300
_CRITICALITY_DEFAULT = 3


def _norm(text: str) -> str:
    return " ".join((text or "").split())


def _short(text: str, n: int = 80) -> str:
    text = _norm(text)
    return text if len(text) <= n else text[: n - 1] + "…"


_TRAIT = (" A requirement about a working style or trait, such as meeting deadlines, following a "
          "process, communicating, collaborating, taking ownership or attention to detail, is met "
          "only by a bullet that states an instance of it: the deadline, the process or standard "
          "followed, the people worked with, or what was checked, owned or caught. A technical "
          "result alone does not show a trait.")


def question_for(requirement: str, version: Optional[str] = None) -> Noul:
    """The one question, for one requirement. Rewording it means bumping `VERSION`; `version`
    asks for an earlier wording (the benchmark's recordings were made with `LEGACY_VERSION`)."""
    version = version or VERSION
    requirement = _norm(requirement)[:REQUIREMENT_MAX_CHARS].rstrip(" .;:!?")
    return Noul(
        version,
        f'Does this bullet show that the candidate meets this requirement: "{requirement}"? '
        "Answer yes when the bullet describes work, results or experience that satisfies it, "
        "even in different words or at a more specific level. Stated interest, plans or being "
        "eager to learn do not meet a requirement; naming a tool does not meet a requirement "
        "that asks for more than using it, such as years, leadership, ownership or production "
        "scale; and something related or of the same kind is not the requirement itself."
        + (_TRAIT if version != LEGACY_VERSION else ""),
        true="The bullet shows the candidate meets the requirement.",
        false="The bullet does not show it: it may be related, only state interest, or fall "
              "short of what the requirement asks.")


def education_question_for(requirement: str) -> Noul:
    """The one question for an education entry, for one requirement. Rewording it means bumping
    `EDU_VERSION`."""
    requirement = _norm(requirement)[:REQUIREMENT_MAX_CHARS].rstrip(" .;:!?")
    return Noul(
        EDU_VERSION,
        f'Does this education entry show that the candidate meets this requirement: "{requirement}"? '
        "Judge only what the entry states: its degree, field and institution, and whether the degree "
        "is earned or expected. A degree not marked expected or in progress is an earned degree. Answer yes when the entry states a degree, field or "
        "enrollment that satisfies the requirement: an earned degree meets a requirement for that "
        "degree; a degree marked expected is current enrollment in it and is not yet an earned "
        "degree; an earned degree is not current enrollment. A different field, a minor, or a "
        "different level of degree (a bachelor's where a master's is asked, or the reverse) is not "
        "the requirement. Do not compare dates or levels beyond what is written.",
        true="The entry states a degree, field or enrollment that satisfies the requirement.",
        false="The entry does not state it: the field or level differs, the degree is only expected "
              "where an earned one is asked, or the entry says nothing about it.")


def build_state(text: str, kind: str = "bullet") -> Dict[str, Any]:
    """The whole state: one bullet (`{bullet}`) or one education entry (`{text, kind}`), and
    nothing else from the resume or the posting."""
    if kind == "bullet":
        return {"bullet": _norm(text)}
    return {"text": _norm(text), "kind": kind}


_IN_PROGRESS = re.compile(r"expected|anticipated|present|current|in progress|ongoing|pursuing", re.IGNORECASE)


def education_text(entry: Dict) -> str:
    """One education entry as the text Jev sees: `<degree>, <institution>`, and its dates only
    when the degree is in progress (`<degree>, <institution> (Expected May 2027)`).

    A finished degree's date is left out on purpose. Jev is weak on dates and, given
    "B.S. Computer Science, <school> (2026-05)", scored "Bachelor's degree in Computer Science" at
    0.3 to 0.4: it could not tell a past date from an expected one. An entry with no marker states
    an earned degree, and the question says so. GPA is left out too: a minimum GPA is a comparison."""
    def part(key):
        return _norm(str(entry.get(key) or ""))
    head = ", ".join(x for x in (part("degree"), part("institution")) if x)
    start, end = part("start_date"), part("end_date")
    ongoing = bool(_IN_PROGRESS.search(end)) or bool(start and not end)
    dates = " – ".join(x for x in (start, end) if x) if ongoing else ""
    return f"{head} ({dates})" if head and dates else head or dates


def page_evidence(content: Dict) -> List[tuple]:
    """`(kind, text)` for every distinct bullet, then every distinct education entry, in page order."""
    out: List[tuple] = []
    seen = set()
    for kind, texts in (("bullet", bullet_texts(content)),
                        ("education", [education_text(e) for e in content.get("education") or []
                                       if isinstance(e, dict)])):
        for t in texts:
            t = _norm(t)
            if t and (kind, t) not in seen:
                seen.add((kind, t))
                out.append((kind, t))
    return out


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


def make_coverage_checker(requirements: Iterable[Dict], tau: Optional[float] = None, *,
                          version: Optional[str] = None,
                          education: bool = True) -> Optional[Callable[[Dict], Dict]]:
    """`content -> result` for one job's requirements, or None when none is eligible.

    A result is `{status, reason, score, covered, of, requirements}`: `status` is `checked`
    (Jev or the cache answered every question), `unchecked` (the fallback answered one, and
    `reason` says why), or `none` (the page has no bullet, so there is nothing to ask).
    `requirements` holds one row per eligible requirement: `{requirement, text, type,
    criticality, terms, p (the best evidence's), by (`bullet` or `education`), covered}`.

    Memoized per bullet and per education entry for the life of the checker, so the many metric
    vectors one plan computes ask each once. After the first unchecked answer the checker stays
    unchecked for its life, so a run with no key or a failing API asks once, not per node.

    `version` and `education=False` reproduce an earlier measurement (the benchmark's recordings
    were made with `LEGACY_VERSION` and bullets only); the executor uses the defaults.
    """
    reqs = eligible_requirements(requirements)
    if not reqs:
        return None
    questions = {"bullet": [question_for(r["text"], version) for r in reqs],
                 "education": [education_question_for(r["text"]) for r in reqs]}
    points = {"bullet": POINT, "education": EDU_POINT}
    memo: Dict[str, List[float]] = {}
    down: List[str] = []

    def answers_for(kind: str, text: str) -> Optional[List[float]]:
        from harness.decisions import engine

        state = build_state(text, kind)
        key = canonical(state)
        if key not in memo:
            answers = engine.decide(points[kind], state, questions[kind], fallback=None)
            failed = next((a for a in answers if a.fell_back), None)
            if failed is not None:
                down.append(failed.reason or "fallback")
                return None
            memo[key] = [round(float(a.p), _ROUND) for a in answers]
        return memo[key]

    def unchecked() -> Dict:
        return {"status": "unchecked", "reason": down[0], "score": None, "covered": 0,
                "of": len(reqs), "requirements": []}

    def check(content: Dict) -> Dict:
        cutoff = TAU_COVER if tau is None else tau
        if down:
            return unchecked()
        evidence = [e for e in page_evidence(content) if education or e[0] == "bullet"]
        if not evidence:
            return {"status": "none", "reason": "no_bullets", "score": None, "covered": 0,
                    "of": len(reqs), "requirements": []}
        best = [0.0] * len(reqs)
        by = ["bullet"] * len(reqs)
        for kind, text in evidence:
            answers = answers_for(kind, text)
            if answers is None:
                return unchecked()
            for i, p in enumerate(answers):
                if p > best[i]:
                    best[i], by[i] = p, kind
        rows = [{**r, "p": p, "by": k, "covered": p >= cutoff - 1e-9} for r, p, k in zip(reqs, best, by)]
        return {"status": "checked", "reason": None, "score": score_of(rows),
                "covered": sum(r["covered"] for r in rows), "of": len(rows), "requirements": rows}

    return check


# ── the literal side, and where the two disagree ─────────────────────────────

def _term_pattern(term: str) -> "re.Pattern":
    """`term` as a whole word or phrase, plural allowed, case-insensitive."""
    return re.compile(r"(?<![a-z0-9])" + re.escape(term.lower()) + r"(?:s|es)?(?![a-z0-9])")


def terms_on_page(content: Dict, terms: Iterable[str]) -> Dict[str, List[str]]:
    """`{present, missing}`: which of `terms` the page's text contains, in the terms' order.
    The page text is the one the literal `coverage` target reads, skills line included, plus the
    education entries (evidence here, so their words count as present)."""
    haystack = "\n".join([ATSScoringEngine.flatten_tailored_text(content),
                          *(education_text(e) for e in content.get("education") or []
                            if isinstance(e, dict))]).lower()
    present, missing = [], []
    for term in terms:
        (present if _term_pattern(term).search(haystack) else missing).append(term)
    return {"present": present, "missing": missing}


def disagreement(result: Dict, content: Dict) -> Dict[str, List[Dict]]:
    """Where the literal and the semantic reading of the page part ways, per requirement.

    - `semantic_only`: covered by some bullet (or education entry: `by: education`), but some of
      its terms are not on the page. The claim is already true and just not in the posting's
      words: keyword-weave candidates (`missing` lists the words to weave, which the cited
      evidence must still support).
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
            via = {"by": "education"} if r.get("by") == "education" else {}
            out["semantic_only"].append({**base, **via, "missing": seen["missing"]})
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
