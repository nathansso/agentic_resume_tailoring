"""Weighted keyword scoring: `w(t) = importance_in_JD(t) x supportability(t)` (issue #125).

`ats_scorer._keyword_coverage` weighed every JD keyword the same. This module
replaces that uniform weight with a product of two factors that are deliberately
*not* corpus statistics:

**Importance** is a within-document property of *this* posting, read off the
#121 JD profile: requirement `type`, `criticality`, section placement, position
in the requirements list, repetition in the JD text, and — carrying the largest
structural multiplier — whether the term appears in the job title.

**Supportability** is a property of *this* candidate, read off their knowledge
graph. Three tiers, and the bottom one is structural rather than a tuning
choice:

| Evidence | Factor | Meaning |
|---|---|---|
| strong   | 1.0 | logged project/experience work backs it — tailoring should surface it |
| adjacent | 0.5 | a claimed-but-unevidenced skill, or a naming variant of evidenced work |
| none     | **0.0** | covering it requires fabricating it |

The zero is the point of the issue. A keyword the user has no evidence for must
contribute nothing to the reward, because a reward that pays for covering it
creates an incentive to fabricate inside the objective and then claws it back
with a faithfulness penalty — strictly worse than never creating it.

## Why not TF-IDF

IDF measures rarity, not importance. On a small JD corpus the rarest tokens are
typos, company-internal tool names, and one-off acronyms, so noise gets maximum
weight; and because `eval/jd_dataset` skews ML/data, a central term like
`pytorch` appears in many JDs, earns low IDF, and is deflated exactly when it is
the requirement that matters. Decisively, corpus statistics cannot see the
candidate at all, which is the half that decides whether covering a term is
truthful. No function in this module reads any other document.

## Reproducibility

The weight map is persisted and feeds a reward (#51 Phase 2), so it must not
move between runs for a fixed profile and a fixed graph. Every aggregation here
is `max` over an unordered collection (order-independent by construction), the
output dict is built over `sorted()` keys, and every value is rounded before it
is stored. Nothing samples, embeds, or queries.

Pure functions over plain dicts. The graph read that produces `support_index`
and the persistence of the result live in `services`, mirroring how
`agents/jd_profile.py` stays pure against `services.rebuild_jd_profile`.
"""
import logging
from typing import Dict, Iterable, List, Optional, Set

from agents.ats_scorer import (
    _MIN_WORD_LEN, _PURE_NUMBER, _SPLIT_PATTERN, _STOP_WORDS, ATSScoringEngine,
)

logger = logging.getLogger(__name__)

# Bump when the *shape* of the stored weights blob changes, or when a
# coefficient below changes enough that stored weights should be recomputed
# rather than trusted. Weights are cheap and deterministic to rebuild (no model
# call), so a bump costs nothing but a recompute.
WEIGHTS_VERSION = 1

# ── Supportability tiers ─────────────────────────────────────────────────────

SUPPORT_STRONG = "strong"
SUPPORT_ADJACENT = "adjacent"
SUPPORT_NONE = "none"

SUPPORTABILITY: Dict[str, float] = {
    SUPPORT_STRONG: 1.0,
    SUPPORT_ADJACENT: 0.5,
    # Structural, not tunable down to a small epsilon: the objective must not
    # pay for covering a term the candidate cannot truthfully claim.
    SUPPORT_NONE: 0.0,
}

# A JD token is an *adjacent* naming variant of evidenced work when it shares a
# prefix this long with an evidenced token (react/reactjs, postgres/postgresql,
# python/python3), or when one contains the other and the shorter is at least
# _ADJACENT_MIN_LEN characters (torch/pytorch, data/database). Deliberately
# lexical: semantic adjacency (flask ~ django) needs an embedding or entailment
# layer, which is #126's scope, not this one's.
_ADJACENT_PREFIX_LEN = 5
_ADJACENT_MIN_LEN = 4

# ── Importance coefficients ──────────────────────────────────────────────────

# The posting's own framing of the requirement, per #121's `type`.
_TYPE_IMPORTANCE: Dict[str, float] = {
    "required": 1.0,
    "preferred": 0.55,
    "incidental": 0.15,
}
_TYPE_DEFAULT = _TYPE_IMPORTANCE["required"]

# criticality 1..5 -> 0.6 .. 1.0
_CRITICALITY_BASE = 0.5
_CRITICALITY_STEP = 0.1

# Stated under a requirements/qualifications heading rather than in prose.
_SECTION_MULTIPLIER = 1.15
_SECTION_MARKERS = (
    "require", "qualification", "must", "need", "responsib", "skill",
    "what you", "you have", "you'll", "minimum", "basic", "preferred", "nice",
)

# Earlier in the requirements list is stated first and matters more. Bounded:
# ordinal is a weak signal and must not swamp `type` or `criticality`.
_ORDINAL_FLOOR = 0.75

# Repeating a term across the posting is evidence it matters, saturating so a
# boilerplate word repeated 30 times cannot outrank a stated requirement.
_REPETITION_MAX = 1.25

# The job title is the highest-signal, lowest-cost importance input available,
# so title terms get both a floor on their structural base and the largest
# multiplier in this module. A title term that is *also* a top requirement then
# outweighs everything else, which is the intended ordering.
_TITLE_MULTIPLIER = 2.0
_TITLE_BASE = 0.5

# A JD token that appears in no extracted requirement: body prose. Not zero —
# extraction recall is imperfect and the token is still in the posting — but low
# enough that any stated requirement outranks it.
_UNSTATED_IMPORTANCE = 0.10

_ROUND = 6


# ── Tokenization ─────────────────────────────────────────────────────────────

def _tokens(text: str) -> Set[str]:
    """Keyword tokens of a string, using the scorer's own tokenizer.

    Everything here keys on the exact token vocabulary
    `ats_scorer._keyword_coverage` scores over, so a weight can never fail to
    line up with the keyword it is meant to weigh. Profile `terms` may be
    phrases ("machine learning"); they are split into the same single tokens the
    scorer matches on.
    """
    return ATSScoringEngine._extract_keywords(text or "")


def _terms_tokens(terms: Optional[Iterable[str]]) -> Set[str]:
    out: Set[str] = set()
    for term in terms or []:
        out |= _tokens(str(term))
    return out


# ── Importance ───────────────────────────────────────────────────────────────

def _criticality_multiplier(value) -> float:
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = 3
    n = max(1, min(5, n))
    return _CRITICALITY_BASE + _CRITICALITY_STEP * n


def _section_multiplier(source_section: Optional[str]) -> float:
    lowered = (source_section or "").lower()
    if not lowered:
        return 1.0
    return _SECTION_MULTIPLIER if any(m in lowered for m in _SECTION_MARKERS) else 1.0


def _ordinal_multiplier(ordinal, count: int) -> float:
    """Linear decay from 1.0 at the first requirement to `_ORDINAL_FLOOR` at the last."""
    if count <= 1:
        return 1.0
    try:
        position = int(ordinal)
    except (TypeError, ValueError):
        return 1.0
    position = max(0, min(count - 1, position))
    return 1.0 - (1.0 - _ORDINAL_FLOOR) * (position / (count - 1))


def _repetition_multiplier(count: int) -> float:
    """Saturating boost for a term the posting repeats. 1 mention -> 1.0."""
    if count <= 1:
        return 1.0
    return 1.0 + (_REPETITION_MAX - 1.0) * (count - 1) / count


def _jd_token_counts(jd_text: str) -> Dict[str, int]:
    """Occurrence counts over the scorer's token vocabulary.

    Deliberately re-derived here rather than imported from
    `skill_scorer._jd_token_counts`: that module's copy is the input to a
    TF-IDF score this issue exists to reject, and importing it would tie this
    module's lifetime to it.
    """
    counts: Dict[str, int] = {}
    for word in _SPLIT_PATTERN.split((jd_text or "").lower()):
        if (
            len(word) >= _MIN_WORD_LEN
            and word not in _STOP_WORDS
            and not _PURE_NUMBER.match(word)
        ):
            counts[word] = counts.get(word, 0) + 1
    return counts


def importance_map(payload: Optional[Dict], jd_text: str) -> Dict[str, float]:
    """`importance_in_JD(t)` for every keyword token in `jd_text`.

    The universe is exactly `ats_scorer._extract_keywords(jd_text)` — the token
    set the keyword component already scores over — so this refines the existing
    component rather than replacing its denominator with the profile's terms.
    Tokens the profile never mentions keep a low floor instead of dropping out,
    because extraction recall is imperfect.

    Aggregation across requirements is `max`, not a sum: a term stated twice is
    not twice as important, and `max` is independent of requirement order, which
    keeps the map reproducible.
    """
    universe = _tokens(jd_text)
    if not universe:
        return {}

    payload = payload or {}
    requirements = [
        r for r in (payload.get("requirements") or []) if isinstance(r, dict)
    ]
    count = len(requirements)

    structural: Dict[str, float] = {}
    for req in requirements:
        base = (
            _TYPE_IMPORTANCE.get(str(req.get("type") or ""), _TYPE_DEFAULT)
            * _criticality_multiplier(req.get("criticality"))
            * _section_multiplier(req.get("source_section"))
            * _ordinal_multiplier(req.get("ordinal"), count)
        )
        for token in _terms_tokens(req.get("terms")) & universe:
            if base > structural.get(token, 0.0):
                structural[token] = base

    title = _terms_tokens(payload.get("title_terms")) & universe
    counts = _jd_token_counts(jd_text)

    out: Dict[str, float] = {}
    for token in sorted(universe):
        base = structural.get(token, _UNSTATED_IMPORTANCE)
        is_title = token in title
        if is_title:
            base = max(base, _TITLE_BASE)
        value = base * _repetition_multiplier(counts.get(token, 1))
        if is_title:
            value *= _TITLE_MULTIPLIER
        out[token] = round(value, _ROUND)
    return out


# ── Supportability ───────────────────────────────────────────────────────────

def supportability_map(
    terms: Iterable[str], support_index: Optional[Dict],
) -> Dict[str, str]:
    """Tier each term against the candidate's evidence. Returns `{term: tier}`.

    `support_index` is `{"strong": [...], "claimed": [...]}` — token lists built
    from the knowledge graph by `services.build_support_index`. An absent or
    empty index tiers everything `none`; the caller decides what that means (the
    scorer falls back to uniform weighting rather than scoring zero).
    """
    index = support_index or {}
    strong: Set[str] = {str(t) for t in (index.get("strong") or [])}
    claimed: Set[str] = {str(t) for t in (index.get("claimed") or [])}
    prefixes = {t[:_ADJACENT_PREFIX_LEN] for t in strong if len(t) >= _ADJACENT_PREFIX_LEN}
    long_strong = [t for t in sorted(strong) if len(t) >= _ADJACENT_MIN_LEN]

    out: Dict[str, str] = {}
    for term in sorted({str(t) for t in terms}):
        if term in strong:
            out[term] = SUPPORT_STRONG
        elif term in claimed:
            out[term] = SUPPORT_ADJACENT
        elif _is_naming_variant(term, prefixes, long_strong):
            out[term] = SUPPORT_ADJACENT
        else:
            out[term] = SUPPORT_NONE
    return out


def _is_naming_variant(term: str, prefixes: Set[str], long_strong: List[str]) -> bool:
    if len(term) < _ADJACENT_MIN_LEN:
        return False
    if len(term) >= _ADJACENT_PREFIX_LEN and term[:_ADJACENT_PREFIX_LEN] in prefixes:
        return True
    return any(
        (term in other) or (len(other) >= _ADJACENT_MIN_LEN and other in term)
        for other in long_strong
    )


# ── The product ──────────────────────────────────────────────────────────────

def compute_weights(
    payload: Optional[Dict], jd_text: str, support_index: Optional[Dict],
) -> Dict:
    """The stored weights blob: `w(t) = importance(t) x supportability(t)`.

    Returns

        {"version": int, "profile_version": int|None, "jd_digest": str,
         "terms": {t: w}, "importance": {t: i}, "supportability": {t: tier}}

    All three maps are kept, not just the product. `importance` is the half that
    does not depend on the candidate and is what #151 must reuse rather than
    inventing a second importance function; `supportability` names the tier, and
    the *adjacent* tier is directly actionable for the planner — it is precisely
    where `reframe` / `keyword_weave` have positive expected value.
    """
    from agents.jd_profile import extraction_key

    importance = importance_map(payload, jd_text)
    support = supportability_map(importance.keys(), support_index)
    terms = {
        token: round(value * SUPPORTABILITY.get(support.get(token, SUPPORT_NONE), 0.0), _ROUND)
        for token, value in importance.items()
    }
    return {
        "version": WEIGHTS_VERSION,
        "profile_version": (payload or {}).get("profile_version"),
        # Ties the map to the exact JD text it was computed over, so a consumer
        # can refuse a stale map instead of silently zero-weighting every token
        # the posting has gained since.
        "jd_digest": extraction_key(jd_text, version=WEIGHTS_VERSION),
        "terms": terms,
        "importance": importance,
        "supportability": support,
    }


def weights_digest(blob: Optional[Dict]) -> str:
    """Stable digest of a weights blob, for determinism assertions."""
    import hashlib
    import json

    return hashlib.sha256(
        json.dumps(blob or {}, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
