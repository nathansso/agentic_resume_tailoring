"""Redundancy metric suite — the four separable failure modes (issue #122).

The suite exists because counting skill terms sees one and a half of four
modes. Each test below pins a mode that the pre-#122 `redundancy_metrics` could
not observe at all, so a regression that silently reverts to counting fails
here rather than in a benchmark aggregate months later.
"""
import math

import numpy as np
import pytest

from agents.redundancy import (
    BulletSimilarityCache,
    bullet_texts,
    bullet_tokens,
    leading_verb_entropy,
    mtld,
    new_information_ratio,
    redundancy_report,
    semantic_duplication,
    term_document_frequency,
)


def _content(experiences=None, projects=None, skills_ranked=None, emphasized=None):
    out = {}
    if experiences is not None:
        out["experiences"] = [{"bullets": b} for b in experiences]
    if projects is not None:
        out["projects"] = [{"bullets": b} for b in projects]
    if skills_ranked is not None:
        out["skills_ranked"] = [{"name": n} for n in skills_ranked]
    if emphasized is not None:
        out["skills_emphasized"] = emphasized
    return out


class _LookupEncoder:
    """Encoder returning chosen vectors for known sentences.

    Lets the semantic tests assert *the metric* rather than the model: given an
    encoder that says two bullets are near-identical, does the suite surface
    them? Asserting real MiniLM behavior belongs in the integration test at the
    bottom of this file, not in the fast suite.
    """

    def __init__(self, table, dim=8):
        self.table = table
        self.dim = dim
        self.calls = 0
        self.texts_seen = []

    def __call__(self, texts):
        self.calls += 1
        self.texts_seen.append(list(texts))
        out = []
        for t in texts:
            vec = self.table.get(t)
            if vec is None:
                # Unknown text → a deterministic vector. Seeded from a stable
                # digest, never `hash()`, which is per-process randomized (#158).
                import hashlib
                digest = hashlib.blake2b(t.encode("utf-8"), digest_size=4).digest()
                rng = np.random.default_rng(int.from_bytes(digest, "big"))
                vec = rng.standard_normal(self.dim)
            out.append(np.asarray(vec, dtype=float))
        return np.asarray(out, dtype=float)


# ── mode 1: semantic duplication (the mode counting cannot see) ────────────────

def test_paraphrases_score_high_cosine_while_term_counts_stay_clean():
    """The acceptance case: duplication invisible to counting is caught."""
    a = "Led a team of five engineers"
    b = "Managed a group of five developers"   # paraphrase, no shared skill term
    c = "Reduced latency using Redis"

    # a and b nearly parallel; c orthogonal to both.
    encoder = _LookupEncoder({
        a: [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        b: [0.98, 0.20, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        c: [0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    })

    content = _content(experiences=[[a, b, c]], skills_ranked=["Redis"])
    report = redundancy_report(content, encoder=encoder)

    # Counting sees nothing wrong: "redis" is in exactly one of three bullets.
    assert report["max_bullet_df"] == pytest.approx(1 / 3, abs=0.01)
    assert report["stuffed_terms"] == {}
    # The semantic metric does.
    assert report["max_pairwise_cosine"] > 0.95
    assert report["duplicate_pair_count"] == 1


def test_semantic_keys_omitted_without_an_encoder():
    """Absent a model the keys are missing, never a fabricated neutral value."""
    content = _content(experiences=[["Built a service", "Shipped an API"]])
    report = redundancy_report(content, encoder=None)
    for key in ("max_pairwise_cosine", "mean_pairwise_cosine", "duplicate_pair_count"):
        assert key not in report


def test_semantic_duplication_needs_two_vectors():
    """A lone bullet cannot duplicate anything; 0.0 would read as 'verified clean'."""
    assert semantic_duplication(np.asarray([[1.0, 0.0]])) == {}
    assert semantic_duplication(np.asarray([])) == {}


def test_identical_vectors_do_not_exceed_one():
    """Float error must not report a cosine above 1.0 and break thresholds."""
    vecs = np.asarray([[0.3, 0.4, 0.5], [0.3, 0.4, 0.5]])
    assert semantic_duplication(vecs)["max_pairwise_cosine"] <= 1.0


# ── mode 2: lexical monotony ───────────────────────────────────────────────────

def test_identical_opening_verbs_score_zero_entropy():
    bullets = [
        "Developed a payments service",
        "Developed a data pipeline",
        "Developed a recommendation model",
        "Developed an internal API",
    ]
    entropy = leading_verb_entropy(bullets)
    assert entropy == 0.0
    assert not math.copysign(1, entropy) < 0, "must be 0.0, not -0.0"


def test_varied_opening_verbs_score_full_entropy():
    bullets = [
        "Developed a payments service",
        "Led a data pipeline",
        "Built a recommendation model",
        "Shipped an internal API",
    ]
    assert leading_verb_entropy(bullets) == 1.0


def test_entropy_undefined_below_two_bullets():
    assert leading_verb_entropy(["Developed a service"]) is None
    assert leading_verb_entropy([]) is None


def test_entropy_ignores_leading_punctuation_and_case():
    assert leading_verb_entropy(["- Developed a service", "developed a pipeline"]) == 0.0


# ── mode 2b: MTLD is not length-biased the way TTR is ─────────────────────────

def test_mtld_lacks_the_systematic_length_bias_that_ttr_has():
    """TTR falls monotonically with length at constant diversity; MTLD does not.

    This is the whole reason MTLD is added: `bullet_budget` varies bullet
    length, so `bullet_type_token_ratio` partly measures the budget rather than
    repetitiveness. MTLD is noisy at resume scale (~100-300 tokens) so the
    assertion is about *trend and spread*, not equality — claiming stability it
    does not have would be a worse test than none.
    """
    import random
    rng = random.Random(7)
    vocab = [f"word{i:03d}" for i in range(120)]

    def tokens_for(n_bullets):
        bullets = [" ".join(rng.choice(vocab) for _ in range(12)) for _ in range(n_bullets)]
        return bullet_tokens(bullets)

    ttrs, mtlds = [], []
    for n in (6, 12, 24, 48):
        toks = tokens_for(n)
        ttrs.append(len(set(toks)) / len(toks))
        mtlds.append(mtld(toks))

    # TTR degrades monotonically as the document grows.
    assert all(ttrs[i] > ttrs[i + 1] for i in range(len(ttrs) - 1))
    # MTLD does not carry that trend.
    assert not all(mtlds[i] > mtlds[i + 1] for i in range(len(mtlds) - 1))
    # And its relative spread is far smaller than TTR's.
    assert (max(mtlds) / min(mtlds)) < (max(ttrs) / min(ttrs))


def test_mtld_undefined_for_trivial_input():
    assert mtld([]) is None
    assert mtld(["only"]) is None


# ── mode 3: term stuffing as bullet-level document frequency ──────────────────

def test_term_in_many_bullets_is_stuffing_but_repeats_in_one_are_not():
    """The distinction raw counts cannot draw (issue #122)."""
    spread = _content(
        experiences=[["Python service", "Python pipeline", "Python model", "Python API"]],
        skills_ranked=["Python"],
    )
    concentrated = _content(
        experiences=[["Python with Python and more Python work", "Built a data pipeline",
                      "Shipped a model", "Wrote an API"]],
        skills_ranked=["Python"],
    )
    assert term_document_frequency(spread)["stuffed_terms"] == {"python": 1.0}
    # Same raw occurrence count, but concentrated in one bullet → not stuffing.
    assert term_document_frequency(concentrated)["stuffed_terms"] == {}


def test_bullet_df_is_word_boundary_aware():
    """"sql" must not match inside "mysql" — the pre-existing guarantee."""
    content = _content(experiences=[["Tuned mysql and sqlalchemy queries"]],
                       skills_ranked=["SQL"])
    assert term_document_frequency(content)["bullet_df"] == {"sql": 0.0}


def test_term_df_falls_back_to_skills_emphasized():
    content = _content(experiences=[["Wrote Python services"]], emphasized=["Python"])
    assert term_document_frequency(content)["bullet_df"] == {"python": 1.0}


# ── mode 4: dilution ───────────────────────────────────────────────────────────

def test_restating_an_earlier_bullet_scores_near_zero_new_information():
    content = _content(experiences=[[
        "Built a distributed payments service handling refunds",
        "Built a distributed payments service handling refunds again",
    ]])
    out = new_information_ratio(content)
    assert out["min_new_information"] < 0.2      # the restatement adds almost nothing
    assert out["mean_new_information"] < 0.6


def test_genuinely_new_bullets_score_high_new_information():
    content = _content(experiences=[[
        "Built a distributed payments service",
        "Reduced checkout latency forty percent",
    ]])
    assert new_information_ratio(content)["min_new_information"] > 0.9


def test_dilution_is_scoped_per_item_not_across_the_resume():
    """Two roles may legitimately share a technology; a role repeating itself may not."""
    across = _content(
        experiences=[["Built Kubernetes tooling for deployments"]],
        projects=[["Built Kubernetes tooling for deployments"]],
    )
    # Identical text in *different* items is not counted as dilution...
    assert new_information_ratio(across)["min_new_information"] == 1.0

    # ...while the same repetition inside one item is.
    within = _content(experiences=[[
        "Built Kubernetes tooling for deployments",
        "Built Kubernetes tooling for deployments",
    ]])
    assert new_information_ratio(within)["min_new_information"] == 0.0


# ── incremental path for #127's per-prefix caller ──────────────────────────────

def test_incremental_cache_matches_full_recompute():
    """The equivalence acceptance criterion."""
    bullets = [f"Bullet number {i} describing distinct work" for i in range(6)]
    table = {b: np.random.default_rng(i).standard_normal(8) for i, b in enumerate(bullets)}

    cache = BulletSimilarityCache(_LookupEncoder(table))
    fresh_encoder = _LookupEncoder(table)

    # Feed growing prefixes, as #127's controller will.
    for k in range(2, len(bullets) + 1):
        prefix = bullets[:k]
        incremental = cache.score(prefix)
        full = semantic_duplication(fresh_encoder(prefix))
        assert incremental == full, f"prefix of {k} diverged"


def test_cache_encodes_each_distinct_bullet_exactly_once():
    """The point of the cache: an edit re-encodes one bullet, not the document."""
    bullets = [f"Bullet {i}" for i in range(5)]
    encoder = _LookupEncoder({})
    cache = BulletSimilarityCache(encoder)

    cache.score(bullets)
    assert cache.encoded_texts == 5

    # One bullet edited: only the new text is encoded.
    edited = list(bullets)
    edited[2] = "Bullet 2 rewritten with different wording"
    cache.score(edited)
    assert cache.encoded_texts == 6, "expected exactly one additional encode"

    # Reverting reuses the warm vector — no encode at all.
    calls_before = cache.encode_calls
    cache.score(bullets)
    assert cache.encode_calls == calls_before


def test_cache_encodes_in_sorted_order_for_determinism():
    """Carries #158's lesson: batch composition must not follow document order."""
    encoder = _LookupEncoder({})
    cache = BulletSimilarityCache(encoder)
    cache.score(["Zebra work", "Alpha work", "Middle work"])
    assert encoder.texts_seen[0] == sorted(encoder.texts_seen[0])


def test_cache_without_encoder_is_inert():
    cache = BulletSimilarityCache(None)
    assert cache.score(["a", "b"]) == {}


# ── shared extraction ──────────────────────────────────────────────────────────

def test_bullet_texts_spans_experiences_and_projects_and_drops_blanks():
    content = _content(experiences=[["one", "", "  "]], projects=[["two"]])
    assert bullet_texts(content) == ["one", "two"]


def test_report_handles_an_empty_resume():
    report = redundancy_report({}, encoder=None)
    assert report["bullet_count"] == 0
    assert report["leading_verb_entropy"] is None
    assert report["mean_new_information"] is None


# ── real model (slow, network) ─────────────────────────────────────────────────

@pytest.mark.integration
@pytest.mark.slow
def test_real_model_separates_paraphrase_from_unrelated():
    """The claim the fast tests deliberately do not make: that the *model*
    places a paraphrase nearer than an unrelated sentence."""
    from agents.matcher import get_embedding_model

    model = get_embedding_model()
    encoder = lambda texts: model.encode(texts, normalize_embeddings=True)

    paraphrase = _content(experiences=[[
        "Led a team of five engineers",
        "Managed a group of five developers",
    ]])
    unrelated = _content(experiences=[[
        "Led a team of five engineers",
        "Optimized PostgreSQL index storage on disk",
    ]])
    dup = redundancy_report(paraphrase, encoder=encoder)["max_pairwise_cosine"]
    non = redundancy_report(unrelated, encoder=encoder)["max_pairwise_cosine"]
    assert dup > non, f"paraphrase {dup} should exceed unrelated {non}"
