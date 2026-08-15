"""The implicitness filter's own test suite (issue #172, chunk 6).

Split the way `eval/implicitness.py` documents: everything here runs with **no
model**, against the committed measurement, so editing a pair or a label fails
immediately. The one test that loads the real encoder is
`@pytest.mark.integration`, because `eval/profile_checks.py` already established
that a sentence-transformer is too slow for the default suite.
"""
import pytest

from eval.implicitness import (
    BETA,
    EVIDENCE_AXIS_AUC,
    EVIDENCE_AXIS_BASELINE,
    EVIDENCE_AXIS_BEST_ACCURACY,
    EVIDENCE_FLOOR,
    EXPLICIT,
    IMPLICIT,
    LABELS,
    MEASURED,
    PAIRS,
    UNRELATED,
    auc,
    best_threshold,
    committed_scores,
    distributions,
    is_implicit,
    is_lexically_invisible,
    label_violations,
    score_pairs,
    separation,
    shared_keywords,
)


# ── the labelled set ──────────────────────────────────────────────────────────

def test_every_label_is_known():
    assert {p.label for p in PAIRS} <= set(LABELS)


def test_pair_keys_are_unique():
    keys = [p.key for p in PAIRS]
    assert len(keys) == len(set(keys))


def test_every_pair_names_a_real_jd_and_profile():
    """Both ends are checkable against the committed corpus, which is what makes
    the set reviewable rather than merely plausible."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    for pair in PAIRS:
        assert (root / "eval" / "jd_dataset" / f"{pair.jd}.json").exists(), pair.key
        assert (root / "eval" / "profiles" / f"{pair.profile}.md").exists(), pair.key


def test_requirement_text_appears_in_its_posting():
    """A pair that misquotes its source would make the whole set unauditable."""
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    for pair in PAIRS:
        raw = json.loads(
            (root / "eval" / "jd_dataset" / f"{pair.jd}.json").read_text(encoding="utf-8"))
        normalized = " ".join(raw["description"].split())
        assert " ".join(pair.requirement.split()) in normalized, pair.key


def test_bullet_text_appears_in_its_profile():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    for pair in PAIRS:
        text = (root / "eval" / "profiles" / f"{pair.profile}.md").read_text(encoding="utf-8")
        assert " ".join(pair.bullet.split()) in " ".join(text.split()), pair.key


def test_all_three_populations_are_represented():
    """The `unrelated` class is what an evidence floor would be measured
    against; a set without it cannot answer the question at all."""
    counts = {label: sum(1 for p in PAIRS if p.label == label) for label in LABELS}
    for label, n in counts.items():
        assert n >= 5, f"{label} has only {n} pairs"


# ── the lexical contract — the half that actually ships ───────────────────────

def test_declared_labels_re_derive_from_measured_overlap():
    """The build-failing assertion #172 asks for, on the axis that ships.

    A declaration is free; this re-measures it. The first version of this set
    failed here on 7 of 12 pairs, which is why the explicit pairs were re-mined.
    """
    assert label_violations(PAIRS) == []


def test_lexical_invisibility_uses_the_metric_s_own_extractor():
    """`implicit` must mean 'keyword_coverage can score nothing here', so the
    check has to run on that extractor's vocabulary and not a private one."""
    from agents.ats_scorer import ATSScoringEngine

    requirement = "Design, build, and manage ETL pipelines"
    bullet = "Built Airflow DAGs moving 40M shipment events a day"
    expected = (ATSScoringEngine._extract_keywords(requirement)
                & ATSScoringEngine._extract_keywords(bullet))
    assert shared_keywords(requirement, bullet) == expected


def test_is_implicit_is_lexical_only_and_needs_no_model():
    """Rule 2 was measured and dropped, so the shipped filter takes no cosine."""
    assert is_implicit(
        "Design, build, and manage ETL pipelines integrating Omeda, Salesforce, "
        "Google Analytics, and third-party services.",
        "Built Airflow DAGs moving 40M shipment events a day from Kafka into "
        "Snowflake at 99.9% delivery.")
    assert not is_implicit(
        "Experience with dbt for data transformations and warehouse modeling",
        "Built the dbt test coverage across 60 warehouse models, catching schema "
        "regressions before reporting saw them.")


def test_a_shared_term_makes_a_pair_visible():
    assert is_lexically_invisible("kubernetes basics", "wrote python scripts")
    assert not is_lexically_invisible("kubernetes basics", "runs on kubernetes")


# ── the calibration commitment ────────────────────────────────────────────────

def test_every_pair_has_a_committed_score():
    scores = committed_scores()
    assert set(scores) == {p.key for p in PAIRS}


def test_beta_is_unset_because_the_populations_do_not_separate():
    """The negative result, pinned. If a future encoder *does* separate them this
    test fails and asks to be re-read — which is the intended prompt, not a
    nuisance."""
    assert BETA is None
    separated, implicit_max, explicit_min = separation(
        committed_scores(), IMPLICIT, EXPLICIT)
    assert not separated
    assert implicit_max >= explicit_min


def test_cosine_still_ranks_explicit_above_implicit():
    """'No usable threshold' and 'no signal' are different claims, and only the
    first is true. Retiring β on the strength of the second would have been
    wrong."""
    scores = committed_scores()
    by = {label: [scores[p.key] for p in PAIRS if p.label == label]
          for label in LABELS}
    assert auc(by[EXPLICIT], by[IMPLICIT]) == pytest.approx(0.918, abs=0.01)


def test_evidence_floor_is_unset_because_the_axis_is_too_weak():
    assert EVIDENCE_FLOOR is None
    scores = committed_scores()
    by = {label: [scores[p.key] for p in PAIRS if p.label == label]
          for label in LABELS}
    measured_auc = auc(by[IMPLICIT], by[UNRELATED])
    _, accuracy, baseline = best_threshold(by[IMPLICIT], by[UNRELATED])
    assert measured_auc == pytest.approx(EVIDENCE_AXIS_AUC, abs=0.01)
    assert accuracy == pytest.approx(EVIDENCE_AXIS_BEST_ACCURACY, abs=0.01)
    assert baseline == pytest.approx(EVIDENCE_AXIS_BASELINE, abs=0.01)
    # The recorded reason for dropping rule 2: the margin over the baseline is
    # too thin to gate dataset admission on.
    assert accuracy - baseline < 0.30


def test_committed_distributions_match_the_docstring():
    dist = distributions(committed_scores())
    assert dist[EXPLICIT]["n"] == 10
    assert dist[IMPLICIT]["n"] == 11
    assert dist[UNRELATED]["n"] == 10
    assert dist[EXPLICIT]["min"] == pytest.approx(0.1144, abs=1e-4)
    assert dist[IMPLICIT]["max"] == pytest.approx(0.2501, abs=1e-4)


def test_a_pair_without_a_committed_score_is_an_error():
    """Adding a pair and forgetting to re-measure must fail loudly rather than
    silently dropping it from every check."""
    from eval.implicitness import CalibrationError, Pair

    extra = PAIRS + (Pair(key="unmeasured", label=UNRELATED, requirement="x",
                          bullet="y", jd="j", profile="p"),)
    with pytest.raises(CalibrationError, match="unmeasured"):
        committed_scores(extra)


def test_scores_are_deterministic_under_a_fake_encoder():
    """Batch composition is a function of the pair set alone (#158), so two
    calls agree."""
    import numpy as np

    def encoder(texts):
        rng = np.random.default_rng(0)
        return rng.normal(size=(len(texts), 8))

    assert score_pairs(encoder) == score_pairs(encoder)


# ── the live encoder ──────────────────────────────────────────────────────────

@pytest.mark.integration
@pytest.mark.slow
def test_committed_scores_still_match_the_live_encoder():
    """The commitment cannot drift away from the model it describes.

    Integration-gated: the fast suite pins the *contract*, this pins the
    *measurement*.
    """
    from eval.implicitness import live_encoder

    fresh = score_pairs(live_encoder())
    committed = committed_scores()
    for key, value in sorted(committed.items()):
        assert fresh[key] == pytest.approx(value, abs=5e-3), key
