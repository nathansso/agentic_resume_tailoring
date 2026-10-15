"""Which area a skill belongs to, for the skills the keyword table cannot place (#318).

`knowledge_graph/areas.py` places a skill by name and category with a keyword table. A skill
it cannot place ("Mailchimp", "Figma", "Prompt engineering") is "Other", which is where a
misfiled skill hides. This asks Jev instead, once per skill, and caches the answer.

**Question.** One `Choice`, `skill_area@v1`, built per skill: "Which area of a resume's
skills section does the skill <name> belong to?" with the seven areas as options (a short
description each, `areas.AREA_DESCRIPTIONS`) and `Other` as the catch-all. The skill is in
the question, not the state, so one request can carry every unplaced skill of an ingest
(questions in a request are answered independently) while each skill keeps its own cache
row. The state is a constant. The keyword table runs first; Jev only sees what it left in
`Other`.

**Two paths.** `classify` is the write path: `engine.decide` (cache, then one batched
request for the misses, then the fallback on no key, mode `off` or an API error). `cached`
is the read path: `engine.lookup`, the cache and nothing else, so listing a profile's skills
never waits on the network and never spends a call. A skill nobody has classified yet reads
as `Other` until its write path or the backfill has run.

**Threshold.** None: the area is Jev's own choice. It only groups a column, a wrong group
costs a drag and not a wrong resume, and there is no labelled set to fit one on. If
misfilings show up, label them and fit one then (`Answer.p` is the chosen area's
probability).

Model-free apart from `engine.decide`, imported only when a decision is asked.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Tuple

from harness.decisions.questions import Choice

POINT = "skill_area"
VERSION = "skill_area@v1"
STATE = {"task": "Place a skill from a resume in one area of the resume's skills section."}
NAME_MAX_CHARS = 80
CATEGORY_MAX_CHARS = 60

Pair = Tuple[str, Optional[str]]


def _norm(text: Optional[str], limit: int) -> str:
    return " ".join(str(text or "").split())[:limit].rstrip()


def question_for(name: str, category: Optional[str] = None) -> Choice:
    """The one question for one skill. Rewording it means bumping `VERSION`."""
    from knowledge_graph.areas import AREA_DESCRIPTIONS, OTHER

    cat = _norm(category, CATEGORY_MAX_CHARS)
    listed = f' The resume lists it under "{cat}".' if cat and cat.lower() != OTHER.lower() else ""
    return Choice(
        VERSION,
        f'Which area of a resume\'s skills section does the skill "{_norm(name, NAME_MAX_CHARS)}" '
        f"belong to?{listed} Choose the area where a recruiter would look for it, judging by "
        f"what the skill is, not by how it is spelled. Choose {OTHER} when it fits none of them.",
        dict(AREA_DESCRIPTIONS), no_match=OTHER)


def _asked(pairs: Iterable[Pair]) -> List[Tuple[Pair, Choice]]:
    """Each distinct skill once, with its question. A blank name is not a skill."""
    seen: Dict[str, Tuple[Pair, Choice]] = {}
    for pair in pairs:
        if _norm(pair[0], NAME_MAX_CHARS):
            q = question_for(*pair)
            seen.setdefault(q.canonical(), (pair, q))
    return list(seen.values())


def _area(answer) -> Optional[str]:
    return None if answer is None or answer.fell_back else str(answer.value)


def classify(pairs: Iterable[Pair], *, client=None) -> Dict[Pair, Tuple[Optional[str], str]]:
    """Write path: `{(name, category): (area | None, source)}`, asking Jev (one request) for
    the skills the cache lacks. `source` is `cache`, `jev` or `fallback`; the area is None
    when the fallback answered (no key, mode `off`, an API error) and the caller leaves the
    skill where it is."""
    from harness.decisions import engine

    pairs = list(pairs)
    asked = _asked(pairs)
    answers = engine.decide(POINT, STATE, [q for _, q in asked], fallback=None, client=client)
    by_question = {q.canonical(): a for (_, q), a in zip(asked, answers)}
    out: Dict[Pair, Tuple[Optional[str], str]] = {}
    for pair in pairs:
        a = by_question.get(question_for(*pair).canonical()) if _norm(pair[0], NAME_MAX_CHARS) else None
        if a is not None:
            out[pair] = (_area(a), a.source)
    return out


def cached(pairs: Iterable[Pair]) -> Dict[Pair, str]:
    """Read path: `{(name, category): area}` for the skills the cache holds an answer for.
    Never makes a call, in any mode; a miss, an unreadable store or a bad row is left out."""
    from harness.decisions import engine

    pairs = list(pairs)
    asked = _asked(pairs)
    if not asked:
        return {}
    try:
        answers = engine.lookup(POINT, STATE, [q for _, q in asked])
    except Exception:
        return {}
    by_question = {q.canonical(): _area(a) for (_, q), a in zip(asked, answers)}
    out: Dict[Pair, str] = {}
    for pair in pairs:
        area = by_question.get(question_for(*pair).canonical()) if _norm(pair[0], NAME_MAX_CHARS) else None
        if area:
            out[pair] = area
    return out
