"""The labelled set for fitting the support check's thresholds, and its recordings (issue #237).

`eval/support_labels/pairs.json` holds hand-proposed (evidence, original, bullet)
labels over the synthetic benchmark profiles; `recordings.json` holds the real
Jev answers to `support@v1` for every pair, recorded once. Nothing here calls
the API: the key is removed from every test's environment, and the replay tests
fail on any miss instead of falling back.
"""

import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

from eval import fit_support_threshold as fit
from harness.decisions import engine, recordings
from harness.decisions.client import JevReplayMiss
from harness.decisions.support import LABELS, QUESTION

ROOT = Path(__file__).resolve().parent.parent
PAIRS_DOC = json.loads(fit.PAIRS_PATH.read_text(encoding="utf-8"))
PAIRS = PAIRS_DOC["pairs"]
CATEGORIES = {"faithful_rewording", "grounded_weave", "role_inflation", "invented_scope_outcome",
              "ungrounded_tool", "contradiction", "boundary"}


def _replay(monkeypatch):
    monkeypatch.setenv("ART_JEV_MODE", "replay")
    monkeypatch.setattr(engine, "get_client", lambda: pytest.fail("replay reached for a client"))
    engine.reset_stats()
    recordings.import_recordings(json.loads(fit.RECORDINGS_PATH.read_text(encoding="utf-8")))


# ── the set ──────────────────────────────────────────────────────────────────

def test_the_set_is_versioned_commented_and_large_enough():
    assert PAIRS_DOC["_comment"] and PAIRS_DOC["version"] == 1
    assert tuple(PAIRS_DOC["labels"]) == tuple(LABELS)
    assert len(PAIRS) >= 60
    assert len({p["id"] for p in PAIRS}) == len(PAIRS)
    for p in PAIRS:
        assert {"id", "profile", "category", "evidence", "original", "bullet", "label", "rationale",
                "negation", "touches_numbers"} <= set(p), p["id"]
        assert p["label"] in LABELS and p["rationale"].strip() and p["evidence"]
        assert p["original"] is None or isinstance(p["original"], str)


def test_every_category_has_at_least_six_pairs_and_negation_is_covered():
    by_cat = Counter(p["category"] for p in PAIRS)
    assert set(by_cat) == CATEGORIES
    assert all(n >= 6 for n in by_cat.values()), by_cat
    assert sum(bool(p["negation"]) for p in PAIRS) >= 6
    assert {p["label"] for p in PAIRS} == set(LABELS)
    # The categories mean what they say.
    expect = {"faithful_rewording": {"supported"}, "grounded_weave": {"supported"},
              "role_inflation": {"adds_unsupported"}, "invented_scope_outcome": {"adds_unsupported"},
              "ungrounded_tool": {"adds_unsupported"}, "contradiction": {"contradicts"}}
    for p in PAIRS:
        if p["category"] in expect:
            assert p["label"] in expect[p["category"]], p["id"]


def test_evidence_is_verbatim_from_a_synthetic_profile_never_personal():
    profiles = ROOT / "eval" / "profiles"
    text = {}
    for p in PAIRS:
        path = profiles / f"{p['profile']}.md"
        assert path.is_file(), p["id"]
        text.setdefault(p["profile"], path.read_text(encoding="utf-8"))
        for e in p["evidence"]:
            assert e in text[p["profile"]], (p["id"], e)


# ── the recordings ───────────────────────────────────────────────────────────

def test_every_pair_replays_from_the_recordings_with_no_live_call(isolated_engine, monkeypatch):
    _replay(monkeypatch)
    rows = fit.run_pairs(PAIRS)
    assert len(rows) == len(PAIRS)
    assert all(r["source"] == "cache" and r["jev"] in LABELS for r in rows)
    stats = engine.stats()["support"]
    assert stats["hit_rate"] == 1.0 and stats["jev"] == 0 and stats["fallback"] == 0
    assert stats["cache"] == len(PAIRS) and stats["requests"] == 0
    for r in rows:
        assert set(r["probs"]) == set(LABELS) and 0.0 <= r["p_worst"] <= 1.0


def test_a_pair_that_changed_since_it_was_recorded_fails_loudly(isolated_engine, monkeypatch):
    _replay(monkeypatch)
    changed = {**PAIRS[0], "bullet": PAIRS[0]["bullet"] + " Extra."}
    with pytest.raises(JevReplayMiss):
        fit.ask(changed)


def test_the_recordings_answer_the_current_question_and_carry_no_resume_text():
    doc = json.loads(fit.RECORDINGS_PATH.read_text(encoding="utf-8"))
    assert doc["format"] == recordings.FORMAT and doc["version"] == recordings.VERSION
    assert len(doc["decisions"]) == len(PAIRS)
    assert {d["question_version"] for d in doc["decisions"]} == {QUESTION.version}
    blob = json.dumps(doc)
    assert not any(p["bullet"] in blob for p in PAIRS)         # the state is never stored


# ── the analysis ─────────────────────────────────────────────────────────────

def _row(pid, label, p, jev=None, cat="boundary", **extra):
    jev = jev or label
    probs = {"supported": round(1 - p, 4), "adds_unsupported": p, "contradicts": 0.0}
    return {"id": pid, "label": label, "jev": jev, "category": cat, "probs": probs, "p_worst": p,
            "p_sum": p, "negation": False, "touches_numbers": False, "evidence": ["e"], "original": None,
            "bullet": "b", "rationale": "r", **extra}


def test_tau_block_sits_a_step_above_the_worst_supported_pair_and_review_is_bounded():
    supported = [_row(f"s{i}", "supported", p) for i, p in enumerate([0.02] * 8 + [0.05, 0.62])]
    bad = [_row(f"b{i}", "adds_unsupported", p) for i, p in enumerate([0.45, 0.55, 0.75, 0.85, 0.92, 0.97])]
    rows = supported + bad
    rec = fit.recommend(rows)
    assert rec["tau_block"] == 0.70 and rec["top_supported"]["id"] == "s9"
    st = fit.block_stats(rows, rec["tau_block"])
    assert st["fp"] == 0 and st["tp"] == 4 and st["recall"] == pytest.approx(4 / 6)
    assert rec["tau_review"] < rec["tau_block"]
    assert fit.band_stats(rows, rec["tau_review"], rec["tau_block"])["supported"] <= rec["noise_limit"]
    # Recall never rises with a stricter threshold.
    recalls = [fit.block_stats(rows, t)["recall"] for t in fit.GRID_BLOCK]
    assert recalls == sorted(recalls, reverse=True)


def test_confusion_and_disagreements_and_the_alternative_score():
    rows = [_row("a", "supported", 0.1), _row("b", "supported", 0.9, jev="adds_unsupported"),
            _row("c", "contradicts", 0.5, jev="adds_unsupported")]
    con = fit.confusion(rows)
    assert con["supported"] == {"supported": 1, "adds_unsupported": 1, "contradicts": 0}
    assert [r["id"] for r in fit.disagreements(rows)] == ["b", "c"]
    assert [r["label"] for r in fit.relabelled_to_jev(rows)] == ["supported", "adds_unsupported", "contradicts"]
    assert [r["id"] for r in fit.agreed_only(rows)] == ["a"]
    assert fit.rescored([{**rows[0], "p_sum": 0.7}])[0]["p_worst"] == 0.7


def test_the_analysis_matches_what_the_gate_computes(isolated_engine, monkeypatch):
    """`p_worst` is `support._worst`, so blocking here is blocking in `violations`."""
    from harness.decisions import support
    _replay(monkeypatch)
    rows = fit.run_pairs(PAIRS)
    for r in rows:
        finding = {"item": "x", "bullet": r["bullet"], "status": "checked", "label": r["jev"],
                   "p": r["probs"][r["jev"]], "probabilities": r["probs"]}
        assert bool(support.violations([finding])) == (r["p_worst"] >= support.TAU_BLOCK), r["id"]
        assert bool(support.reviews([finding])) == (support.TAU_REVIEW <= r["p_worst"] < support.TAU_BLOCK), r["id"]


def test_the_committed_review_file_is_current_and_lists_every_pair(isolated_engine, monkeypatch):
    _replay(monkeypatch)
    rows = fit.run_pairs(PAIRS)
    review = fit.REVIEW_PATH.read_text(encoding="utf-8")
    assert review == fit.render_review(rows) + "\n"
    report = fit.REPORT_PATH.read_text(encoding="utf-8")
    assert all(f"`{p['id']}`" in review and f"`{p['id']}`" in report for p in PAIRS)


def test_analyze_runs_offline_at_a_full_hit_rate_from_the_command_line(tmp_path):
    report, review = tmp_path / "REPORT.md", tmp_path / "REVIEW.md"
    proc = subprocess.run([sys.executable, str(ROOT / "eval" / "fit_support_threshold.py"), "analyze",
                           "--report", str(report), "--review", str(review)],
                          capture_output=True, text=True, timeout=300, cwd=ROOT)
    assert proc.returncode == 0, proc.stderr
    assert "hit rate 100%" in proc.stdout
    text = report.read_text(encoding="utf-8")
    assert "## Recommendation" in text and "TAU_BLOCK" in text
    assert review.read_text(encoding="utf-8") == fit.REVIEW_PATH.read_text(encoding="utf-8")
