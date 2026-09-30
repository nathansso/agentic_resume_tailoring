"""The labelled set for fitting the semantic-coverage threshold, and its recordings (issue #126).

`eval/coverage_labels/pairs.json` holds hand-proposed (requirement, bullet) labels over the synthetic
benchmark profiles; `recordings.json` holds the real Jev answers to `requirement_covered@v1` for every
pair, recorded once. Nothing here calls the API: the key is removed from every test's environment, and
the replay tests fail on any miss instead of falling back.
"""

import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

from eval import fit_coverage_threshold as fit
from harness.acceptance import Context, metric_vector
from harness.decisions import coverage, engine, recordings
from harness.decisions.client import JevReplayMiss
from harness.decisions.coverage import TAU_COVER, VERSION, make_coverage_checker

ROOT = Path(__file__).resolve().parent.parent
PAIRS_DOC = json.loads(fit.PAIRS_PATH.read_text(encoding="utf-8"))
PAIRS = PAIRS_DOC["pairs"]


def _replay(monkeypatch):
    monkeypatch.setenv("ART_JEV_MODE", "replay")
    monkeypatch.setattr(engine, "get_client", lambda: pytest.fail("replay reached for a client"))
    engine.reset_stats()
    recordings.import_recordings(json.loads(fit.RECORDINGS_PATH.read_text(encoding="utf-8")))


def _page(bullet):
    return {"experiences": [{"title": "Engineer", "company": "Acme", "bullets": [bullet]}], "projects": []}


# ── the set ──────────────────────────────────────────────────────────────────

def test_the_set_is_versioned_commented_and_large_enough():
    assert PAIRS_DOC["_comment"] and PAIRS_DOC["version"] == 1
    assert tuple(PAIRS_DOC["labels"]) == fit.LABELS
    assert len(PAIRS) >= 60
    assert len({p["id"] for p in PAIRS}) == len(PAIRS)
    for p in PAIRS:
        assert {"id", "origin", "profile", "category", "requirement", "text", "label", "rationale"} <= set(p), p["id"]
        assert p["label"] in fit.LABELS and p["rationale"].strip() and p["text"].strip()
        assert p["requirement"]["text"].strip() and p["requirement"]["type"] in coverage.ELIGIBLE
        assert p["origin"] in ("profile", "synthetic")


def test_every_category_has_at_least_six_pairs_and_the_labels_mean_what_the_categories_say():
    by_cat = Counter(p["category"] for p in PAIRS)
    assert set(by_cat) == set(fit.CATEGORY_ORDER)
    assert all(n >= 6 for n in by_cat.values()), by_cat
    for p in PAIRS:
        if fit.EXPECTED_LABEL[p["category"]] != "mixed":
            assert p["label"] == fit.EXPECTED_LABEL[p["category"]], p["id"]
    assert {p["label"] for p in PAIRS if p["category"] == "boundary"} == set(fit.LABELS)


def test_only_aspiration_bullets_are_synthetic_and_every_other_is_verbatim_from_a_synthetic_profile():
    profiles = ROOT / "eval" / "profiles"
    text = {}
    for p in PAIRS:
        assert (p["origin"] == "synthetic") == (p["category"] == "aspiration"), p["id"]
        path = profiles / f"{p['profile']}.md"
        assert path.is_file(), p["id"]                                     # never personal/
        if p["origin"] == "profile":
            text.setdefault(p["profile"], path.read_text(encoding="utf-8"))
            assert p["text"] in text[p["profile"]], (p["id"], p["text"])


def test_literal_pairs_share_the_requirements_words_and_most_semantic_pairs_share_none():
    from agents.ats_scorer import ATSScoringEngine

    generic = {"experience", "experiences", "skills", "ability", "strong", "proficiency", "hands-on", "professional"}

    def shared(p):
        keywords = ATSScoringEngine._extract_keywords(p["requirement"]["text"]) - generic
        return {k for k in keywords if k[:4] in p["text"].lower()}        # a stem, so "tests" meets "testing"
    # A literal pair names the tool its requirement names; most semantic pairs are met in none of its words.
    assert all(shared(p) for p in PAIRS if p["category"] == "literal")
    assert sum(not shared(p) for p in PAIRS if p["category"] == "semantic") >= 8


def test_aspiration_bullets_state_interest_or_a_plan_about_the_required_tool():
    words = ("eager", "exploring", "interested", "excited", "keen", "looking forward", "planning", "learning",
             "completed a short course", "wants to learn")
    for p in PAIRS:
        if p["category"] == "aspiration":
            assert any(w in p["text"].lower() for w in words), p["id"]


# ── the recordings ───────────────────────────────────────────────────────────

def test_every_pair_replays_from_the_recordings_with_no_live_call(isolated_engine, monkeypatch):
    _replay(monkeypatch)
    rows = fit.run_pairs(PAIRS)
    assert len(rows) == len(PAIRS)
    assert all(r["source"] == "cache" and 0.0 <= r["p"] <= 1.0 for r in rows)
    stats = engine.stats()["requirement_covered"]
    assert stats["hit_rate"] == 1.0 and stats["jev"] == 0 and stats["fallback"] == 0
    assert stats["cache"] == len(PAIRS) and stats["requests"] == 0


def test_a_pair_that_changed_since_it_was_recorded_fails_loudly(isolated_engine, monkeypatch):
    _replay(monkeypatch)
    with pytest.raises(JevReplayMiss):
        fit.ask({**PAIRS[0], "text": PAIRS[0]["text"] + " Extra."})
    with pytest.raises(JevReplayMiss):
        fit.ask({**PAIRS[0], "requirement": {**PAIRS[0]["requirement"], "text": "Something else entirely."}})


def test_the_recordings_answer_the_current_question_and_carry_no_resume_text():
    doc = json.loads(fit.RECORDINGS_PATH.read_text(encoding="utf-8"))
    assert doc["format"] == recordings.FORMAT and doc["version"] == recordings.VERSION
    assert {d["question_version"] for d in doc["decisions"]} == {VERSION}
    assert {d["point"] for d in doc["decisions"]} == {"requirement_covered"}
    unique = {(json.dumps(fit.pair_state(p), sort_keys=True), fit.pair_question(p).canonical()) for p in PAIRS}
    assert len(doc["decisions"]) == len(unique)
    blob = json.dumps(doc)
    assert not any(p["text"] in blob for p in PAIRS)                    # the state (the bullet) is never stored


def test_the_checker_reads_the_recorded_answers_the_way_the_labels_say(isolated_engine, monkeypatch):
    """Through the real checker, one requirement and one bullet at a time: covered iff the label says so."""
    _replay(monkeypatch)
    wrong = []
    for p in PAIRS:
        checker = make_coverage_checker([p["requirement"]])
        result = checker(_page(p["text"]))
        assert result["status"] == "checked"
        if result["requirements"][0]["covered"] != (p["label"] == "covered"):
            wrong.append(p["id"])
    assert wrong == []
    stats = engine.stats()["requirement_covered"]
    assert stats["hit_rate"] == 1.0 and stats["jev"] == 0 and stats["fallback"] == 0


def test_the_etl_and_the_eager_to_learn_cases_on_real_recorded_answers(isolated_engine, monkeypatch):
    """The acceptance cases, on recorded Jev answers: moving 40M events a day is data engineering at scale, and
    "eager to learn Kubernetes" is not experience with Kubernetes though the word is there."""
    _replay(monkeypatch)
    by_id = {p["id"]: p for p in PAIRS}
    etl, eager = by_id["s_etl_scale"], by_id["a_k8s"]
    checker = make_coverage_checker([etl["requirement"]])
    content = _page(etl["text"])
    vector = metric_vector(content, Context(jd_text=etl["requirement"]["text"], coverage_checker=checker))
    assert vector["targets"]["semantic_coverage"] == 100.0
    assert vector["targets"]["coverage"] < vector["targets"]["semantic_coverage"]       # the literal score cannot see it
    aspiration = make_coverage_checker([{**eager["requirement"], "terms": ["kubernetes"]}])
    page = _page(eager["text"])
    assert aspiration(page)["requirements"][0]["covered"] is False
    dis = coverage.disagreement(aspiration(page), page)
    assert [e["present"] for e in dis["literal_only"]] == [["kubernetes"]]              # the stuffing signature


# ── the analysis ─────────────────────────────────────────────────────────────

def _row(pid, label, p, cat="near_miss", jev=None):
    jev = jev or ("covered" if p >= fit.JEV_YES else "not_covered")
    return {"id": pid, "label": label, "jev": jev, "category": cat, "p": p, "origin": "profile", "text": "x",
            "rationale": "r", "requirement": {"text": "r", "type": "required"}}


def test_tau_sits_a_step_above_the_worst_not_covered_pair_and_in_the_middle_of_the_gap():
    not_ = [_row(f"n{i}", "not_covered", p) for i, p in enumerate([0.02] * 8 + [0.05, 0.39])]
    cov = [_row(f"c{i}", "covered", p, cat="semantic") for i, p in enumerate([0.68, 0.80, 0.90, 0.95])]
    rows = not_ + cov
    rec = fit.recommend(rows)
    assert rec["rule"] == "all" and rec["top_not"]["id"] == "n9"
    assert rec["candidates"] == [0.45, 0.50, 0.55, 0.60, 0.65]               # the headroom floor, and the recall ceiling
    assert rec["tau_cover"] == 0.55                                           # farthest from 0.39 and 0.68
    st = fit.cover_stats(rows, rec["tau_cover"])
    assert st["fp"] == 0 and st["tp"] == 4 and st["recall"] == 1.0
    recalls = [fit.cover_stats(rows, t)["recall"] for t in fit.GRID]
    assert recalls == sorted(recalls, reverse=True)                          # a stricter threshold never gains recall


def test_the_rule_gives_up_headroom_over_a_partial_before_it_covers_an_aspiration():
    rows = [_row("part", "not_covered", 0.93, cat="partial"), _row("asp", "not_covered", 0.10, cat="aspiration"),
            _row("s", "covered", 0.97, cat="semantic")]
    rec = fit.recommend(rows)
    assert rec["rule"] == "core" and rec["tau_cover"] >= 0.15
    assert fit.cover_stats([r for r in rows if r["category"] == "aspiration"], rec["tau_cover"])["fp"] == 0


def test_more_semantic_recall_beats_a_wider_margin():
    rows = [_row("n", "not_covered", 0.10, cat="aspiration"), _row("s1", "covered", 0.40, cat="semantic"),
            _row("s2", "covered", 0.95, cat="semantic")]
    assert fit.recommend(rows)["tau_cover"] <= 0.40                          # 0.55 would be wider but loses s1


def test_confusion_and_disagreements_and_adjudication():
    rows = [_row("a", "not_covered", 0.1), _row("b", "not_covered", 0.9), _row("c", "covered", 0.2, cat="semantic")]
    con = fit.confusion(rows)
    assert con["not_covered"] == {"covered": 1, "not_covered": 1}
    assert con["covered"] == {"covered": 0, "not_covered": 1}
    assert [r["id"] for r in fit.disagreements(rows)] == ["b", "c"]
    assert [r["label"] for r in fit.relabelled_to_jev(rows)] == ["not_covered", "covered", "not_covered"]
    assert [r["id"] for r in fit.agreed_only(rows)] == ["a"]


def test_the_analysis_matches_what_the_checker_computes(isolated_engine, monkeypatch):
    """`is_covered` here is the checker's `covered` there, at the threshold in `coverage.py`."""
    _replay(monkeypatch)
    for r in fit.run_pairs(PAIRS):
        result = make_coverage_checker([r["requirement"]])(_page(r["text"]))
        assert result["requirements"][0]["covered"] == fit.is_covered(r, TAU_COVER), r["id"]
        assert result["requirements"][0]["p"] == r["p"]


def test_the_threshold_in_coverage_py_is_the_fit_and_covers_no_aspiration_or_near_miss(isolated_engine, monkeypatch):
    """`TAU_COVER` is what the analysis recommends from the labels, with headroom over every not-covered pair,
    no false cover, and every semantic match covered. A relabelled pair or a new recording that moves the fit
    fails this."""
    _replay(monkeypatch)
    rows = fit.run_pairs(PAIRS)
    rec = fit.recommend(rows)
    assert TAU_COVER == rec["tau_cover"]
    st = fit.cover_stats(rows, TAU_COVER)
    assert st["fp"] == 0
    for c in fit.CORE_NEGATIVE_CATEGORIES:                                   # zero covers on aspiration and near-miss
        assert fit.cover_stats([r for r in rows if r["category"] == c], TAU_COVER)["fp"] == 0
    assert rec["rule"] == "all" and TAU_COVER - rec["top_not"]["p"] >= fit.HEADROOM - 1e-9
    assert fit.cover_stats([r for r in rows if r["category"] == "semantic"], TAU_COVER)["recall"] == 1.0
    assert st["recall"] >= 0.9


def test_the_committed_report_and_review_files_are_current_and_list_every_pair(isolated_engine, monkeypatch):
    _replay(monkeypatch)
    rows = fit.run_pairs(PAIRS)
    review = fit.REVIEW_PATH.read_text(encoding="utf-8")
    assert review == fit.render_review(rows) + "\n"
    report = fit.REPORT_PATH.read_text(encoding="utf-8")
    assert all(f"`{p['id']}`" in review and f"`{p['id']}`" in report for p in PAIRS)
    assert review.index("## Disagreements with Jev") < review.index("## Agreements")      # disagreements first
    meta = fit._meta(json.loads(fit.RECORDINGS_PATH.read_text(encoding="utf-8")), 1.0)
    assert report == fit.render_report(rows, meta) + "\n"


def test_analyze_runs_offline_at_a_full_hit_rate_from_the_command_line(tmp_path):
    report, review = tmp_path / "REPORT.md", tmp_path / "REVIEW.md"
    proc = subprocess.run([sys.executable, str(ROOT / "eval" / "fit_coverage_threshold.py"), "analyze",
                           "--report", str(report), "--review", str(review)],
                          capture_output=True, text=True, timeout=300, cwd=ROOT)
    assert proc.returncode == 0, proc.stderr
    assert "hit rate 100%" in proc.stdout
    assert "## Recommendation" in report.read_text(encoding="utf-8")
    assert review.read_text(encoding="utf-8") == fit.REVIEW_PATH.read_text(encoding="utf-8")
    assert report.read_text(encoding="utf-8") == fit.REPORT_PATH.read_text(encoding="utf-8")
