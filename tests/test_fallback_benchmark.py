"""The fallback benchmark (issue #350): every Jev decision point's fallback, scored on its labelled set.

`eval/fallback_benchmark.py` runs the six shipped fallbacks (support, negative pin, coverage, memory gate,
variant choice, track baseline) through the production functions with Jev off, on the sets those points'
thresholds were fitted on, and `eval/FALLBACKS.md` sets the result beside Jev's figures, read out of each
`REPORT.md`. This file pins the measured numbers the way `tests/test_policy.py` pins the fits: a change to
a fallback, a label or a REPORT fails here until the numbers are reviewed and the document regenerated.
Nothing here uses a key, a recording or the network.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from eval import fallback_benchmark as fb
from harness.decisions import engine

ROOT = Path(__file__).resolve().parent.parent

# The fallbacks' numbers on the committed labels. A fix to a fallback (#351, #352) changes them on
# purpose: update them here in the same change, with the fallback's own fit in harness/policy.json.
EXPECTED = {
    "support": {
        "n": 89, "should": 54, "supported": 35,
        # the #351 deterministic check alone (it was unchecked when #350 took the baseline: 0 blocked)
        "blocked": 36, "reviewed": 1, "silent": 17, "false_blocks": 0, "false_reviews": 0,
        "by_label": {"contradicts": [11, 14], "adds_unsupported": [25, 40]},
        "reasons": ["mode=off"],
        # the #123 numbers-and-names gate that runs beside it, on the cited text alone: #350's baseline
        "g123": {"blocked": 19, "by_label": {"contradicts": [6, 14], "adds_unsupported": [13, 40]},
                 "false_blocks": 1, "false_ids": ["bd_seaborn_python"]},
        # what the page is held to: either blocks
        "with123": {"blocked": 47, "by_label": {"contradicts": [13, 14], "adds_unsupported": [34, 40]},
                    "reviewed": 1, "silent": 6, "false_blocks": 1, "false_ids": ["bd_seaborn_python"]},
        "silent_ids": ["rw_dependency_free", "scope_no_internet", "scope_plagiarism_feature", "scope_hourly_refresh",
                       "contra_replay_overwrites", "bd_original_not_evidence"],
    },
    "negative_pin": {
        "n": 74, "mentions": 41, "other": 33, "blocked": 11, "reviewed": 0, "silent": 30,
        "false_blocks": 0, "false_ids": [],
    },
    "coverage": {"n": 131, "should": 58, "other": 73, "credited": 0, "false_covers": 0, "answered": 0},
}
EXPECTED_PIN_BLOCKED = {"direct": 11, "paraphrase": 0, "indirect": 0, "near_miss": 0, "unrelated": 0, "negation": 0}
EXPECTED_GATE = {
    "main": {"n": 111, "prefs": 67, "other": 44, "writes": 0, "host": 71, "drop": 40, "prefs_kept": 57,
             "prefs_dropped": 10, "other_to_host": 14},
    "context": {"n": 40, "prefs": 33, "other": 7, "writes": 0, "host": 36, "drop": 4, "prefs_kept": 33,
                "prefs_dropped": 0, "other_to_host": 3},
}
EXPECTED_LIBRARY = {
    # #352: the concept-fit variant pick and the duties-based track pick (before: 18/48 with 7 poor-fit picks
    # and 27/36 with 8 wrong tracks; `fb.BEFORE_352`). Fits, not held-out figures: see library_labels/FALLBACK_FIT.md.
    "variant_choice": {"fallback": {"n": 48, "correct": 41, "picks": 27, "right_picks": 26, "poor": 0, "wrong": 1, "lost": 6},
                       "constant": {"correct": 15, "picks": 0, "lost": 33}},
    "track_baseline": {"fallback": {"n": 36, "correct": 36, "picks": 22, "right_picks": 22, "poor": 0, "wrong": 0, "lost": 0},
                       "constant": {"correct": 14, "picks": 0, "lost": 22}},
}
# Jev's figures as the committed REPORT.md files report them, so a refit that moves one is noticed here
# (the document is regenerated from the same files).
EXPECTED_JEV = {
    "support": {"blocked": "51/54 (94%)", "contradicts": "14/14", "adds": "37/40", "false_blocks": "0/35",
                "silent": 0, "review_should": 3, "review_supported": 1},
    "negative_pin": {"blocked": "38/41", "false_blocks": "0/33", "direct": "11/11", "paraphrase": "14/14",
                     "indirect": "7/10", "near_miss": "0/16", "unrelated": "0/11", "negation": "6/12",
                     "review_total": 4, "review_mentions": 3},
    "coverage": {"covered": 58, "should": 58, "false_covers": 0, "other": 73},
    "memory_gate": {"kept": {"tp": 57, "fp": 0, "fn": 10}, "heuristic": {"tp": 57, "fp": 14, "fn": 10},
                    "writes": 15, "messages": 111, "wrong_writes": 0},
    "variant_choice": {"correct": "47/48 (98%)", "picks": 33, "right": 32, "bad": 0, "lost": 0},
    "track_baseline": {"correct": "35/36 (97%)", "picks": 21, "right": 21, "bad": 0, "lost": 1},
}


@pytest.fixture(scope="module")
def measured():
    return fb.measure()


@pytest.fixture(scope="module")
def jev():
    return fb.jev_figures()


def _subset(got, expected):
    """`got` restricted to the keys of `expected`, recursively."""
    if isinstance(expected, dict):
        return {k: _subset(got[k], v) for k, v in expected.items()}
    return got


# ── the pinned numbers ───────────────────────────────────────────────────────

@pytest.mark.parametrize("point", sorted(EXPECTED))
def test_the_fallback_numbers_are_pinned(measured, point):
    assert _subset(measured[point], EXPECTED[point]) == EXPECTED[point]


def test_the_pin_term_match_blocks_only_the_direct_mentions(measured):
    cats = measured["negative_pin"]["categories"]
    assert {c: v["blocked"] for c, v in cats.items()} == EXPECTED_PIN_BLOCKED
    assert cats["direct"]["mentions"] == cats["direct"]["n"] == 11


def test_the_memory_gate_fallback_writes_nothing_and_drops_what_the_prefilter_drops(measured):
    for which, exp in EXPECTED_GATE.items():
        assert _subset(measured["memory_gate"][which], exp) == exp
    assert len(measured["memory_gate"]["main"]["dropped_ids"]) == 10


@pytest.mark.parametrize("point", sorted(EXPECTED_LIBRARY))
def test_the_library_fallbacks_are_pinned(measured, point):
    got, exp = measured[point], EXPECTED_LIBRARY[point]
    assert _subset(got["fallback"], exp["fallback"]) == exp["fallback"]
    assert _subset(got["constant"], exp["constant"]) == exp["constant"]


@pytest.mark.parametrize("point", sorted(EXPECTED_LIBRARY))
def test_the_library_fallback_equals_the_row_in_library_labels_report(measured, jev, point):
    """The benchmark's fallback is the fit script's: the same numbers the committed REPORT.md prints."""
    f, rep = measured[point]["fallback"], jev[point]["report_fallback"]
    bad = "poor" if point == "variant_choice" else "wrong"
    assert (fb._frac(f["correct"], f["n"]), f["picks"], f["right_picks"], f[bad], f["lost"]) == (
        rep["correct"], rep["picks"], rep["right"], rep["bad"], rep["lost"])
    for cat, c in measured[point]["categories"].items():
        r = jev[point]["categories"][cat]
        assert (f"{c['correct']}/{c['n']}", c[bad]) == (r["fb_correct"], r["fb_bad"]), cat


# ── Jev's figures come from the REPORT.md files ──────────────────────────────

@pytest.mark.parametrize("point", sorted(EXPECTED_JEV))
def test_jev_figures_are_read_from_the_committed_reports(jev, point):
    got = jev[point]["jev"] if point in ("variant_choice", "track_baseline") else jev[point]
    assert _subset(got, EXPECTED_JEV[point]) == EXPECTED_JEV[point]


def test_no_fallback_is_safer_than_jev_on_the_harmful_error(measured, jev):
    """Jev's harmful-error count is 0 on every set that has one; each fallback's is higher, except where it
    cannot err (memory gate: no write; coverage: no gate). The support fallback leaves 6 should-block pairs
    past both the #351 check and #123, down from 54 kept silently when it was unchecked."""
    assert measured["support"]["with123"]["silent"] > jev["support"]["silent"] == 0
    jp = jev["negative_pin"]
    assert measured["negative_pin"]["silent"] > measured["negative_pin"]["mentions"] - jp["blocked_n"] - jp["review_mentions"] == 0
    # the library points were the exception until #352: their fallbacks now meet Jev's own harmful-error
    # count (no poor-fit variant, no wrong track) and the one other wrong variant pick Jev also makes
    assert measured["variant_choice"]["fallback"]["poor"] == jev["variant_choice"]["jev"]["bad"] == 0
    assert measured["track_baseline"]["fallback"]["wrong"] == jev["track_baseline"]["jev"]["bad"] == 0
    jv = jev["variant_choice"]["jev"]
    assert measured["variant_choice"]["fallback"]["wrong"] <= jv["picks"] - jv["right"] - jv["bad"] == 1
    assert measured["memory_gate"]["main"]["writes"] == 0 == jev["memory_gate"]["wrong_writes"]


def test_the_only_supported_pair_a_fallback_side_gate_blocks_is_reported(measured, jev):
    """The epic's rule: a fallback never blocks more than Jev would without saying so. #123 blocks one
    supported pair that Jev does not, and the document names it."""
    assert measured["support"]["g123"]["false_ids"] == ["bd_seaborn_python"]
    assert jev["support"]["false_blocks"] == "0/35"
    assert "bd_seaborn_python" in fb.DOC_PATH.read_text(encoding="utf-8")


# ── the proposed targets ─────────────────────────────────────────────────────

def test_half_gap_rounds_up():
    assert fb.half_gap(19, 51) == 35 and fb.half_gap(18, 47) == 33 and fb.half_gap(27, 35) == 31
    assert fb.half_gap(0, 1) == 1 and fb.half_gap(4, 4) == 4


def test_every_point_has_a_target_a_reason_and_a_verdict(measured, jev):
    t = fb.targets(measured, jev)
    assert set(t) == set(fb.POINTS) and len(fb.POINTS) == 6
    for name, row in t.items():
        assert row["hard"] and row["bar"] and row["reason"], name
        assert row["verdict"] in ("work: #351", "met: #351", "work: #352", "done: #352", "good enough", "leave"), name
    assert [n for n, r in t.items() if r["verdict"] == "good enough"] == ["coverage", "memory_gate"]
    assert [n for n, r in t.items() if r["verdict"].startswith("work")] == []
    assert t["support"]["verdict"] == "met: #351"
    assert [n for n, r in t.items() if r["verdict"] == "done: #352"] == ["variant_choice", "track_baseline"]


def test_the_bars_follow_the_half_gap_rule(measured, jev):
    t = fb.targets(measured, jev)
    assert t["support"]["bar"].startswith("35/54 should-block pairs blocked with #123 counted (contradicts 10/14, adds_unsupported 25/40)")
    assert t["variant_choice"]["bar"].startswith("33/48 correct") and "(15/48)" in t["variant_choice"]["bar"]
    assert t["track_baseline"]["bar"].startswith("31/36 correct") and "(14/36)" in t["track_baseline"]["bar"]
    assert "0/35" in t["support"]["hard"] and "0/48 poor-fit" in t["variant_choice"]["hard"]
    assert "0/36 wrong" in t["track_baseline"]["hard"]


def test_each_bar_sits_between_todays_figure_and_jevs(measured, jev):
    """Strictly above what ran before the fix (the bars were set from it at #350 and do not move when a
    fix lands), at or below Jev's own figure; the library points' fallbacks now meet them."""
    t = fb.targets(measured, jev)
    for point, today, ceiling in (
            ("variant_choice", fb.BEFORE_352["variant_choice"]["correct"], 47),
            ("track_baseline", fb.BEFORE_352["track_baseline"]["correct"], 35)):
        bar = int(t[point]["bar"].split("/")[0])
        assert today < bar <= ceiling, point
        assert measured[point]["fallback"]["correct"] >= bar, point
    bar = int(t["support"]["bar"].split("/")[0])
    assert measured["support"]["g123"]["blocked"] < bar <= jev["support"]["blocked_n"]


def test_the_support_bar_is_met(measured, jev):
    """#351's bar, from the document's own rule: 0 of the 35 supported pairs blocked by the new check (#123's
    own 1 is reported, not counted), at most 3 surfaced for review, and the half-gap figure on the main
    measure with #123 counted."""
    s, t = measured["support"], fb.targets(measured, jev)["support"]
    assert s["false_blocks"] == 0 and s["false_reviews"] <= 3 and s["false_ids"] == []
    assert s["with123"]["blocked"] >= 35 and s["with123"]["by_label"]["contradicts"][0] >= 10
    assert s["with123"]["by_label"]["adds_unsupported"][0] >= 25
    assert s["g123"]["false_ids"] == ["bd_seaborn_python"] and t["verdict"] == "met: #351"
    # short of Jev's blocks, and the new check adds to #123 rather than replacing it
    assert s["with123"]["blocked"] < jev["support"]["blocked_n"] and s["blocked"] < s["with123"]["blocked"]


# ── offline, no Jev ──────────────────────────────────────────────────────────

def test_measuring_never_reaches_jev_even_with_a_key_and_auto_mode(monkeypatch):
    """`measure` forces `ART_JEV_MODE=off` for its duration and puts the environment back."""
    monkeypatch.setenv("ART_JEV_MODE", "auto")
    monkeypatch.setenv("TYPESAFE_API_KEY", "not-a-real-key")
    monkeypatch.setattr(engine, "get_client", lambda: pytest.fail("the benchmark reached for a Jev client"))
    engine.reset_stats()
    fb.measure()
    for point, row in engine.stats().items():
        assert row["jev"] == 0 and row["cache"] == 0 and row["requests"] == 0, point
    assert engine.stats()["support"]["fallback"] == 89
    assert os.environ["ART_JEV_MODE"] == "auto" and os.environ["TYPESAFE_API_KEY"] == "not-a-real-key"


def test_every_measured_finding_is_the_fallbacks_not_a_jev_answer(measured):
    assert measured["support"]["reasons"] == ["mode=off"]
    assert measured["coverage"]["answered"] == 0


# ── the document ─────────────────────────────────────────────────────────────

def test_the_committed_document_is_current():
    """Each generated table in eval/FALLBACKS.md equals what the script renders from the committed labels
    and REPORT.md files now."""
    text = fb.DOC_PATH.read_text(encoding="utf-8")
    fresh, have = fb.render_blocks(), fb.committed_blocks(text)
    assert set(have) == set(fresh) == set(fb.BLOCKS)
    for name in fresh:
        assert have[name] == fresh[name], f"{name} is stale: run python eval/fallback_benchmark.py write"


def test_the_document_lists_all_six_points_with_a_fallback_a_jev_figure_and_a_target():
    text = fb.DOC_PATH.read_text(encoding="utf-8")
    summary = fb.committed_blocks(text)["summary"]
    rows = [r for r in summary.splitlines()[2:] if r.startswith("|")]
    assert [r.split("|")[1].split("(")[0].strip() for r in rows] == [
        "support", "negative pin", "coverage", "memory gate", "variant choice", "track baseline"]
    for r in rows:
        assert all(cell.strip() for cell in r.strip("|").split("|")), r
    targets = fb.committed_blocks(text)["targets"]
    assert targets.count("\n|") == 7                       # header, separator, six points
    assert "bd_seaborn_python" in text                     # the one supported pair a fallback-side gate blocks
    assert "Already good enough" in text and "Needs work" in text


def test_write_and_check_run_offline_from_the_command_line(tmp_path):
    doc = tmp_path / "FALLBACKS.md"
    doc.write_text(fb.DOC_PATH.read_text(encoding="utf-8").replace("| 0/35 | 1/35 | 0/35 |", "| 0/35 | 1/35 | 9/35 |"), encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k not in ("TYPESAFE_API_KEY", "DATABASE_URL")}
    env["PYTHONPATH"] = str(ROOT)
    env["ART_JEV_MODE"] = "auto"                           # the script must force it off itself

    def run(cmd):
        return subprocess.run([sys.executable, str(ROOT / "eval" / "fallback_benchmark.py"), cmd, "--doc", str(doc)],
                              capture_output=True, text=True, cwd=ROOT, env=env, timeout=240)
    stale = run("check")
    assert stale.returncode == 1 and "stale: support" in stale.stderr
    written = run("write")
    assert written.returncode == 0, written.stderr
    assert doc.read_text(encoding="utf-8") == fb.DOC_PATH.read_text(encoding="utf-8")
    assert run("check").returncode == 0
