"""The labelled set for fitting the negative-pin check's thresholds, and its recordings (issue #232).

`eval/negative_pin_labels/pairs.json` holds hand-proposed (text, pin) labels over the synthetic
benchmark profiles; `recordings.json` holds the real Jev answers to `negative_pin@v1` for every
pair, recorded once. Nothing here calls the API: the key is removed from every test's
environment, and the replay tests fail on any miss instead of falling back.
"""

import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

from eval import fit_negative_pin_threshold as fit
from harness.decisions import engine, negative_pins, recordings
from harness.decisions.client import JevReplayMiss
from harness.decisions.negative_pins import VERSION, term_pattern

ROOT = Path(__file__).resolve().parent.parent
PAIRS_DOC = json.loads(fit.PAIRS_PATH.read_text(encoding="utf-8"))
PAIRS = PAIRS_DOC["pairs"]
EXPECTED_LABEL = {"direct": "mentions", "paraphrase": "mentions", "indirect": "mentions",
                  "near_miss": "does_not_mention", "unrelated": "does_not_mention"}


def _replay(monkeypatch):
    monkeypatch.setenv("ART_JEV_MODE", "replay")
    monkeypatch.setattr(engine, "get_client", lambda: pytest.fail("replay reached for a client"))
    engine.reset_stats()
    recordings.import_recordings(json.loads(fit.RECORDINGS_PATH.read_text(encoding="utf-8")))


# ── the set ──────────────────────────────────────────────────────────────────

def test_the_set_is_versioned_commented_and_large_enough():
    assert PAIRS_DOC["_comment"] and PAIRS_DOC["version"] == 1
    assert tuple(PAIRS_DOC["labels"]) == fit.LABELS
    assert len(PAIRS) >= 50
    assert len({p["id"] for p in PAIRS}) == len(PAIRS)
    for p in PAIRS:
        assert {"id", "profile", "category", "kind", "pin", "text", "label", "rationale"} <= set(p), p["id"]
        assert p["label"] in fit.LABELS and p["rationale"].strip() and p["text"].strip()
        assert p["pin"]["term"].strip() and p["pin"]["statement"].strip()
        assert p["kind"] in ("bullet", *negative_pins.FIELDS)


def test_every_category_has_at_least_six_pairs_and_the_labels_mean_what_the_categories_say():
    by_cat = Counter(p["category"] for p in PAIRS)
    assert set(by_cat) == set(fit.CATEGORY_ORDER)
    assert all(n >= 6 for n in by_cat.values()), by_cat
    for p in PAIRS:
        if p["category"] in EXPECTED_LABEL:
            assert p["label"] == EXPECTED_LABEL[p["category"]], p["id"]
    assert {p["label"] for p in PAIRS if p["category"] == "negation"} == set(fit.LABELS)
    assert sum(p["kind"] != "bullet" for p in PAIRS) >= 6                   # item fields are covered too


def test_only_direct_pairs_contain_the_term_so_every_other_hit_is_one_only_jev_can_add():
    for p in PAIRS:
        has_term = bool(term_pattern(p["pin"]["term"]).search(p["text"].lower()))
        assert has_term == (p["category"] == "direct"), p["id"]


def test_negation_pairs_carry_a_negation_or_a_comparison_and_never_reach_jev():
    for p in PAIRS:
        topic = fit.pair_topic(p)
        if p["category"] == "negation":
            assert negative_pins._NEGATION.search(p["pin"]["statement"]), p["id"]
            assert not negative_pins._NEGATION.search(topic), p["id"]        # the negation stays with the user


def test_text_is_verbatim_from_a_synthetic_profile_never_personal():
    profiles = ROOT / "eval" / "profiles"
    text = {}
    for p in PAIRS:
        path = profiles / f"{p['profile']}.md"
        assert path.is_file(), p["id"]
        text.setdefault(p["profile"], path.read_text(encoding="utf-8"))
        assert p["text"] in text[p["profile"]], (p["id"], p["text"])


# ── the recordings ───────────────────────────────────────────────────────────

def test_every_pair_replays_from_the_recordings_with_no_live_call(isolated_engine, monkeypatch):
    _replay(monkeypatch)
    rows = fit.run_pairs(PAIRS)
    assert len(rows) == len(PAIRS)
    assert all(r["source"] == "cache" and 0.0 <= r["p"] <= 1.0 for r in rows)
    stats = engine.stats()["negative_pin"]
    assert stats["hit_rate"] == 1.0 and stats["jev"] == 0 and stats["fallback"] == 0
    assert stats["cache"] == len(PAIRS) and stats["requests"] == 0


def test_a_pair_that_changed_since_it_was_recorded_fails_loudly(isolated_engine, monkeypatch):
    _replay(monkeypatch)
    with pytest.raises(JevReplayMiss):
        fit.ask({**PAIRS[0], "text": PAIRS[0]["text"] + " Extra."})
    with pytest.raises(JevReplayMiss):
        fit.ask({**PAIRS[0], "pin": {**PAIRS[0]["pin"], "statement": "Never mention something else"}})


def test_the_recordings_answer_the_current_question_and_carry_no_resume_text():
    doc = json.loads(fit.RECORDINGS_PATH.read_text(encoding="utf-8"))
    assert doc["format"] == recordings.FORMAT and doc["version"] == recordings.VERSION
    assert {d["question_version"] for d in doc["decisions"]} == {VERSION}
    assert {d["point"] for d in doc["decisions"]} == {"negative_pin"}
    unique = {(json.dumps(fit.pair_state(p), sort_keys=True), fit.pair_topic(p)) for p in PAIRS}
    assert len(doc["decisions"]) == len(unique)                    # two pairs may share a question
    blob = json.dumps(doc)
    # The state is never stored. (A company or project name can appear as a pin's topic, so only
    # the bullets are checked.)
    assert not any(p["text"] in blob for p in PAIRS if p["kind"] == "bullet")


# ── the analysis ─────────────────────────────────────────────────────────────

def _row(pid, label, p, cat="near_miss", jev=None):
    jev = jev or ("mentions" if p >= fit.JEV_YES else "does_not_mention")
    return {"id": pid, "label": label, "jev": jev, "category": cat, "p": p, "kind": "bullet", "topic": "t",
            "text": "x", "rationale": "r", "pin": {"term": "t", "statement": "s"}}


def test_tau_block_sits_a_step_above_the_worst_not_a_mention_pair_and_review_is_bounded():
    not_ = [_row(f"n{i}", "does_not_mention", p) for i, p in enumerate([0.02] * 8 + [0.05, 0.62])]
    ment = [_row(f"m{i}", "mentions", p, cat="paraphrase") for i, p in enumerate([0.45, 0.55, 0.75, 0.85, 0.92, 0.97])]
    rows = not_ + ment
    rec = fit.recommend(rows)
    assert rec["tau_block"] == 0.70 and rec["top_not"]["id"] == "n9" and rec["rule"] == "all"
    st = fit.block_stats(rows, rec["tau_block"])
    assert st["fp"] == 0 and st["tp"] == 4 and st["recall"] == pytest.approx(4 / 6)
    assert rec["tau_review"] < rec["tau_block"]
    assert fit.band_stats(rows, rec["tau_review"], rec["tau_block"])["not"] <= rec["noise_limit"]
    recalls = [fit.block_stats(rows, t)["recall"] for t in fit.GRID_BLOCK]
    assert recalls == sorted(recalls, reverse=True)                # a stricter threshold never gains recall


def test_the_rule_gives_up_headroom_over_a_negation_pair_before_it_blocks_a_near_miss():
    rows = [_row("hi", "does_not_mention", 0.93, cat="negation"), _row("nm", "does_not_mention", 0.10),
            _row("m", "mentions", 0.97, cat="direct")]
    rec = fit.recommend(rows)
    assert rec["rule"] == "core" and rec["tau_block"] == 0.40
    assert fit.block_stats([r for r in rows if r["category"] == "near_miss"], rec["tau_block"])["fp"] == 0


def test_confusion_and_disagreements_and_adjudication():
    rows = [_row("a", "does_not_mention", 0.1), _row("b", "does_not_mention", 0.9),
            _row("c", "mentions", 0.2, cat="indirect")]
    con = fit.confusion(rows)
    assert con["does_not_mention"] == {"mentions": 1, "does_not_mention": 1}
    assert con["mentions"] == {"mentions": 0, "does_not_mention": 1}
    assert [r["id"] for r in fit.disagreements(rows)] == ["b", "c"]
    assert [r["label"] for r in fit.relabelled_to_jev(rows)] == ["does_not_mention", "mentions", "does_not_mention"]
    assert [r["id"] for r in fit.agreed_only(rows)] == ["a"]


def test_the_analysis_matches_what_the_gate_computes(isolated_engine, monkeypatch):
    """`is_blocked` here is `violations` there, and the review band is `reviews`."""
    _replay(monkeypatch)
    for r in fit.run_pairs(PAIRS):
        finding = {"item": "exp:x", "kind": r["kind"], "text": r["text"], "pin": r["pin"]["term"],
                   "topic": r["topic"], "status": "checked", "p": r["p"]}
        assert bool(negative_pins.violations([finding])) == (r["p"] >= negative_pins.TAU_BLOCK), r["id"]
        assert bool(negative_pins.reviews([finding])) == (
            negative_pins.TAU_REVIEW <= r["p"] < negative_pins.TAU_BLOCK), r["id"]
        assert fit.is_blocked(r, negative_pins.TAU_BLOCK) == bool(negative_pins.violations([finding]))


def test_the_thresholds_in_negative_pins_py_are_the_fit_and_block_no_near_miss(isolated_engine, monkeypatch):
    """`TAU_BLOCK` / `TAU_REVIEW` are what the analysis recommends from the confirmed labels, with
    headroom over every not-a-mention pair, no false block, and no mention passing silently. A
    relabelled pair or a new recording that moves the fit fails this."""
    _replay(monkeypatch)
    rows = fit.run_pairs(PAIRS)
    rec = fit.recommend(rows)
    assert (negative_pins.TAU_BLOCK, negative_pins.TAU_REVIEW) == (rec["tau_block"], rec["tau_review"])
    st = fit.block_stats(rows, negative_pins.TAU_BLOCK)
    assert st["fp"] == 0
    for c in fit.CORE_NEGATIVE_CATEGORIES:                        # zero blocks on near-miss and unrelated
        assert fit.block_stats([r for r in rows if r["category"] == c], negative_pins.TAU_BLOCK)["fp"] == 0
    assert negative_pins.TAU_BLOCK - rec["top_not"]["p"] >= fit.HEADROOM - 1e-9
    assert fit.band_stats(rows, negative_pins.TAU_REVIEW, negative_pins.TAU_BLOCK)["missed"] == 0
    assert st["recall"] >= 0.9                                    # and it still catches the paraphrases
    for c in ("direct", "paraphrase"):
        assert fit.block_stats([r for r in rows if r["category"] == c], negative_pins.TAU_BLOCK)["recall"] == 1.0


def test_the_committed_report_and_review_files_are_current_and_list_every_pair(isolated_engine, monkeypatch):
    _replay(monkeypatch)
    rows = fit.run_pairs(PAIRS)
    review = fit.REVIEW_PATH.read_text(encoding="utf-8")
    assert review == fit.render_review(rows) + "\n"
    report = fit.REPORT_PATH.read_text(encoding="utf-8")
    assert all(f"`{p['id']}`" in review and f"`{p['id']}`" in report for p in PAIRS)
    disagreements_first = review.index("## Disagreements with Jev") < review.index("## Agreements")
    assert disagreements_first
    meta = fit._meta(json.loads(fit.RECORDINGS_PATH.read_text(encoding="utf-8")), 1.0)
    assert report == fit.render_report(rows, meta) + "\n"


def test_analyze_runs_offline_at_a_full_hit_rate_from_the_command_line(tmp_path):
    report, review = tmp_path / "REPORT.md", tmp_path / "REVIEW.md"
    proc = subprocess.run([sys.executable, str(ROOT / "eval" / "fit_negative_pin_threshold.py"), "analyze",
                           "--report", str(report), "--review", str(review)],
                          capture_output=True, text=True, timeout=300, cwd=ROOT)
    assert proc.returncode == 0, proc.stderr
    assert "hit rate 100%" in proc.stdout
    assert "## Recommendation" in report.read_text(encoding="utf-8")
    assert review.read_text(encoding="utf-8") == fit.REVIEW_PATH.read_text(encoding="utf-8")
    assert report.read_text(encoding="utf-8") == fit.REPORT_PATH.read_text(encoding="utf-8")
