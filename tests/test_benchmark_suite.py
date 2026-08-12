"""Multi-profile benchmark suite (issue #172).

The merge layer is pure and tested directly. The subprocess-per-profile choice
is not a preference: `database/db.py` builds `engine` at *import* time from a
`config.DATABASE_URL` also read at import, so a second `run_benchmark()` inside
one process keeps writing to the first run's throwaway database. One test pins
that reasoning so the driver is not "simplified" into an in-process loop later.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from eval.benchmark_suite import discover_profiles, merge  # noqa: E402


def _run(profile, strata, deltas, families=None):
    families = families or ["data_science"] * len(deltas)
    return {
        "profile": profile,
        "profile_strata": strata,
        "failed": [],
        "aggregate": {"tasks": len(deltas)},
        "task_results": [
            {"task_id": f"{profile}-{i}", "role_family": fam, "level": "entry",
             "n_attempts": 1,
             "metrics": {"ats": {"delta": d, "baseline_composite": 50.0,
                                 "tailored_composite": 50.0 + d},
                         "experience_allocation": {}, "skills": {},
                         "redundancy": {}}}
            for i, (d, fam) in enumerate(zip(deltas, families))
        ],
    }


# ── merge ──────────────────────────────────────────────────────────────────────

def test_merge_pools_across_profiles_and_keeps_each_profile_separately():
    merged = merge([
        _run("a.md", {"breadth": "specialist"}, [10.0, 20.0]),
        _run("b.md", {"breadth": "generalist"}, [40.0, 50.0]),
    ])
    assert merged["profiles"] == 2 and merged["tasks"] == 4
    assert merged["aggregate"]["ats_delta"]["mean"] == 30.0
    assert merged["per_profile"]["a.md"]["aggregate"]["tasks"] == 2
    assert merged["per_profile"]["b.md"]["strata"]["breadth"] == "generalist"


def test_candidate_strata_slice_across_profiles():
    """The finding the whole suite exists to make sayable."""
    merged = merge([
        _run("a.md", {"breadth": "specialist"}, [10.0, 20.0]),
        _run("b.md", {"breadth": "generalist"}, [40.0, 50.0]),
    ])
    by_breadth = merged["aggregate_by_stratum"]["profile_breadth"]
    assert by_breadth["specialist"]["ats_delta"]["mean"] == 15.0
    assert by_breadth["generalist"]["ats_delta"]["mean"] == 45.0


def test_candidate_and_posting_axes_are_namespaced_apart():
    """Both sides define `role_family`; pooling them would be meaningless.

    A candidate whose own family is ML engineering, tailoring to data-science
    postings, must not have those two collapse into one bucket.
    """
    merged = merge([
        _run("a.md", {"role_family": "ml_engineering"}, [10.0, 30.0],
             families=["data_science", "software_engineering"]),
    ])
    by = merged["aggregate_by_stratum"]
    assert set(by["role_family"]) == {"data_science", "software_engineering"}
    assert set(by["profile_role_family"]) == {"ml_engineering"}


def test_the_pooled_figure_survives_the_split():
    """Every historical CHANGELOG table is pooled; dropping it would break
    comparability with everything already recorded."""
    merged = merge([_run("a.md", {}, [10.0]), _run("b.md", {}, [20.0])])
    assert merged["aggregate"]["ats_delta"]["mean"] == 15.0


def test_failed_tasks_are_excluded_from_the_merge_but_still_reported():
    run = _run("a.md", {}, [10.0])
    run["task_results"].append({"task_id": "boom", "error": "kaboom"})
    run["failed"] = ["boom"]
    merged = merge([run])
    assert merged["tasks"] == 1
    assert merged["per_profile"]["a.md"]["failed"] == ["boom"]


def test_a_profile_without_a_sidecar_still_merges():
    merged = merge([_run("plain.md", {}, [10.0, 12.0])])
    assert merged["tasks"] == 2
    assert merged["per_profile"]["plain.md"]["strata"] == {}


# ── discovery ──────────────────────────────────────────────────────────────────

def test_profiles_are_discovered_in_a_stable_order():
    """A glob is unordered; an unordered iteration feeding a reported table is
    the bug class #158 and #171 each paid for."""
    found = discover_profiles()
    assert found == sorted(found)
    assert all(p.suffix == ".md" for p in found)


def test_sidecars_are_not_mistaken_for_profiles():
    assert not any(p.name.endswith(".meta.json") for p in discover_profiles())


# ── the reason for subprocess isolation ───────────────────────────────────────

def test_the_engine_is_bound_at_import_which_is_why_profiles_get_subprocesses():
    """Pins the constraint behind the design.

    `database/db.py` calls `create_engine(DATABASE_URL)` at module scope, so
    rebinding `DATABASE_URL` afterwards cannot move an already-imported engine.
    A second `run_benchmark()` in one process would therefore write into the
    first run's throwaway database. If this ever stops being true the suite can
    be simplified — until then, an in-process loop is silently wrong.
    """
    source = (ROOT / "database" / "db.py").read_text(encoding="utf-8")
    module_level = [line for line in source.splitlines()
                    if line.startswith("engine = create_engine(")]
    assert module_level, "database/db.py no longer binds `engine` at import"
