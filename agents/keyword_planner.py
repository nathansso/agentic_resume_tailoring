"""
Contextual keyword planning for resume tailoring (issue #72).

The tailor used to hand the generator the first 15 *alphabetically-sorted*
missing JD keywords and reward any that appeared *anywhere* in the output — which
encourages blindly stapling keywords onto whatever bullet, including experiences
they have no business touching. This module replaces that with a two-stage plan:

  1. score_keywords()  — rank the missing JD keywords by how much *signal* they
     carry (JD term frequency x corpus IDF, boosted for skill-graph terms,
     penalized for HR/boilerplate). High-signal, distinctive terms rise; generic
     filler sinks.

  2. assign_keywords() — decide *where* each high-signal keyword can be inserted,
     scoring its contextual fit against each candidate experience/project's own
     source text. A keyword lands on the single item whose content actually
     supports it; a keyword that fits nowhere is dropped rather than stuffed
     somewhere wrong.

Stage 3 (placement evaluation) lives in agents/tailor.py's evaluate node, which
uses evaluate_placement() here to score whether each keyword landed in the item
it was assigned to — so the generate→evaluate loop enforces placement instead of
rewarding blind attachment.

Pure functions over plain dicts/strings — no DB or LLM access. agents/tailor.py
loads the JD, corpus, and item source text and calls these.
"""
import os
import re
from typing import Dict, List, Optional, Sequence, Tuple

from agents.ats_scorer import ATSScoringEngine
from agents.skill_scorer import compute_idf, _idf_of, _jd_token_counts


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


# How many high-signal keywords to carry into the assignment stage.
TOP_KEYWORDS = _env_int("TAILOR_TOP_KEYWORDS", 20)
# Minimum contextual-fit score for a keyword to be assigned to an item at all;
# below this the keyword fits nowhere well and is dropped from bullet insertion.
ASSIGN_THRESHOLD = _env_float("TAILOR_ASSIGN_THRESHOLD", 0.15)
# Cap suggestions per item so a single bullet set is not asked to absorb a dozen
# terms (which reads as stuffing).
MAX_KEYWORDS_PER_ITEM = _env_int("TAILOR_MAX_KEYWORDS_PER_ITEM", 4)
# A keyword sitting directly in an item's own source text is demonstrably about
# that item — the strongest, most truthful placement signal.
_DIRECT_FIT = 1.0

# HR / boilerplate terms that clear the base stop-word filter but carry no
# tailoring signal. Down-weighted so they never crowd out real skills.
_BOILERPLATE_TERMS = frozenset({
    "benefits", "compensation", "salary", "equal", "opportunity", "employer",
    "diversity", "inclusion", "eeo", "disability", "veteran", "gender", "race",
    "applicant", "candidate", "candidates", "responsibilities", "requirements",
    "qualifications", "preferred", "team", "teams", "company", "role", "position",
    "work", "working", "ability", "strong", "excellent", "years", "experience",
    "including", "etc", "please", "apply", "join", "looking", "seeking",
})
_BOILERPLATE_PENALTY = _env_float("TAILOR_BOILERPLATE_PENALTY", 0.4)
# Boost for a keyword that is also a token of one of the user's skills — a term
# we can most plausibly claim truthfully.
_SKILL_BOOST = _env_float("TAILOR_KEYWORD_SKILL_BOOST", 1.35)

_SENTENCE_SPLIT = re.compile(r"[.!?\n;]+")


def _tokenize(text: str) -> set:
    """Keyword tokens of a text, using the ATS engine's tokenizer/stopwords."""
    return ATSScoringEngine._extract_keywords(text or "")


# ── Stage 1: score keywords for signal ─────────────────────────────────────────

def score_keywords(
    missing_keywords: Sequence[str],
    jd_text: str,
    corpus_texts: Optional[Sequence[str]] = None,
    skill_terms: Optional[Sequence[str]] = None,
    top_k: int = TOP_KEYWORDS,
) -> List[Tuple[str, float]]:
    """
    Rank missing JD keywords by signal: TF-in-JD (saturated) x corpus IDF, boosted
    when the keyword is also one of the user's skill terms and penalized for
    HR/boilerplate. Returns [(keyword, score), ...] sorted descending, top_k long.

    `skill_terms` is the set of tokens across the user's skill names — a keyword
    matching one is a term we can most plausibly claim truthfully.
    """
    if not missing_keywords:
        return []
    jd_counts = _jd_token_counts(jd_text or "")
    idf = compute_idf(list(corpus_texts or []))
    skill_tokens = set()
    for term in skill_terms or []:
        skill_tokens |= _tokenize(term)

    scored: List[Tuple[str, float]] = []
    for kw in missing_keywords:
        kw = (kw or "").strip().lower()
        if not kw:
            continue
        cnt = jd_counts.get(kw, 0)
        tf_sat = cnt / (cnt + 1.0) if cnt else 0.5  # unseen-in-JD tokens: neutral TF
        score = tf_sat * _idf_of(kw, idf)
        if kw in skill_tokens:
            score *= _SKILL_BOOST
        if kw in _BOILERPLATE_TERMS:
            score *= _BOILERPLATE_PENALTY
        scored.append((kw, round(score, 4)))

    # Sort by score desc, then keyword asc for determinism.
    scored.sort(key=lambda kv: (-kv[1], kv[0]))
    return scored[:top_k]


# ── Stage 2: assign keywords to items by contextual fit ────────────────────────

def _jd_context(keyword: str, jd_text: str) -> set:
    """Tokens co-occurring with `keyword` in JD sentences that mention it.

    This is the keyword's "neighborhood" — the vocabulary of the JD passages
    about it — which we match against an item's source text to judge fit.
    """
    ctx: set = set()
    for sentence in _SENTENCE_SPLIT.split(jd_text or ""):
        toks = _tokenize(sentence)
        if keyword in toks:
            ctx |= toks
    ctx.discard(keyword)
    return ctx


def keyword_fit(keyword: str, item_tokens: set, context_tokens: set) -> float:
    """
    Contextual fit of one keyword to one item, in [0, 1].

    - _DIRECT_FIT when the keyword sits in the item's own source text: the item
      demonstrably involves it, the most truthful placement.
    - Otherwise the fraction of the keyword's JD-context vocabulary that the item
      also covers — how topically aligned the item is with where this keyword
      lives in the JD.
    """
    if keyword in item_tokens:
        return _DIRECT_FIT
    if not context_tokens:
        return 0.0
    return len(item_tokens & context_tokens) / len(context_tokens)


def assign_keywords(
    scored_keywords: Sequence[Tuple[str, float]],
    items: Sequence[Dict],
    jd_text: str,
    *,
    threshold: float = ASSIGN_THRESHOLD,
    max_per_item: int = MAX_KEYWORDS_PER_ITEM,
) -> Dict[str, List[str]]:
    """
    Assign each high-signal keyword to the single item whose source text best
    supports it (issue #72). Keywords that fit no item above `threshold` are
    dropped — never stapled onto an unrelated experience/project.

    `items`: [{"key": <stable id>, "source_text": <original title+bullets+desc>}, ...]
    Returns {item_key: [keyword, ...]} in descending keyword-signal order, each
    list capped at `max_per_item`. Items with no assignments are omitted.
    """
    item_tokens = {it["key"]: _tokenize(it.get("source_text", "")) for it in items}
    assignments: Dict[str, List[str]] = {}

    for kw, _signal in scored_keywords:
        context = _jd_context(kw, jd_text)
        best_key, best_fit = None, 0.0
        for it in items:
            key = it["key"]
            if len(assignments.get(key, [])) >= max_per_item:
                continue  # item already at its keyword cap
            fit = keyword_fit(kw, item_tokens[key], context)
            if fit > best_fit:
                best_key, best_fit = key, fit
        if best_key is not None and best_fit >= threshold:
            assignments.setdefault(best_key, []).append(kw)

    return assignments


# ── Stage 3: evaluate placement of assigned keywords ───────────────────────────

def evaluate_placement(
    assignments: Dict[str, List[str]],
    rendered_by_key: Dict[str, str],
) -> Dict:
    """
    Score whether assigned keywords landed in the item they were assigned to
    (issue #72). Feeds the generate→evaluate retry loop so it enforces *placement*
    rather than mere presence.

    `rendered_by_key`: {item_key: generated_text_for_that_item} (bullets joined).

    Returns:
      - precision: assigned keywords present in their assigned item / total assigned
      - gaps:      {item_key: [assigned keywords not yet present there]}
      - misplaced: {item_key: [keywords assigned elsewhere that leaked into here]}
    """
    total = 0
    placed = 0
    gaps: Dict[str, List[str]] = {}
    # keyword -> the item it was assigned to, for misplacement detection.
    owner = {kw: key for key, kws in assignments.items() for kw in kws}

    lowered = {key: (text or "").lower() for key, text in rendered_by_key.items()}

    for key, kws in assignments.items():
        item_text = lowered.get(key, "")
        missing_here: List[str] = []
        for kw in kws:
            total += 1
            if _present(kw, item_text):
                placed += 1
            else:
                missing_here.append(kw)
        if missing_here:
            gaps[key] = missing_here

    misplaced: Dict[str, List[str]] = {}
    for key, text in lowered.items():
        leaked = [
            kw for kw, assigned_key in owner.items()
            if assigned_key != key and _present(kw, text)
        ]
        if leaked:
            misplaced[key] = leaked

    return {
        "precision": round(placed / total, 3) if total else 1.0,
        "total": total,
        "placed": placed,
        "gaps": gaps,
        "misplaced": misplaced,
    }


def _present(keyword: str, text: str) -> bool:
    """Whole-word presence of `keyword` in already-lowercased `text`."""
    if not keyword:
        return False
    return re.search(rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])", text) is not None
