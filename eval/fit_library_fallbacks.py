"""Fit the Jev-free library fallbacks on the labelled sets (issue #352).

`harness/library.py` has two judgment calls that run without Jev (`docs/harness.md` § 4, `eval/FALLBACKS.md`):

- **Variant choice.** `best_variant` scores each approved variant by `variant_fit` (what share of the job's top
  requirements and title asks for the kinds of work the bullet shows, `harness/concepts.py`) and picks the best
  one when its score is at least `VARIANT_MATCH_FLOOR`, else `no_match`.
- **Track baseline.** `pick_track` scores each saved track's role family against the same reading of the job and
  picks the best when its score is at least `TRACK_FALLBACK_FLOOR` and it leads every other family by
  `TRACK_FALLBACK_MARGIN`, else none.

The vocabulary (`CONCEPTS`, `FAMILIES`) is written by hand; this script fits the three numbers that use it, on
`eval/library_labels/variants.json` (48 cases) and `baselines.json` (36 cases), with the rule `docs/architecture.md`
§ 8.8 prescribes and `eval/fit_library_thresholds.py` follows: **harmful errors first** (a poor-fit variant, a wrong
track), then the most correct, then the grid value closest to the middle of its gap, ties to the higher. The track
pair is fitted one number at a time, the floor with no margin and then the margin at the floor. Nothing here reads
a model's confidence and nothing touches the network or a recording.

    python eval/fit_library_fallbacks.py analyze    # print the fit, leave-one-out and the holdout
    python eval/fit_library_fallbacks.py write      # also rewrite eval/library_labels/FALLBACK_FIT.md
    python eval/fit_library_fallbacks.py check      # exit 1 when FALLBACK_FIT.md is stale

Two figures are read beside the fitted one, because the vocabulary was written with the labelled sets in view and a
fit on 48 and 36 cases can overstate itself:

- **Leave-one-out**: each case is scored by thresholds fitted on the others. It tests the three numbers, not the
  vocabulary.
- **Holdout** (`eval/library_labels/holdout/`): 22 variant cases and 17 track cases written by the author before the
  vocabulary and never used to fit a number. They are the author's labels, unreviewed, and the author also wrote the
  vocabulary, so they are a cross-check on the numbers and on the vocabulary's reach, not an independent test.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from eval import fit_library_thresholds as lib  # noqa: E402  the label loaders and outcomes
from eval.fit_support_threshold import _table  # noqa: E402

HOLDOUT_DIR = lib.LABEL_DIR / "holdout"
REPORT_PATH = lib.LABEL_DIR / "FALLBACK_FIT.md"
SCRIPT = "eval/fit_library_fallbacks.py"
VARIANT_GRID = [round(0.01 * i, 2) for i in range(0, 41)]      # 0.00 .. 0.40
FLOOR_GRID = [round(0.01 * i, 2) for i in range(0, 96)]        # 0.00 .. 0.95
MARGIN_GRID = [round(0.01 * i, 2) for i in range(0, 41)]       # 0.00 .. 0.40

# What ran before #352, from eval/FALLBACKS.md at #350 (the labelled sets are unchanged).
BEFORE = {"variant": {"correct": 18, "picks": 18, "right": 7, "poor": 7, "wrong": 4, "lost": 19},
          "track": {"correct": 27, "picks": 23, "right": 15, "wrong": 8, "lost": 1}}


class _JevOff:
    """Force `ART_JEV_MODE=off` for a block: nothing here may reach a model."""

    def __enter__(self):
        self._old = os.environ.get("ART_JEV_MODE")
        os.environ["ART_JEV_MODE"] = "off"

    def __exit__(self, *exc):
        if self._old is None:
            os.environ.pop("ART_JEV_MODE", None)
        else:
            os.environ["ART_JEV_MODE"] = self._old


# ── raw scores per case ──────────────────────────────────────────────────────

def variant_rows(doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    """One row per variant case: the best variant by `variant_fit` (`pick0`, its label key) and its score."""
    from harness import library

    rows = []
    for case in doc["cases"]:
        title, reqs, _, variants = lib.variant_inputs(doc, case)
        terms = library.job_terms(None, "\n".join([title, *[r["text"] for r in reqs]]))
        scored = library.variant_scores(variants, terms, library.job_reading(title, reqs))
        score, top = scored[0]
        rows.append({**case, "pick0": top["key"], "score": score,
                     "scores": {v["key"]: s for s, v in scored}})
    return rows


def track_rows_of(doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    """One row per track case: the best-fitting saved track (`pick0`, None when the job has no
    requirements), its score and its lead over every other family."""
    from harness import library

    tracks = lib.track_rows(doc)
    rows = []
    for case in doc["cases"]:
        found = library.track_scores(case["title"], case["requirements"], tracks)
        rows.append({**case, "pick0": found["track"] if found else None,
                     "score": found["score"] if found else 0.0, "margin": found["margin"] if found else 0.0,
                     "rival": found["rival"] if found else 0.0})
    return rows


# ── the fit ──────────────────────────────────────────────────────────────────

def _pick(row, feature: str, t: float, gate: Callable[[Dict], bool]) -> Optional[str]:
    return row["pick0"] if row["pick0"] and gate(row) and row[feature] >= t - 1e-9 else None


def fit_threshold(rows: Sequence[Dict], feature: str, outcome, bad: str, grid: Sequence[float],
                  gate: Callable[[Dict], bool] = lambda r: True) -> Dict[str, Any]:
    """The rule of `eval/fit_library_thresholds._fit` on one number: over `grid`, no pick whose outcome is
    `bad` first, then the most correct, then the grid value closest to the middle of the gap between the worst
    `bad` score below the lowest candidate and the lowest score of a right pick above it, ties to the higher.
    `gate` is the other threshold, held fixed."""
    def stats(t):
        outs = [outcome(r, _pick(r, feature, t, gate)) for r in rows]
        return {"bad": sum(o == bad for o in outs), "correct": sum(o == "correct" for o in outs),
                "wrong": sum(o == "wrong" for o in outs), "lost": sum(o == "lost" for o in outs),
                "picks": sum(_pick(r, feature, t, gate) is not None for r in rows)}
    table = {t: stats(t) for t in grid}
    best = min((s["bad"], -s["correct"]) for s in table.values())
    cands = [t for t, s in table.items() if (s["bad"], -s["correct"]) == best]
    lo = min(cands)
    danger = [r[feature] for r in rows
              if r["pick0"] and gate(r) and outcome(r, r["pick0"]) == bad]
    below = [d for d in danger if d < lo - 1e-9]
    kept = [r[feature] for r in rows if r["pick0"] and gate(r) and r["pick0"] in r["best"]
            and r[feature] >= lo - 1e-9]
    floor_p = max(max(below, default=grid[0]), grid[0])      # a gap cannot reach below the grid itself
    mid = (floor_p + (min(kept) if kept else floor_p)) / 2
    tau = min(cands, key=lambda t: (round(abs(t - mid), 6), -t))
    return {"tau": tau, "table": table, "candidates": cands, "danger_max": max(danger, default=None),
            "kept_min": min(kept, default=None), "stats": table[tau]}


def fit_variant(rows) -> Dict[str, Any]:
    return fit_threshold(rows, "score", lib.variant_outcome, "poor", VARIANT_GRID)


def fit_track(rows) -> Dict[str, Any]:
    """Floor first (no margin), then the margin at that floor."""
    floor = fit_threshold(rows, "score", lib.baseline_outcome, "wrong", FLOOR_GRID,
                          gate=lambda r: r["margin"] >= 0.0)         # the track at least leads, as at margin 0
    margin = fit_threshold(rows, "margin", lib.baseline_outcome, "wrong", MARGIN_GRID,
                           gate=lambda r, f=floor["tau"]: r["score"] >= f - 1e-9)
    return {"floor": floor, "margin": margin}


def recommend(vrows, brows) -> Dict[str, float]:
    """The numbers `harness/policy.json` must hold, by policy key."""
    t = fit_track(brows)
    return {"library.variant_match_floor": fit_variant(vrows)["tau"],
            "library.fallback_track_floor": t["floor"]["tau"],
            "library.fallback_track_margin": t["margin"]["tau"]}


# ── tallies, leave-one-out, holdout ──────────────────────────────────────────

def _tally(rows, outcome, picker) -> Dict[str, int]:
    outs = [outcome(r, picker(r)) for r in rows]
    picks = [(r, picker(r)) for r in rows if picker(r)]
    return {"n": len(rows), "correct": sum(o == "correct" for o in outs), "poor": sum(o == "poor" for o in outs),
            "wrong": sum(o == "wrong" for o in outs), "lost": sum(o == "lost" for o in outs),
            "picks": len(picks), "right": sum(p in r["best"] for r, p in picks)}


def variant_tally(rows, floor: float) -> Dict[str, int]:
    return _tally(rows, lib.variant_outcome, lambda r: _pick(r, "score", floor, lambda _: True))


def track_tally(rows, floor: float, margin: float) -> Dict[str, int]:
    def pick(r):
        return (r["pick0"] if r["pick0"] and r["score"] >= floor - 1e-9
                and r["margin"] >= max(margin, 0.0) - 1e-9 else None)
    return _tally(rows, lib.baseline_outcome, pick)


def leave_one_out(vrows, brows) -> Dict[str, Dict[str, int]]:
    """Each case scored by thresholds fitted on all the others."""
    v = {"n": len(vrows), "correct": 0, "poor": 0, "wrong": 0, "lost": 0, "picks": 0, "right": 0}
    b = {"n": len(brows), "correct": 0, "poor": 0, "wrong": 0, "lost": 0, "picks": 0, "right": 0}
    for i, row in enumerate(vrows):
        t = fit_variant(vrows[:i] + vrows[i + 1:])["tau"]
        for k, x in variant_tally([row], t).items():
            if k != "n":
                v[k] += x
    for i, row in enumerate(brows):
        fit = fit_track(brows[:i] + brows[i + 1:])
        for k, x in track_tally([row], fit["floor"]["tau"], fit["margin"]["tau"]).items():
            if k != "n":
                b[k] += x
    return {"variant": v, "track": b}


def constant_tally(rows, outcome) -> Dict[str, int]:
    return _tally(rows, outcome, lambda r: None)


def measure() -> Dict[str, Any]:
    """Everything the report and the tests read: the fit, the shipped figures, leave-one-out, the holdout."""
    with _JevOff():
        vdoc, bdoc = lib.load_variants(), lib.load_baselines()
        hv, hb = lib.load_variants(HOLDOUT_DIR / "variants.json"), lib.load_baselines(HOLDOUT_DIR / "baselines.json")
        vrows, brows = variant_rows(vdoc), track_rows_of(bdoc)
        hvrows, hbrows = variant_rows(hv), track_rows_of(hb)
    vfit, tfit = fit_variant(vrows), fit_track(brows)
    floor, tfloor, tmargin = vfit["tau"], tfit["floor"]["tau"], tfit["margin"]["tau"]
    return {
        "fit": {"variant_floor": floor, "track_floor": tfloor, "track_margin": tmargin},
        "variant": {"fitted": variant_tally(vrows, floor), "holdout": variant_tally(hvrows, floor),
                    "constant": constant_tally(vrows, lib.variant_outcome),
                    "holdout_constant": constant_tally(hvrows, lib.variant_outcome),
                    "fit": vfit, "poor_ids": [r["id"] for r in vrows
                                              if lib.variant_outcome(r, _pick(r, "score", floor, lambda _: True)) == "poor"]},
        "track": {"fitted": track_tally(brows, tfloor, tmargin), "holdout": track_tally(hbrows, tfloor, tmargin),
                  "constant": constant_tally(brows, lib.baseline_outcome),
                  "holdout_constant": constant_tally(hbrows, lib.baseline_outcome), "fit": tfit},
        "loo": leave_one_out(vrows, brows),
        "rows": {"variant": vrows, "track": brows, "holdout_variant": hvrows, "holdout_track": hbrows},
    }


# ── the report ───────────────────────────────────────────────────────────────

def _f(t: Dict[str, int], bad: str) -> str:
    return f"{t['correct']}/{t['n']}, {t['picks']} picks ({t['right']} right, {t[bad]} {'poor-fit' if bad == 'poor' else 'wrong'}"\
           + (f", {t['wrong']} other wrong" if bad == "poor" else "") + f"), {t['lost']} lost"


def render(m: Dict[str, Any]) -> str:
    from harness import concepts, library

    v, t, loo = m["variant"], m["track"], m["loo"]
    fit = m["fit"]
    n_patterns = sum(len(p) for p in concepts.CONCEPTS.values())
    o = ["# Jev-free variant and track fallbacks: the fit (issue #352)\n",
         "Generated by `python eval/fit_library_fallbacks.py write` from the committed labels; "
         "`tests/test_library_fallbacks.py` fails when this file is stale. See the script's docstring for the rule.\n",
         "## What was fitted\n",
         f"The vocabulary (`harness/concepts.py`: {len(concepts.CONCEPTS)} kinds of work, {n_patterns} word patterns, "
         f"{len(concepts.FAMILIES)} role families and {len(concepts.OFF_TRACK)} kinds of work no track covers) is written by hand "
         "and was written with `variants.json` and `baselines.json` in view. Three numbers use it and are fitted: "
         "harmful errors first (a poor-fit variant, a wrong track), then the most correct, then the middle of the gap, ties to the higher.\n",
         _table(["number", "value", "model", "what it gates"], [
             ["`library.variant_match_floor`", f"{fit['variant_floor']:.2f}", "`fallback-variant-v1`",
              "a variant is picked when its `variant_fit` is at least this, else `no_match`"],
             ["`library.fallback_track_floor`", f"{fit['track_floor']:.2f}", "`fallback-track-v1`",
              "a track is picked when its role family's cosine with the job's reading is at least this"],
             ["`library.fallback_track_margin`", f"{fit['track_margin']:.2f}", "`fallback-track-v1`",
              "...and it leads every other family (saved or not) by at least this"]]),
         "",
         "## Variant choice (48 cases)\n"]
    vf, vh, vc = v["fitted"], v["holdout"], v["constant"]
    o.append(_table(["", "correct", "picks", "right", "poor-fit", "other wrong", "lost"], [
        ["before (#229 word overlap, floor 0.10)", f"{BEFORE['variant']['correct']}/48", BEFORE["variant"]["picks"],
         BEFORE["variant"]["right"], BEFORE["variant"]["poor"], BEFORE["variant"]["wrong"], BEFORE["variant"]["lost"]],
        ["**fitted** (floor " + f"{fit['variant_floor']:.2f})", f"{vf['correct']}/48", vf["picks"], vf["right"], vf["poor"], vf["wrong"], vf["lost"]],
        ["leave-one-out", f"{loo['variant']['correct']}/48", loo["variant"]["picks"], loo["variant"]["right"],
         loo["variant"]["poor"], loo["variant"]["wrong"], loo["variant"]["lost"]],
        ["constant: always no_match", f"{vc['correct']}/48", 0, 0, 0, 0, vc["lost"]]]))
    o.append("")
    o.append(f"Holdout (22 author-written cases, same floor): {_f(vh, 'poor')}; the constant 'always no_match' scores "
             f"{m['variant']['holdout_constant']['correct']}/{vh['n']}.\n")
    fv = v["fit"]
    o.append(f"The floor sits in a gap: the highest score of a poor-fit or wrong pick below it is "
             f"{fv['danger_max'] if fv['danger_max'] is not None else 'n/a'}, and the lowest right pick it keeps scores "
             f"{fv['kept_min']}. Correct, poor-fit and wrong picks by floor:\n")
    grid = [0.04, 0.06, 0.08, 0.09, 0.10, 0.12, 0.15, 0.20, 0.30]
    o.append(_table(["floor", "correct", "picks", "poor-fit", "other wrong", "lost"],
                    [[f"{g:.2f}", f"{fv['table'][g]['correct']}/48", fv["table"][g]["picks"], fv["table"][g]["bad"],
                      fv["table"][g]["wrong"], fv["table"][g]["lost"]] for g in grid if g in fv["table"]]))
    o.append("")
    o.append("## Track baseline (36 cases)\n")
    tf, th, tc = t["fitted"], t["holdout"], t["constant"]
    o.append(_table(["", "correct", "picks", "right", "wrong", "lost"], [
        ["before (#229 title lookup)", f"{BEFORE['track']['correct']}/36", BEFORE["track"]["picks"], BEFORE["track"]["right"],
         BEFORE["track"]["wrong"], BEFORE["track"]["lost"]],
        ["**fitted** (floor " + f"{fit['track_floor']:.2f}, margin {fit['track_margin']:.2f})", f"{tf['correct']}/36", tf["picks"],
         tf["right"], tf["wrong"], tf["lost"]],
        ["leave-one-out", f"{loo['track']['correct']}/36", loo["track"]["picks"], loo["track"]["right"],
         loo["track"]["wrong"], loo["track"]["lost"]],
        ["constant: always none", f"{tc['correct']}/36", 0, 0, 0, tc["lost"]]]))
    o.append("")
    o.append(f"Holdout (17 author-written cases, same floor and margin): {_f(th, 'wrong')}; the constant 'always none' scores "
             f"{m['track']['holdout_constant']['correct']}/{th['n']}.\n")
    tfit = t["fit"]
    o.append(f"Every job no saved track fits has another family ahead of the best saved track (a negative lead), so once a track "
             f"must at least lead no wrong track appears at any floor or margin on the grid, and neither number is bound by a "
             f"wrong pick. The floor is the middle of the gap between 0 and the lowest right pick's score "
             f"({tfit['floor']['kept_min']}), and the margin the middle of the gap between 0 and the lowest right "
             f"pick's lead ({tfit['margin']['kept_min']}). Correct and lost by floor (the track leading) and by margin (at the fitted floor):\n")
    o.append(_table(["floor", "correct", "wrong", "lost"],
                    [[f"{g:.2f}", f"{tfit['floor']['table'][g]['correct']}/36", tfit["floor"]["table"][g]["bad"],
                      tfit["floor"]["table"][g]["lost"]] for g in (0.0, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8)]))
    o.append("")
    o.append(_table(["margin", "correct", "wrong", "lost"],
                    [[f"{g:.2f}", f"{tfit['margin']['table'][g]['correct']}/36", tfit["margin"]["table"][g]["bad"],
                      tfit["margin"]["table"][g]["lost"]] for g in (0.0, 0.02, 0.04, 0.05, 0.1, 0.15, 0.2)]))
    o.append("")
    o.append("## How far to trust these figures\n")
    o.append("- The vocabulary was written with both labelled sets in view, so the fitted rows are a fit and the "
             "leave-one-out row tests only the three numbers.\n"
             "- The holdout was written before the vocabulary, but by the same author, who then added kinds of work "
             "(`dba`, `sales`) after writing it. It shows the vocabulary's reach into fresh wording; it is not an "
             "independent test. Its labels are unreviewed.\n"
             "- A bullet that repeats the posting's nouns while describing teaching or office work scores 0 "
             "(`concepts.mostly_unasked`); a keyword-stuffed bullet the vocabulary cannot place is the case this "
             "fallback cannot see.\n"
             "- The pick reads requirements. A job with none gets `no_match` for variants, and the track lookup of "
             "#229 (the title's role family) for tracks.\n")
    return "\n".join(o) + "\n"


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("command", choices=["analyze", "write", "check"])
    args = ap.parse_args(argv)
    m = measure()
    text = render(m)
    if args.command == "check":
        current = REPORT_PATH.read_text(encoding="utf-8") if REPORT_PATH.is_file() else ""
        if current.replace("\r\n", "\n") != text:
            print(f"{REPORT_PATH} is stale: run `python eval/fit_library_fallbacks.py write`")
            return 1
        print("FALLBACK_FIT.md is current")
        return 0
    if args.command == "write":
        REPORT_PATH.write_text(text, encoding="utf-8", newline="\n")
    print(text)
    print(json.dumps(m["fit"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
