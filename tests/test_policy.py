"""The policy artifact (issue #241): one versioned home for fitted thresholds and guard tolerances.

`harness/policy.json` holds every value with the fit it came from. This file pins four things:
the artifact equals the constants that shipped before it existed (a consolidation, not a refit);
the modules read it and fall back to the same numbers without it; every committed node records
the version it ran under; and, for each fit script, a re-fit that changes a value fails here until
the artifact is updated. The per-module label tests keep the rest of each fit's checks (headroom,
categories); the "constant equals the fit" assertion lives here only.
"""

import ast
import contextlib
import json
import tomllib
from pathlib import Path
from uuid import UUID

import pytest
from sqlmodel import Session

from harness import policy
from harness.decisions import engine, recordings

ROOT = Path(__file__).resolve().parent.parent
DOC = json.loads(policy.POLICY_PATH.read_text(encoding="utf-8"))
VALUES = DOC["values"]

# The numbers that shipped before the artifact: every one must still be what a module sees.
# A new value is added here on purpose; a changed one fails here, then in its drift test.
EXPECTED = {
    "support.tau_block": 0.85, "support.tau_review": 0.35,
    "support_fallback.tau_block": 1.0, "support_fallback.tau_review": 0.5,
    "negative_pin.tau_block": 0.85, "negative_pin.tau_review": 0.15,
    "coverage.tau_cover": 0.65,
    "memory_gate.v1.tau_lo": 0.25, "memory_gate.v1.tau_hi": 0.65, "memory_gate.v1.tau_target": 0.75,
    "memory_gate.v2.tau_lo": 0.20, "memory_gate.v2.tau_hi": 0.65, "memory_gate.v2.tau_target": 0.75,
    "memory_gate.hi_floor": 0.5, "memory_gate.hard_mass": 0.25, "memory_gate.v1_would_be_writes": 29,
    "library.tau_variant": 0.50, "library.tau_baseline": 0.50, "library.tau_floor": 0.5,
    "library.variant_match_floor": 0.10,
    "guards.stuffing": 0.0, "guards.verb_entropy": 0.35, "guards.mtld": 0.10,
    "guards.duplication": 0.15, "guards.variant_drift": 0.35,
}


def _modules():
    from harness import acceptance, library
    from harness.decisions import (
        coverage, library as jev_library, memory_gate, negative_pins, support, support_fallback,
    )
    return acceptance, library, coverage, jev_library, memory_gate, negative_pins, support, support_fallback


def _module_values():
    acceptance, library, coverage, jl, mg, neg, sup, fb = _modules()
    return {
        "support.tau_block": sup.TAU_BLOCK, "support.tau_review": sup.TAU_REVIEW,
        "support_fallback.tau_block": fb.TAU_BLOCK, "support_fallback.tau_review": fb.TAU_REVIEW,
        "negative_pin.tau_block": neg.TAU_BLOCK, "negative_pin.tau_review": neg.TAU_REVIEW,
        "coverage.tau_cover": coverage.TAU_COVER,
        "memory_gate.v1.tau_lo": mg.TAU_LO, "memory_gate.v1.tau_hi": mg.TAU_HI,
        "memory_gate.v1.tau_target": mg.TAU_TARGET,
        "memory_gate.v2.tau_lo": mg.TAU_LO_V2, "memory_gate.v2.tau_hi": mg.TAU_HI_V2,
        "memory_gate.v2.tau_target": mg.TAU_TARGET_V2,
        "memory_gate.hi_floor": mg.HI_FLOOR, "memory_gate.hard_mass": mg.HARD_MASS,
        "memory_gate.v1_would_be_writes": mg.V1_WOULD_BE_WRITES,
        "library.tau_variant": jl.TAU_VARIANT, "library.tau_baseline": jl.TAU_BASELINE,
        "library.tau_floor": jl.TAU_FLOOR, "library.variant_match_floor": library.VARIANT_MATCH_FLOOR,
        "guards.stuffing": acceptance.DEFAULT_TOLERANCES["stuffing"],
        "guards.verb_entropy": acceptance.DEFAULT_TOLERANCES["verb_entropy"],
        "guards.mtld": acceptance.DEFAULT_TOLERANCES["mtld"],
        "guards.duplication": acceptance.DEFAULT_TOLERANCES["duplication"],
        "guards.variant_drift": acceptance.DEFAULT_TOLERANCES["variant_drift"],
    }


@contextlib.contextmanager
def _policy_file(path):
    """Point the loader at `path` for a block, then back at the committed artifact."""
    saved = policy.POLICY_PATH
    policy.POLICY_PATH = path
    policy.reload()
    try:
        yield
    finally:
        policy.POLICY_PATH = saved
        policy.reload()


# ── the artifact is what shipped, and well formed ────────────────────────────

def test_the_artifact_holds_exactly_the_values_that_shipped_before_it():
    assert {k: v["value"] for k, v in VALUES.items()} == EXPECTED
    assert [type(v["value"]) for v in VALUES.values()] == [type(v) for v in EXPECTED.values()]


def test_the_fallback_table_equals_the_artifact():
    assert policy._FALLBACK == EXPECTED
    assert set(policy._FALLBACK) == set(VALUES)


def test_the_modules_see_the_artifact_values():
    assert _module_values() == EXPECTED


def test_the_loader_falls_back_to_the_same_values_when_the_artifact_is_missing_or_broken(tmp_path):
    bad = json.dumps({"version": 9, "values": {"support.tau_block": {"value": "x"}}})
    for content in (None, "{not json", "[]", bad):
        path = tmp_path / "policy.json"
        if content is not None:
            path.write_text(content, encoding="utf-8")
        with _policy_file(path if content is not None else tmp_path / "absent.json"):
            assert {k: policy.get(k) for k in EXPECTED} == EXPECTED
            assert [type(policy.get(k)) for k in EXPECTED] == [type(v) for v in EXPECTED.values()]
    assert policy.version() == str(DOC["version"])


def test_an_unversioned_policy_says_so(tmp_path):
    with _policy_file(tmp_path / "absent.json"):
        assert policy.version() == policy.UNVERSIONED


def test_a_value_in_the_file_wins_over_the_fallback(tmp_path):
    path = tmp_path / "policy.json"
    path.write_text(json.dumps({"version": 7, "values": {"support.tau_block": {"value": 0.9},
                                                        "memory_gate.v1_would_be_writes": {"value": 30}}}),
                    encoding="utf-8")
    with _policy_file(path):
        assert policy.get("support.tau_block") == 0.9 and policy.get("support.tau_review") == 0.35
        assert policy.get("memory_gate.v1_would_be_writes") == 30
        assert isinstance(policy.get("memory_gate.v1_would_be_writes"), int)
        assert policy.version() == "7"


def test_no_module_hard_codes_a_value_the_artifact_holds():
    """Each constant is assigned from `policy.get`, so a number cannot be edited in a module and
    leave the artifact behind. (The fallback table in policy.py is the one place the numbers repeat.)"""
    wanted = {  # module path -> names that must be `policy.get(...)`
        "harness/decisions/support.py": {"TAU_BLOCK", "TAU_REVIEW"},
        "harness/decisions/support_fallback.py": {"TAU_BLOCK", "TAU_REVIEW"},
        "harness/decisions/negative_pins.py": {"TAU_BLOCK", "TAU_REVIEW"},
        "harness/decisions/coverage.py": {"TAU_COVER"},
        "harness/decisions/memory_gate.py": {"TAU_LO", "TAU_HI", "TAU_TARGET", "TAU_LO_V2", "TAU_HI_V2",
                                             "TAU_TARGET_V2", "HI_FLOOR", "HARD_MASS", "V1_WOULD_BE_WRITES"},
        "harness/decisions/library.py": {"TAU_VARIANT", "TAU_BASELINE", "TAU_FLOOR"},
        "harness/library.py": {"VARIANT_MATCH_FLOOR"},
        "harness/acceptance.py": {"VARIANT_DRIFT_TOLERANCE"},
    }
    for rel, names in wanted.items():
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        found = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
                found[node.targets[0].id] = node.value
        for name in names:
            call = found.get(name)
            assert (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                    and call.func.attr == "get" and getattr(call.func.value, "id", None) == "policy"), (rel, name)
    tols = next(n.value for n in ast.parse((ROOT / "harness/acceptance.py").read_text(encoding="utf-8")).body
                if isinstance(n, ast.AnnAssign) and getattr(n.target, "id", "") == "DEFAULT_TOLERANCES")
    assert all(isinstance(v, ast.Call) or getattr(v, "id", None) == "VARIANT_DRIFT_TOLERANCE"
               for v in tols.values)


def test_every_value_names_its_kind_issue_and_fit():
    kinds = {"fitted", "pinned", "rule", "provisional"}
    for key, entry in VALUES.items():
        assert entry["kind"] in kinds and isinstance(entry["source_issue"], int), key
        if entry["kind"] in ("fitted", "pinned"):
            assert entry["fit"] in DOC["fits"], key
        else:
            assert entry["fit"] is None and entry["note"].strip(), key
    for key, entry in VALUES.items():
        if entry["kind"] == "pinned":
            assert entry["pinned_to"] in VALUES and entry["fit_alone"] is not None, key
            assert entry["value"] >= VALUES[entry["pinned_to"]]["value"], key       # never looser (#244)
    assert {e["fit"] for e in VALUES.values() if e["fit"]} == set(DOC["fits"])


def test_the_digest_covers_the_values_so_a_change_must_be_versioned():
    """Change a value and this fails until `digest` is updated; update `version` with it, since
    `version` is what every committed node records."""
    assert DOC["digest"] == policy.digest(VALUES), f"policy.json values changed: digest is {policy.digest(VALUES)}"
    assert policy.digest({**VALUES, "support.tau_block": {**VALUES["support.tau_block"], "value": 0.8}}) != DOC["digest"]


def test_the_artifact_ships_in_the_wheel():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    only = pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["only-include"]
    rel = policy.POLICY_PATH.relative_to(ROOT).as_posix()
    assert policy.POLICY_PATH.is_file() and any(rel == p or rel.startswith(p.rstrip("/") + "/") for p in only), rel


# ── each fit is on the committed files it names ──────────────────────────────

def test_each_fit_names_files_that_exist_with_the_hashes_recorded():
    """A relabelled set or a new recording is a new fit: its hash moves and this fails until the
    artifact is updated (after the fit script has been re-run)."""
    for name, fit in DOC["fits"].items():
        assert (ROOT / fit["script"]).is_file(), name
        assert fit["model"] and fit["fit_date"] and isinstance(fit["source_issue"], int), name
        for kind in ("labels", "recordings"):
            # A fallback fit (#351) has no recordings: its "model" is code, so there is no answer to record.
            assert fit[kind] or (kind == "recordings" and fit["model"].startswith("fallback-")), (name, kind)
            for f in fit[kind]:
                assert policy.file_sha256(ROOT / f["path"]) == f["sha256"], (name, f["path"])


# ── a re-fit that changes a value fails until the artifact is updated ────────

def _replay(monkeypatch, *paths):
    monkeypatch.setenv("ART_JEV_MODE", "replay")
    monkeypatch.setattr(engine, "get_client", lambda: pytest.fail("replay reached for a client"))
    engine.reset_stats()
    for p in paths:
        recordings.import_recordings(json.loads(Path(p).read_text(encoding="utf-8")))


def _drift(fitted, values=None):
    """The keys whose artifact value differs from what a fit script recommends now (`{}` = none)."""
    values = VALUES if values is None else values
    return {k: (values[k]["value"], v) for k, v in fitted.items() if values[k]["value"] != v}


def test_a_changed_fit_is_reported_as_drift():
    held = {"support.tau_block": {"value": 0.85}, "support.tau_review": {"value": 0.35}}
    assert _drift({"support.tau_block": 0.85, "support.tau_review": 0.35}, held) == {}
    assert _drift({"support.tau_block": 0.80, "support.tau_review": 0.35}, held) == {"support.tau_block": (0.85, 0.80)}


def test_support_thresholds_are_what_the_fit_recommends(isolated_engine, monkeypatch):
    from eval import fit_support_threshold as fit
    _replay(monkeypatch, fit.RECORDINGS_PATH)
    rec = fit.recommend(fit.run_pairs(fit.load_pairs()))
    assert _drift({"support.tau_block": rec["tau_block"], "support.tau_review": rec["tau_review"]}) == {}


def test_support_fallback_cutoffs_are_what_the_fit_recommends():
    """The deterministic check's cutoffs (#351) are fitted on the same labels, with no recording and no
    Jev: re-running the fit on the committed pairs gives the artifact's values. A change to the lists
    that moves a score moves the fit, and this fails until the artifact is updated."""
    from eval import fit_support_fallback as fit
    from harness.decisions import support_fallback
    rec = fit.recommend(fit.run_pairs(fit.load_pairs()))
    assert _drift({"support_fallback.tau_block": rec["tau_block"],
                   "support_fallback.tau_review": rec["tau_review"]}) == {}
    entry = DOC["fits"]["support_fallback"]
    assert entry["model"] == support_fallback.MODEL and entry["decision"] == support_fallback.VERSION
    assert entry["recordings"] == []


def test_negative_pin_thresholds_are_what_the_fit_recommends(isolated_engine, monkeypatch):
    from eval import fit_negative_pin_threshold as fit
    _replay(monkeypatch, fit.RECORDINGS_PATH)
    rec = fit.recommend(fit.run_pairs(fit.load_pairs()))
    assert _drift({"negative_pin.tau_block": rec["tau_block"], "negative_pin.tau_review": rec["tau_review"]}) == {}


def test_the_coverage_threshold_is_what_the_fit_recommends(isolated_engine, monkeypatch):
    from eval import fit_coverage_threshold as fit
    _replay(monkeypatch, fit.RECORDINGS_PATH)
    rec = fit.recommend(fit.run_pairs(fit.load_pairs()))
    assert _drift({"coverage.tau_cover": rec["tau_cover"]}) == {}


def test_memory_gate_v1_thresholds_are_what_the_fit_recommends(isolated_engine, monkeypatch):
    from eval import fit_memory_gate_threshold as fit
    _replay(monkeypatch, fit.RECORDINGS_PATH)
    rows = fit.replay_rows(fit.load_doc())[0]
    rec = fit.recommend(rows)
    assert _drift({"memory_gate.v1.tau_lo": rec["tau_lo"], "memory_gate.v1.tau_hi": rec["tau_hi"],
                   "memory_gate.v1.tau_target": rec["tau_target"]}) == {}


def test_memory_gate_v2_thresholds_are_the_pinned_fit(isolated_engine, monkeypatch):
    """v2's fit alone is looser than v1 (0.50 / 0.40); the artifact holds the pinned values (#244)
    and records the fit alone, which must still be what the script computes."""
    from eval import fit_memory_gate_context as ctx
    from eval import fit_memory_gate_threshold as base
    _replay(monkeypatch, base.RECORDINGS_PATH, ctx.RECORDINGS_PATH)
    rows = ctx.replay_rows(ctx.load_doc())[0]
    rec = ctx.recommend(rows)
    assert rec["pinned"]
    assert _drift({"memory_gate.v2.tau_lo": rec["tau_lo"], "memory_gate.v2.tau_hi": rec["tau_hi"],
                   "memory_gate.v2.tau_target": rec["tau_target"]}) == {}
    assert VALUES["memory_gate.v2.tau_hi"]["fit_alone"] == rec["raw"]["tau_hi"]
    assert VALUES["memory_gate.v2.tau_target"]["fit_alone"] == rec["raw"]["tau_target"]
    assert VALUES["memory_gate.v1_would_be_writes"]["value"] == len(
        [r for r in base.replay_rows(base.load_doc())[0]
         if base.route_row(r, VALUES["memory_gate.v1.tau_lo"]["value"], VALUES["memory_gate.v1.tau_hi"]["value"],
                           VALUES["memory_gate.v1.tau_target"]["value"], without=base.FIT_WITHOUT)["action"] == "write"])


def test_library_thresholds_are_what_the_fit_recommends(isolated_engine, monkeypatch):
    from eval import fit_library_thresholds as fit
    _replay(monkeypatch, fit.RECORDINGS_PATH)
    vrows, brows, _ = fit.replay_rows(fit.load_variants(), fit.load_baselines())
    rec = fit.recommend(vrows, brows)
    assert _drift({"library.tau_variant": rec["tau_variant"], "library.tau_baseline": rec["tau_baseline"]}) == {}


# ── every committed node records the policy it ran under ─────────────────────

@pytest.fixture()
def job(isolated_engine):
    from conftest import _seed_user_and_skill
    from test_job_card import _seed_job_and_result
    user = _seed_user_and_skill(isolated_engine)
    content = {"experiences": [{"title": "Intern", "company": "Acme", "bullets": ["Built a pipeline"]}],
               "projects": [], "skills_ranked": ["Python"]}
    job_id, result_id = _seed_job_and_result(isolated_engine, user.user_id, content=content)
    return user.user_id, job_id, content


def test_every_commit_path_stamps_the_policy_version(job):
    from harness import tree
    uid, jid, content = job
    pipeline = tree.record_result(uid, jid, source="pipeline")
    host = tree.commit_node(uid, jid, content={**content, "skills_ranked": ["SQL"]}, source="host",
                            provenance={"host": "claude-code"})
    assert pipeline["provenance"]["policy_version"] == host["provenance"]["policy_version"] == str(DOC["version"])
    assert host["provenance"]["host"] == "claude-code"
    assert tree.get_node(uid, host["node_id"])["provenance"]["policy_version"] == str(DOC["version"])


def test_a_node_from_before_the_stamp_still_loads(job, isolated_engine):
    from database.models import TailorNode
    from harness import tree
    uid, jid, content = job
    node = tree.record_result(uid, jid, source="pipeline")
    with Session(isolated_engine) as s:
        row = s.get(TailorNode, UUID(node["node_id"]))
        row.provenance = {"host": "art", "art_version": "0.1.0"}
        s.add(row)
        s.commit()
    old = tree.get_node(uid, node["node_id"])
    assert "policy_version" not in old["provenance"] and old["content"] == content
    assert [n["node_id"] for n in tree.history(uid, jid)] == [node["node_id"]]
    newer = tree.commit_node(uid, jid, content={**content, "skills_ranked": ["SQL"]}, source="host")
    assert newer["provenance"]["policy_version"] == str(DOC["version"])


def test_a_caller_may_override_the_stamp_and_a_missing_artifact_stamps_unversioned(job, tmp_path):
    from harness import tree
    uid, jid, content = job
    kept = tree.commit_node(uid, jid, content=content, source="host", provenance={"policy_version": "imported"})
    assert kept["provenance"]["policy_version"] == "imported"
    with _policy_file(tmp_path / "absent.json"):
        bare = tree.commit_node(uid, jid, content={**content, "skills_ranked": ["R"]}, source="host")
    assert bare["provenance"]["policy_version"] == policy.UNVERSIONED
