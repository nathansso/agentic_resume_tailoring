"""Profile ontology and per-profile sidecar metadata (issue #172).

The ontology names the axes a benchmark profile can vary along; the sidecar is
how one profile declares which axis it isolates, plus the two things a résumé
markdown has nowhere to put — GitHub metrics and distractor sets.

Everything here is offline and pure.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from eval.profile_meta import (  # noqa: E402
    GITHUB_METRIC_KEYS,
    ProfileMetaError,
    check_projects_exist,
    load_meta,
    parse_meta,
    sidecar_path,
)
from eval.profile_ontology import (  # noqa: E402
    MIN_PROFILES_PER_CELL,
    REPORTED_STRATA,
    STRATA,
    STRATUM_NAMES,
    OntologyError,
    cell_key,
    missing_strata,
    validate_strata,
)

PROFILES_DIR = ROOT / "eval" / "profiles"


# ── ontology ───────────────────────────────────────────────────────────────────

def test_role_family_is_the_primary_stratum_and_matches_the_jd_corpus():
    """Candidate and posting must be sliceable on the same axis (#177)."""
    sys.path.insert(0, str(ROOT / "scripts"))
    from scrape_job_descriptions import ROLE_FAMILY_NAMES

    assert STRATUM_NAMES[0] == "role_family"
    assert set(STRATA["role_family"]) == set(ROLE_FAMILY_NAMES)


def test_level_matches_the_jd_corpus_levels():
    sys.path.insert(0, str(ROOT / "scripts"))
    from scrape_job_descriptions import LEVELS

    assert set(STRATA["level"]) == set(LEVELS)


def test_declaration_order_is_the_reporting_order():
    """A per-stratum table must not reorder itself between runs (#158/#171)."""
    assert list(REPORTED_STRATA) == [s for s in STRATUM_NAMES if s in REPORTED_STRATA]


def test_validate_accepts_a_well_formed_declaration():
    validate_strata({"role_family": "ml_engineering", "level": "intern"})


def test_an_unknown_stratum_value_is_refused():
    with pytest.raises(OntologyError, match="expected one of"):
        validate_strata({"level": "senior"})


def test_a_misspelled_stratum_key_is_refused_not_ignored():
    """Silently ignoring a typo is worse than not slicing at all.

    The profile would report under a stratum it never declared and the table
    would still look complete.
    """
    with pytest.raises(OntologyError, match="unknown stratum"):
        validate_strata({"evidence_denisty": "metric_rich"})


def test_missing_strata_lists_undeclared_reported_axes_in_order():
    assert missing_strata({"role_family": "data_science"}) == [
        s for s in REPORTED_STRATA if s != "role_family"]


def test_cell_key_is_stable_regardless_of_dict_order():
    a = cell_key({"level": "intern", "role_family": "data_science"})
    b = cell_key({"role_family": "data_science", "level": "intern"})
    assert a == b == "role_family=data_science|level=intern"


def test_min_profiles_per_cell_is_above_one():
    """n=1 confounds the stratum with the individual profile.

    Codified Character Logic (arXiv:2505.07705) reports per-stratum results
    across 83 characters; the point of a floor above 1 is that a difference
    cannot be explained by one synthetic person's phrasing.
    """
    assert MIN_PROFILES_PER_CELL >= 3


# ── sidecar ────────────────────────────────────────────────────────────────────

def test_sidecar_path_sits_beside_the_profile():
    assert sidecar_path(Path("a/b/alex.md")).name == "alex.meta.json"


def test_a_profile_without_a_sidecar_loads_empty(tmp_path):
    meta = load_meta(tmp_path / "nobody.md")
    assert meta.strata == {} and meta.github_metrics == {}
    assert meta.cell == ""


def test_parse_reads_strata_metrics_and_distractors():
    meta = parse_meta({
        "strata": {"role_family": "ai_engineering", "level": "intern"},
        "github_metrics": {"Thing": {"stars": 4, "author_commits": 10}},
        "distractors": {"skills": ["COBOL"]},
        "notes": "hand-reviewed",
    })
    assert meta.stratum("role_family") == "ai_engineering"
    assert meta.github_metrics["Thing"]["stars"] == 4
    assert meta.distractors["skills"] == ["COBOL"]
    assert meta.notes == "hand-reviewed"


def test_an_unknown_github_metric_key_is_refused():
    """`_github_signal` reads a closed set, so an unknown key is inert.

    Accepting it would leave the author believing they had seeded a signal.
    """
    with pytest.raises(ProfileMetaError, match="unknown key"):
        parse_meta({"github_metrics": {"Thing": {"starz": 4}}})


@pytest.mark.parametrize("key", sorted(GITHUB_METRIC_KEYS))
def test_every_documented_metric_key_is_accepted(key):
    parse_meta({"github_metrics": {"Thing": {key: 1}}})


def test_malformed_sections_are_refused():
    for raw, match in [
        ({"strata": []}, "must be an object"),
        ({"github_metrics": []}, "must be an object"),
        ({"github_metrics": {"T": 3}}, "must be an object"),
        ({"distractors": {"skills": "COBOL"}}, "must be a list"),
    ]:
        with pytest.raises(ProfileMetaError, match=match):
            parse_meta(raw)


def test_invalid_json_names_the_file(tmp_path):
    profile = tmp_path / "p.md"
    sidecar_path(profile).write_text("{not json", encoding="utf-8")
    with pytest.raises(ProfileMetaError, match="not valid JSON"):
        load_meta(profile)


def test_metrics_naming_a_nonexistent_project_are_refused():
    """A typo would seed the metrics onto nothing and leave _github_signal None
    while the fixture looked like it carried a signal."""
    meta = parse_meta({"github_metrics": {"Typo-Name": {"stars": 1}}})
    with pytest.raises(ProfileMetaError, match="no such project"):
        check_projects_exist(meta, ["SemanticSearch-Lite", "StreamBoard"])


def test_project_names_match_case_insensitively():
    meta = parse_meta({"github_metrics": {"semanticsearch-lite": {"stars": 1}}})
    check_projects_exist(meta, ["SemanticSearch-Lite"])


# ── the shipped sidecars ───────────────────────────────────────────────────────

def _profiles():
    return sorted(PROFILES_DIR.glob("*.md"))


def test_every_shipped_sidecar_is_valid_and_matches_its_profile():
    from eval.profile_fixture import load_profile

    for profile in _profiles():
        meta = load_meta(profile)          # raises on a malformed sidecar
        if meta.github_metrics:
            fixture = load_profile(profile)
            check_projects_exist(meta, [p["name"] for p in fixture.projects])


def test_the_default_profile_seeds_metrics_that_make_github_signal_fire():
    """#155's recorded gap, made executable.

    Its deviations state: "the harness profile carries no ingested GitHub
    metrics, so `_github_signal()` returns `None` for every fixture project and
    the component is omitted from `_complexity()` exactly as before — a fixture
    with GitHub metrics is the missing capability."
    """
    from agents.project_scorer import _github_signal

    meta = load_meta(PROFILES_DIR / "benchmark_profile.md")
    assert meta.github_metrics, "the default profile seeds no metrics"
    for project, metrics in sorted(meta.github_metrics.items()):
        signal = _github_signal(metrics)
        assert signal is not None, f"{project} still yields no github signal"
        assert 0.0 <= signal <= 1.0


def test_a_solo_and_a_collaborative_project_score_differently():
    """The seeded metrics must actually exercise #155's authorship weighting,
    not just be present."""
    from agents.project_scorer import _github_signal

    meta = load_meta(PROFILES_DIR / "benchmark_profile.md")
    signals = {name: _github_signal(m) for name, m in meta.github_metrics.items()}
    assert len(set(signals.values())) > 1, signals
