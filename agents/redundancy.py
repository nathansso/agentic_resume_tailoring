"""
Redundancy metrics for a tailored resume (issue #122).

"Redundancy" is four distinct failure modes, and counting skill terms — all the
benchmark did before this module — sees one and a half of them:

    term stuffing        "Python" spread across eight bullets
    semantic duplication "Led a team of 5" + "Managed 5 engineers"
    lexical monotony     every bullet: "Developed X using Y to achieve Z"
    dilution             bullets grow longer and say no more

Semantic duplication is the largest gap and the one a human reader punishes
hardest. It shares no tokens with its twin, so no amount of counting can find
it; it needs embeddings.

Why this lives in `agents/` and not `eval/`
-------------------------------------------
These are eval metrics today, but #127 consumes them as the `Δcost` term of
`net(a) = ΔATS − λ·Δcost` **inside the tailoring controller**, per prefix. That
makes them runtime scoring, not reporting. Putting them in `eval/` would force
`agents/` to import from `eval/` — a dependency inversion the repo has nowhere.
Same reasoning that produced `agents/skill_selection.py` in #150.

The cost term matters because the ATS composite cannot detect over-tailoring:
0.75 of it is coverage, and coverage is monotone non-decreasing in edits. A
monotone reward makes "edit maximally" optimal, which any learner will happily
saturate. This penalty is what makes the objective peaked.

Purity
------
Pure functions over a `tailored_content` dict — stdlib + numpy, no DB, no LLM.
The embedding model is **injected**, never imported: the semantic metrics are
the only ones with a model dependency, and an injected encoder keeps that
dependency visible at the call site instead of buried in an import.

Absent an encoder the semantic keys are *omitted* rather than reported as some
neutral value, matching how `agents/skill_embeddings.py` degrades. A metric that
fabricates a signal when it has none is worse than a missing key — see #158,
where a fake-but-stable embedder silently drove the whole skills ranking.
"""
from __future__ import annotations

import math
import re
from typing import Callable, Dict, Iterable, List, Optional, Sequence

import numpy as np

from agents.ats_scorer import ATSScoringEngine

# A bullet-level document frequency above this reads as stuffing: the term is
# in more than half the bullets, which no genuine resume needs.
STUFFING_DF_THRESHOLD = 0.5

# MTLD's standard TTR floor (McCarthy & Jarvis 2010). A factor completes when
# the running type-token ratio falls to this value.
MTLD_TTR_FLOOR = 0.72

# An `Encoder` is anything with sentence-transformers' `.encode` shape. Typed as
# a callable protocol rather than importing SentenceTransformer, so this module
# stays free of the model dependency.
Encoder = Callable[[List[str]], "np.ndarray"]


# ── bullet extraction ──────────────────────────────────────────────────────────

def bullet_texts(tailored_content: Dict) -> List[str]:
    """Every bullet in the resume, in document order.

    One canonical extraction so the four modes cannot disagree about what a
    "bullet" is — the previous per-metric inlining of this loop is exactly how
    term counts and the type-token ratio drifted apart.
    """
    out: List[str] = []
    for key in ("experiences", "projects"):
        for item in tailored_content.get(key) or []:
            for b in item.get("bullets") or []:
                if b and str(b).strip():
                    out.append(str(b))
    return out


def _bullets_by_item(tailored_content: Dict) -> List[List[str]]:
    """Bullets grouped by their parent experience/project.

    Dilution is only meaningful *within* an item: two projects legitimately
    restate a shared technology, but a bullet repeating the bullet above it in
    the same role is padding.
    """
    groups: List[List[str]] = []
    for key in ("experiences", "projects"):
        for item in tailored_content.get(key) or []:
            bullets = [str(b) for b in (item.get("bullets") or []) if b and str(b).strip()]
            if bullets:
                groups.append(bullets)
    return groups


# ── term stuffing: bullet-level document frequency ─────────────────────────────

def term_document_frequency(tailored_content: Dict) -> Dict:
    """How many distinct bullets each skill term appears in, normalized.

    Raw occurrence counts conflate two different faults: "Python" three times in
    one bullet is a badly written bullet, while "Python" once in each of eight
    bullets is stuffing. Document frequency separates them and is what actually
    reads as spam.
    """
    terms = _skill_terms(tailored_content)
    bullets = [b.lower() for b in bullet_texts(tailored_content)]
    if not terms or not bullets:
        return {"bullet_df": {}, "max_bullet_df": 0.0, "mean_bullet_df": 0.0, "stuffed_terms": {}}

    df: Dict[str, float] = {}
    for term in terms:
        # Boundary-aware, so "sql" does not match inside "mysql"/"sqlalchemy".
        pattern = re.compile(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])")
        hits = sum(1 for b in bullets if pattern.search(b))
        df[term] = round(hits / len(bullets), 3)

    stuffed = {t: v for t, v in df.items() if v > STUFFING_DF_THRESHOLD}
    values = list(df.values())
    return {
        "bullet_df": dict(sorted(df.items(), key=lambda kv: -kv[1])),
        "max_bullet_df": max(values),
        "mean_bullet_df": round(sum(values) / len(values), 3),
        "stuffed_terms": dict(sorted(stuffed.items(), key=lambda kv: -kv[1])),
    }


def _skill_terms(tailored_content: Dict) -> List[str]:
    """Skill terms to audit, from the ranked selection or the emphasized list.

    Mirrors the fallback `eval/metrics.py::redundancy_metrics` already used, so
    both read the same universe. `skills_ranked` is stable run to run as of #158.
    """
    ranked = tailored_content.get("skills_ranked") or []
    terms = [str(s.get("name", "")).lower() for s in ranked if s.get("name")]
    if not terms:
        terms = [str(t).lower() for t in tailored_content.get("skills_emphasized") or [] if t]
    return [t for t in terms if t]


# ── lexical monotony: leading-verb entropy ─────────────────────────────────────

def leading_verb_entropy(bullets: Sequence[str]) -> Optional[float]:
    """Normalized Shannon entropy over each bullet's opening token.

    Resume bullets open with a verb by convention, so the first token is a free
    proxy for the verb with no POS tagger. This catches the machine-generated
    cadence — "Developed…", "Developed…", "Developed…" — that a type-token ratio
    misses entirely, because those bullets can be lexically varied everywhere
    after word one.

    Normalized by log(n) so the scale is comparable across resumes with
    different bullet counts: 1.0 = every bullet opens differently, 0.0 = all
    identical. Returns None for fewer than two bullets, where the measure has
    no meaning.
    """
    openers = [_leading_token(b) for b in bullets]
    openers = [o for o in openers if o]
    n = len(openers)
    if n < 2:
        return None

    counts: Dict[str, int] = {}
    for o in openers:
        counts[o] = counts.get(o, 0) + 1
    entropy = -sum((c / n) * math.log(c / n) for c in counts.values())
    # `+ 0.0` normalizes the -0.0 that a single-opener distribution produces:
    # an all-identical resume should report 0.0, not a negative zero.
    return round(entropy / math.log(n), 3) + 0.0


def _leading_token(bullet: str) -> str:
    """First alphabetic word of a bullet, lowercased and de-punctuated."""
    for word in re.split(r"[^A-Za-z']+", str(bullet).strip()):
        if word:
            return word.lower()
    return ""


# ── lexical monotony: MTLD ─────────────────────────────────────────────────────

def mtld(tokens: Sequence[str], floor: float = MTLD_TTR_FLOOR) -> Optional[float]:
    """Measure of Textual Lexical Diversity (McCarthy & Jarvis 2010).

    The mean number of tokens it takes for the running type-token ratio to fall
    to `floor`, averaged over a forward and a backward pass. Unlike TTR it does
    not shrink as text grows, which matters here specifically: `bullet_budget`
    varies bullet length, so the benchmark's `bullet_type_token_ratio` partly
    measures the *budget* rather than repetitiveness.

    Returns None when the text never reaches the floor even once — with too few
    tokens the statistic is dominated by the partial factor and is not a
    diversity measure at all.

    Caveat worth knowing before tuning against this: MTLD is only reliable from
    roughly 100 tokens, and a tailored resume runs ~100–300 across all bullets.
    Measured on synthetic constant-diversity text, TTR falls monotonically with
    length (0.667 → 0.203 over a 8x span) while MTLD shows no trend but does
    wander within a band (~62–94). So it removes the systematic length bias that
    makes TTR partly a measure of `bullet_budget`, but it is not a precision
    instrument at this scale — treat a small MTLD difference as noise.
    """
    toks = [t for t in tokens if t]
    if len(toks) < 2:
        return None
    forward = _mtld_pass(toks, floor)
    backward = _mtld_pass(list(reversed(toks)), floor)
    if forward is None or backward is None:
        return None
    return round((forward + backward) / 2, 2)


def _mtld_pass(tokens: Sequence[str], floor: float) -> Optional[float]:
    """One directional MTLD pass: mean token-length of a complete factor."""
    factors = 0.0
    types: set = set()
    count = 0
    for tok in tokens:
        types.add(tok)
        count += 1
        ttr = len(types) / count
        if ttr <= floor:
            factors += 1
            types, count = set(), 0
    if count > 0:
        # Partial trailing factor, weighted by how far it got toward the floor.
        ttr = len(types) / count if count else 1.0
        denom = 1.0 - floor
        factors += (1.0 - ttr) / denom if denom else 0.0
    if factors <= 0:
        return None
    return len(tokens) / factors


def bullet_tokens(bullets: Sequence[str]) -> List[str]:
    """Flat token stream across bullets, matching the existing TTR tokenizer."""
    out: List[str] = []
    for b in bullets:
        out.extend(w for w in str(b).lower().split() if len(w) > 2)
    return out


# ── dilution: new-information ratio ────────────────────────────────────────────

def new_information_ratio(tailored_content: Dict) -> Dict:
    """How much each bullet adds over the bullets above it in the same item.

    Operationalizes "bullets grow longer, say no more": a bullet whose content
    tokens all already appeared in earlier bullets of the same experience scores
    0.0 no matter how long it is. Length alone is not the fault — restatement is.

    The first bullet of an item is 1.0 by definition; it has nothing to repeat.
    """
    groups = _bullets_by_item(tailored_content)
    ratios: List[float] = []
    for bullets in groups:
        seen: set = set()
        for bullet in bullets:
            tokens = ATSScoringEngine._extract_keywords(bullet)
            if not tokens:
                continue
            fresh = tokens - seen
            ratios.append(len(fresh) / len(tokens))
            seen |= tokens
    if not ratios:
        return {"mean_new_information": None, "min_new_information": None}
    return {
        "mean_new_information": round(sum(ratios) / len(ratios), 3),
        "min_new_information": round(min(ratios), 3),
    }


# ── semantic duplication: pairwise cosine ──────────────────────────────────────

def semantic_duplication(vectors) -> Dict:
    """Max and mean pairwise cosine over bullet embeddings.

    The headline addition: paraphrases share no tokens, so this is the only one
    of the four modes that can see them. Computed as a gram matrix rather than
    an O(n^2) Python loop — the cost that matters is the encode, not this.

    Returns empty when there are fewer than two vectors: a single bullet cannot
    duplicate anything, and reporting 0.0 would read as "verified clean".
    """
    arr = np.asarray(vectors, dtype=float)
    if arr.ndim != 2 or arr.shape[0] < 2:
        return {}

    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    unit = arr / norms

    gram = unit @ unit.T
    # Upper triangle excluding the diagonal: each unordered pair exactly once.
    iu = np.triu_indices(gram.shape[0], k=1)
    pairs = gram[iu]
    if pairs.size == 0:
        return {}

    # Clamp: floating point can push an identical pair a hair above 1.0, and a
    # cosine reported as 1.0000001 makes downstream thresholds look broken.
    pairs = np.clip(pairs, -1.0, 1.0)
    return {
        "max_pairwise_cosine": round(float(pairs.max()), 4),
        "mean_pairwise_cosine": round(float(pairs.mean()), 4),
        "duplicate_pair_count": int((pairs >= 0.9).sum()),
    }


# ── incremental path (built for #127's per-prefix caller) ──────────────────────

class BulletSimilarityCache:
    """Bullet-text → vector cache, so a per-prefix caller re-encodes only edits.

    #127 evaluates `Δcost` after every action in a plan. Recomputing every
    bullet's embedding per prefix would make the controller's dominant cost the
    encoder, and the issue calls this out as the thing to design for up front
    rather than retrofit.

    Keyed on the bullet *text*, not its index: an edit changes one bullet's text
    while the rest of the document keeps its vectors, and a text that reappears
    (a revert, or the same bullet under a reordered plan) is already warm.

    Determinism (#158): new texts are encoded in sorted order and mapped back by
    text, so batch composition is fixed for a given set of new bullets rather
    than depending on document order. The residual #158 documented still holds —
    composition depends on *which* texts are already cached — but it cannot make
    one process's results disagree with themselves, which is what matters here.
    """

    def __init__(self, encoder: Optional[Encoder] = None):
        self._encoder = encoder
        self._vectors: Dict[str, np.ndarray] = {}
        self.encode_calls = 0      # test seam: proves warm bullets are not re-encoded
        self.encoded_texts = 0

    def warm(self, bullets: Iterable[str]) -> None:
        """Encode any bullets not already cached."""
        if self._encoder is None:
            return
        missing = sorted({str(b) for b in bullets if b and str(b).strip()} - self._vectors.keys())
        if not missing:
            return
        vecs = np.asarray(self._encoder(missing), dtype=float)
        if vecs.ndim == 1:
            vecs = vecs.reshape(1, -1)
        for text, vec in zip(missing, vecs):
            self._vectors[text] = vec
        self.encode_calls += 1
        self.encoded_texts += len(missing)

    def vectors_for(self, bullets: Sequence[str]) -> List[np.ndarray]:
        """Cached vectors for these bullets, in order, warming any that are new."""
        self.warm(bullets)
        return [self._vectors[str(b)] for b in bullets if str(b) in self._vectors]

    def score(self, bullets: Sequence[str]) -> Dict:
        """Semantic duplication for this prefix, reusing every warm vector."""
        if self._encoder is None:
            return {}
        vecs = self.vectors_for(bullets)
        if len(vecs) < 2:
            return {}
        return semantic_duplication(np.asarray(vecs, dtype=float))


# ── rollup ─────────────────────────────────────────────────────────────────────

def redundancy_report(tailored_content: Dict, encoder: Optional[Encoder] = None) -> Dict:
    """All four redundancy modes for one tailored resume.

    `encoder` takes a list of strings and returns a 2-D array of vectors — the
    `.encode` shape of a sentence-transformers model. When it is None the
    semantic keys are *omitted*, not zeroed: see the module docstring.
    """
    bullets = bullet_texts(tailored_content)
    tokens = bullet_tokens(bullets)

    report: Dict = {
        **term_document_frequency(tailored_content),
        **new_information_ratio(tailored_content),
        "leading_verb_entropy": leading_verb_entropy(bullets),
        "mtld": mtld(tokens),
        "bullet_count": len(bullets),
    }

    if encoder is not None and len(bullets) >= 2:
        cache = BulletSimilarityCache(encoder)
        report.update(cache.score(bullets))
    return report
