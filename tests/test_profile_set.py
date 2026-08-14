"""The committed benchmark profile set (issue #172).

`tests/test_profile_ontology.py` covers the ontology and the sidecar *format*.
This file covers the **dataset**: that the twenty committed profiles are what
`eval/profile_banks.py` renders, that they parse, that they cover the strata
they claim to cover, and — the part that matters most — that every declared
stratum is *true of the text*. A declaration is free; a false one silently
corrupts the slice it reports under, which is worse than not slicing at all.

Everything here is offline: no database, no model, no benchmark run.
"""
import collections
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from eval.benchmark_suite import discover_profiles  # noqa: E402
from eval.profile_banks import FAMILY_VOCAB, SLOTS, VARIANTS  # noqa: E402
from eval.profile_checks import (  # noqa: E402
    ALLOWED_LEVELS,
    check_all,
    check_profile,
    measure,
    redundancy_modes_fired,
)
from eval.profile_fixture import load_profile  # noqa: E402
from eval.profile_generator import (  # noqa: E402
    REDUNDANT_SUFFIX,
    all_profiles,
    drifted,
    slug_for,
)
from eval.profile_meta import check_projects_exist, load_meta  # noqa: E402
from eval.profile_ontology import (  # noqa: E402
    MIN_PROFILES_PER_CELL,
    REPORTED_STRATA,
    STRATA,
)

PROFILES_DIR = ROOT / "eval" / "profiles"


@pytest.fixture(scope="module")
def profiles():
    """Every profile the suite reports on — retired fixtures excluded."""
    found = discover_profiles(PROFILES_DIR)
    assert found, "no profiles discovered"
    return found


def _strata(path: Path):
    return load_meta(path).strata


# ── the committed files are the bank's output ─────────────────────────────────

def test_committed_profiles_match_the_bank():
    """The markdown is generated, so hand-editing it is a defect.

    Without this test the rendered file quietly becomes the real dataset and the
    bank becomes a stale comment that no longer explains it.
    """
    problems = drifted(PROFILES_DIR)
    assert not problems, (
        "committed profiles are out of date with eval/profile_banks.py:\n  "
        + "\n  ".join(problems)
        + "\n\nrun: python eval/profile_generator.py --write")


def test_the_set_is_five_families_times_three_variants_plus_derivatives():
    composed = all_profiles()
    authored = [c for c in composed if not c.redundant]
    derived = [c for c in composed if c.redundant]
    assert len(authored) == 15, "5 role families x 3 variants"
    assert len(derived) == 5, "one redundancy-bearing derivative per family"
    assert len(SLOTS) == 15
    assert len(VARIANTS) == 3


def test_every_derivative_is_a_strict_superset_of_its_base():
    """The redundancy pair must differ in the hazard and nothing else.

    Appended, never substituted: if the base's bullets were rewritten instead,
    a metric moving between the pair could be explained by the rewrite rather
    than by the redundancy, and the matched-pair design would be lost.
    """
    for derived in (c for c in all_profiles() if c.redundant):
        base_slug = derived.slug[: -len(REDUNDANT_SUFFIX)]
        base = load_profile(PROFILES_DIR / f"{base_slug}.md")
        variant = load_profile(PROFILES_DIR / f"{derived.slug}.md")

        assert [e["company"] for e in base.experiences] == \
               [e["company"] for e in variant.experiences]
        assert [s["name"] for s in base.skills] == [s["name"] for s in variant.skills]
        for before, after in zip(base.experiences + base.projects,
                                 variant.experiences + variant.projects):
            assert after["bullets"][:len(before["bullets"])] == before["bullets"]
        assert len(variant.bullets) > len(base.bullets)


# ── parsing ───────────────────────────────────────────────────────────────────

def test_every_profile_parses(profiles):
    for path in profiles:
        fixture = load_profile(path)
        assert fixture.experiences, path.name
        assert fixture.projects, path.name
        assert fixture.skills, path.name
        assert fixture.education, f"{path.name} has no parsed education"
        assert all(b.strip() for b in fixture.bullets), path.name


def test_every_experience_and_project_carries_bullets(profiles):
    for path in profiles:
        fixture = load_profile(path)
        for item in fixture.experiences + fixture.projects:
            assert item["bullets"], f"{path.name}: {item} has no bullets"


def test_no_profile_reuses_another_profile_identity():
    """Two synthetic people sharing a name would make a per-profile row
    ambiguous in the suite's `per_profile` table."""
    names = [c.slot.name for c in all_profiles() if not c.redundant]
    assert len(set(names)) == len(names), f"duplicate identities: {names}"


# ── stratum coverage ──────────────────────────────────────────────────────────

def test_every_profile_declares_every_reported_stratum(profiles):
    for path in profiles:
        missing = load_meta(path).missing
        assert not missing, f"{path.name} declares no {', '.join(missing)}"


def test_every_declared_value_is_in_the_ontology(profiles):
    for path in profiles:
        for axis, value in _strata(path).items():
            assert value in STRATA[axis], f"{path.name}: {axis}={value}"


def test_each_family_carries_at_least_three_authored_profiles(profiles):
    """n=1 per stratum confounds the stratum with the person (#172)."""
    counts = collections.Counter(
        _strata(p)["role_family"] for p in profiles
        if not p.stem.endswith(REDUNDANT_SUFFIX))
    assert set(counts) == set(STRATA["role_family"])
    for family, n in counts.items():
        assert n >= MIN_PROFILES_PER_CELL, f"{family} has only {n}"


def test_both_levels_appear_in_every_family_and_every_variant(profiles):
    """Level must not be confounded with family or with the hazard variant."""
    by_family = collections.defaultdict(set)
    by_variant = collections.defaultdict(set)
    for path in profiles:
        strata = _strata(path)
        by_family[strata["role_family"]].add(strata["level"])
        by_variant[_variant_of(path)].add(strata["level"])

    assert all(len(levels) == 2 for levels in by_family.values()), dict(by_family)
    assert all(len(levels) == 2 for levels in by_variant.values()), dict(by_variant)


def _variant_of(path: Path) -> str:
    strata = _strata(path)
    if strata["redundancy"] == "bearing":
        return "redundant"
    if strata["evidence_density"] == "metric_poor":
        return "metric_poor"
    return strata["breadth"]


def test_every_hazard_axis_has_both_values_present(profiles):
    for axis in ("breadth", "evidence_density", "redundancy"):
        values = {_strata(p)[axis] for p in profiles}
        assert values == set(STRATA[axis]), f"{axis} only covers {values}"


# ── declared strata are true of the text ──────────────────────────────────────

def test_all_declared_strata_are_verified_by_the_text():
    failures = [f for f in check_all(PROFILES_DIR) if not f.ok]
    assert not failures, "\n".join(str(f) for f in failures)


def test_no_profile_reads_as_more_senior_than_junior(profiles):
    """A stray `senior` / `staff` / `principal` / `lead…` anywhere in a résumé
    promotes the whole document: `_detect_level` returns the *highest* tier
    matched. In an intern/entry corpus that silently scores `role_level`
    against a candidate the fixture does not describe — which is exactly what
    the retired `benchmark_profile.md` does (it detects as `lead`)."""
    for path in profiles:
        detected = measure(path)["detected_level"]
        assert detected in ALLOWED_LEVELS, f"{path.name} detects as {detected}"


def test_intern_profiles_avoid_the_junior_vocabulary(profiles):
    """`intern` is the lowest tier, so an intern profile that says `associate`
    or `junior` anywhere reports one level up from what it declares."""
    for path in profiles:
        if _strata(path)["level"] != "intern":
            continue
        assert measure(path)["detected_level"] == "intern", path.name


def test_the_redundancy_pair_separates_on_the_metrics(profiles):
    """#122's deviations record that its four modes could not fire on a fixture
    a human wrote to be non-redundant, and named the missing capability. This
    is the assertion that the capability now exists."""
    bearing = [p for p in profiles if _strata(p)["redundancy"] == "bearing"]
    clean = [p for p in profiles if _strata(p)["redundancy"] == "clean"]
    assert bearing and clean

    for path in bearing:
        fired = redundancy_modes_fired(measure(path))
        assert fired, f"{path.name} declares bearing and fires nothing"
    for path in clean:
        fired = redundancy_modes_fired(measure(path))
        assert not fired, f"{path.name} declares clean but fires {fired}"


def test_specialist_skills_come_from_the_declared_family(profiles):
    """Breadth is a vocabulary difference, and it must be the *right*
    vocabulary — a specialist listing another family's toolchain is a
    generalist that mislabelled itself."""
    for path in profiles:
        strata = _strata(path)
        if strata["breadth"] != "specialist":
            continue
        core = {s.lower() for s in FAMILY_VOCAB[strata["role_family"]]["core"]}
        names = {s["name"].lower() for s in load_profile(path).skills}
        assert names <= core, f"{path.name}: {sorted(names - core)} outside core"


# ── GitHub metrics (the #155 fixture gap) ─────────────────────────────────────

def test_github_metrics_name_projects_that_exist(profiles):
    for path in profiles:
        meta = load_meta(path)
        if meta.github_metrics:
            check_projects_exist(meta, [p["name"] for p in load_profile(path).projects])


def test_at_least_one_profile_per_family_carries_github_metrics(profiles):
    """`_github_signal()` returns None with no metrics, so the whole component
    drops out of `_complexity()` — the gap #155's deviations recorded."""
    families = {_strata(p)["role_family"] for p in profiles
                if load_meta(p).github_metrics}
    assert families == set(STRATA["role_family"]), (
        f"no GitHub metrics for: {set(STRATA['role_family']) - families}")


def test_seeded_metrics_produce_a_non_null_github_signal(profiles):
    """The point of seeding is that `_github_signal()` fires. Assert the signal
    itself, not the presence of the keys."""
    from agents.project_scorer import _github_signal

    seeded = 0
    for path in profiles:
        for metrics in load_meta(path).github_metrics.values():
            signal = _github_signal(metrics)
            assert signal is not None, f"{path.name}: {metrics}"
            assert 0.0 <= signal <= 1.0
            seeded += 1
    assert seeded >= 5


# ── retirement ────────────────────────────────────────────────────────────────

def test_the_legacy_mid_level_fixture_is_retired_but_still_present():
    """It is out of the intern/entry domain, so it must not land in a reported
    stratum — and it must not be deleted either: every historical benchmark
    figure and the committed cassette are measured on it."""
    legacy = PROFILES_DIR / "benchmark_profile.md"
    assert legacy.exists()
    assert load_meta(legacy).retired is True
    assert legacy not in discover_profiles(PROFILES_DIR)


def test_retired_profiles_still_load_and_parse():
    legacy = PROFILES_DIR / "benchmark_profile.md"
    assert load_profile(legacy).experiences
    assert check_profile(legacy), "retired profiles are still checkable"
