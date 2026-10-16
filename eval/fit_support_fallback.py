"""Fit the deterministic support check's cutoffs on the labelled set (issue #351).

`harness/decisions/support_fallback.py` is what reads a cited bullet when Jev cannot (no key,
`ART_JEV_MODE=off`, an API error). It scores a bullet as a sum of signal weights and acts on two
cutoffs: block at `>= TAU_BLOCK`, review at `>= TAU_REVIEW`. This script fits them on the same 89
labelled pairs the Jev check was fitted on (`eval/support_labels/pairs.json`), by the same order of
cost (`docs/architecture.md` § 8.8):

    python eval/fit_support_fallback.py analyze    # offline; writes eval/support_labels/FALLBACK_REPORT.md

There is no recording to make: the check is code, so the "answers" are recomputed on every run
and nothing calls Jev. `analyze` does replay Jev's recorded answers (`recordings.json`, 100% cache hit
or it refuses) for one section only, "With a key": whether the fallback should also run beside
Jev as a floor.

The rule, in priority order:

1. **A false block of an honest edit is the worst error.** `tau_block` is the lowest grid value one
   step above the highest score of any pair labelled `supported`, and never below `BLOCK_FLOOR`
   (the weight of one strong signal): a lone soft signal may surface a bullet for review, it never
   refuses one. The floor is a rule, not a fit, as the 0.5 floor is for the write thresholds.
2. **Then recall**, which is what the grid shows; nothing is tuned on it.
3. **`tau_review`** is, among grid values below `tau_block` whose band holds at most
   `REVIEW_NOISE_LIMIT` of the supported pairs (the cap the Jev fits use), the one that surfaces the
   most should-block pairs for the fewest supported ones, then the lowest.

The score is a sum of authored weights (`STRONG` 1.0, `SOFT` 0.5), so its values sit on a 0.5 grid and
the cutoffs are fitted on that grid, never on a model's reported confidence.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from eval import fit_support_threshold as jev_fit  # noqa: E402  the shared pairs, helpers and Jev rows

LABEL_DIR = Path(__file__).resolve().parent / "support_labels"
PAIRS_PATH = jev_fit.PAIRS_PATH
RECORDINGS_PATH = jev_fit.RECORDINGS_PATH
REPORT_PATH = LABEL_DIR / "FALLBACK_REPORT.md"

STEP = 0.5
BLOCK_FLOOR = 1.0                                   # one strong signal's weight
GRID_BLOCK = [round(STEP * i, 2) for i in range(1, 9)]       # 0.5 .. 4.0
GRID_REVIEW = [round(STEP * i, 2) for i in range(1, 8)]      # 0.5 .. 3.5
REVIEW_NOISE_LIMIT = jev_fit.REVIEW_NOISE_LIMIT

_pct, _table = jev_fit._pct, jev_fit._table


# ── rows: the check run on every pair ────────────────────────────────────────

def load_pairs(path: Path = PAIRS_PATH) -> List[Dict[str, Any]]:
    return jev_fit.load_pairs(path)


def run_pairs(pairs: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Each pair with the fallback's score, label and signals, and whether the #123 numbers-and-names
    gate blocks it on the cited text (the gate that runs beside the fallback in production)."""
    from agents.checks import consistency_check
    from eval.fallback_benchmark import _support_page
    from harness.decisions import support_fallback as sf

    rows = []
    for pair in pairs:
        a = sf.assess(pair["bullet"], pair["evidence"], pair.get("original"))
        content, source, _base = _support_page(pair)
        rows.append({**pair, "score": a["score"], "fb_label": a["label"],
                     "signals": [s.text() for s in a["signals"]],
                     "kinds": sorted({s.kind for s in a["signals"]}),
                     "g123": bool(consistency_check(content, source))})
    return rows


# ── the analysis (pure: rows in, numbers out) ────────────────────────────────

def should_block(r) -> bool:
    return r["label"] != "supported"


def blocked(r, tau_block: float) -> bool:
    return r["score"] >= tau_block


def in_band(r, tau_review: float, tau_block: float) -> bool:
    return tau_review <= r["score"] < tau_block


def block_stats(rows, tau: float) -> Dict[str, Any]:
    sb = [r for r in rows if should_block(r)]
    sp = [r for r in rows if not should_block(r)]
    tp = [r for r in sb if blocked(r, tau)]
    fp = [r for r in sp if blocked(r, tau)]
    by = {l: ([r for r in sb if r["label"] == l], [r for r in sb if r["label"] == l and blocked(r, tau)])
          for l in ("contradicts", "adds_unsupported")}
    both = [r for r in sb if blocked(r, tau) or r["g123"]]
    return {"tau": tau, "n_should": len(sb), "n_supported": len(sp), "tp": len(tp), "fp": len(fp),
            "fp_ids": [r["id"] for r in fp], "with_123": len(both),
            "precision": len(tp) / (len(tp) + len(fp)) if (tp or fp) else None,
            "recall": len(tp) / len(sb) if sb else None,
            "by_label": {l: (len(hit), len(all_)) for l, (all_, hit) in by.items()}}


def band_stats(rows, tau_review: float, tau_block: float) -> Dict[str, Any]:
    band = [r for r in rows if in_band(r, tau_review, tau_block)]
    below = [r for r in rows if should_block(r) and r["score"] < tau_review]
    return {"tau_review": tau_review, "tau_block": tau_block, "n": len(band),
            "should": sum(should_block(r) for r in band), "supported": sum(not should_block(r) for r in band),
            "missed": len(below), "missed_ids": [r["id"] for r in below]}


def recommend(rows) -> Dict[str, Any]:
    supported = [r for r in rows if not should_block(r)]
    top = max(supported, key=lambda r: r["score"]) if supported else None
    floor = max(BLOCK_FLOOR, (top["score"] if top else 0.0) + STEP)
    tau_block = next((t for t in GRID_BLOCK if t >= floor - 1e-9), GRID_BLOCK[-1])
    limit = math.floor(REVIEW_NOISE_LIMIT * len(supported))
    ok = [t for t in GRID_REVIEW if t < tau_block and band_stats(rows, t, tau_block)["supported"] <= limit]
    tau_review = (min(ok, key=lambda t: (-band_stats(rows, t, tau_block)["should"],
                                         band_stats(rows, t, tau_block)["supported"], t))
                  if ok else tau_block)      # no band fits the cap: no review tier (an empty band)
    return {"tau_block": tau_block, "tau_review": tau_review, "top_supported": top, "noise_limit": limit}


def outcome(rows, tau_block: float, tau_review: float) -> Dict[str, int]:
    o = {"block": 0, "review": 0, "pass": 0}
    for r in rows:
        o["block" if blocked(r, tau_block) else "review" if in_band(r, tau_review, tau_block) else "pass"] += 1
    return o


def groups(rows) -> List[tuple]:
    cats = sorted({r["category"] for r in rows})
    return [(c, [r for r in rows if r["category"] == c]) for c in cats]


def with_key(fb_rows, jev_rows, fb_cut, jev_cut) -> Dict[str, Any]:
    """Jev alone against Jev with the fallback running beside it as a floor (a block from either
    blocks, a review from either reviews), on the same pairs. `fb_rows` carry the fallback's score,
    `jev_rows` Jev's `p_worst`; both are joined by id."""
    jev = {r["id"]: r for r in jev_rows}
    tb, tr = fb_cut
    jb, jr = jev_cut
    out = {"alone": {"block": 0, "review": 0, "silent": 0, "fp": 0, "fr": 0},
           "floor": {"block": 0, "review": 0, "silent": 0, "fp": 0, "fr": 0},
           "added_blocks": [], "added_reviews": [], "false_added": [], "false_added_reviews": [],
           "added_in_band": 0}
    for r in fb_rows:
        j = jev[r["id"]]["p_worst"]
        jev_block, jev_rev = j >= jb, jr <= j < jb
        fb_block, fb_rev = r["score"] >= tb, tr <= r["score"] < tb
        sb = should_block(r)
        for name, blk, rev in (("alone", jev_block, jev_rev),
                               ("floor", jev_block or fb_block, (jev_rev or fb_rev) and not (jev_block or fb_block))):
            o = out[name]
            if sb:
                o["block"] += blk
                o["review"] += rev
                o["silent"] += not (blk or rev)
            else:
                o["fp"] += blk
                o["fr"] += rev
        if fb_block and not jev_block:
            (out["added_blocks"] if sb else out["false_added"]).append(r["id"])
            out["added_in_band"] += bool(sb and jev_rev)
        if fb_rev and not (jev_block or jev_rev) and not fb_block:
            (out["added_reviews"] if sb else out["false_added_reviews"]).append(r["id"])
    return out


# ── rendering ────────────────────────────────────────────────────────────────

def render_report(rows, jev_rows: Optional[List[Dict[str, Any]]] = None) -> str:
    from harness.decisions import support, support_fallback as sf

    rec = recommend(rows)
    tb, tr = rec["tau_block"], rec["tau_review"]
    n = len(rows)
    sup = [r for r in rows if not should_block(r)]
    st, bs = block_stats(rows, tb), band_stats(rows, tr, tb)
    top = rec["top_supported"]
    oc = outcome([r for r in rows if should_block(r)], tb, tr)
    oc_sup = outcome(sup, tb, tr)

    o: List[str] = []
    o.append("# Support check without Jev: cutoff fit (issue #351)\n")
    o.append("Generated by `python eval/fit_support_fallback.py analyze` from `pairs.json` (and, for one section, "
             "`recordings.json`). Nothing here calls Jev. The check is `harness/decisions/support_fallback.py`; its "
             "model name in `harness/policy.json` is `" + sf.MODEL + "`.\n")
    o.append(f"- {n} pairs ({st['n_should']} should block: contradicts "
             f"{sum(r['label'] == 'contradicts' for r in rows)}, adds_unsupported "
             f"{sum(r['label'] == 'adds_unsupported' for r in rows)}; {st['n_supported']} supported).")
    o.append(f"- Cutoffs now in `support_fallback.py`: `TAU_BLOCK` {sf.TAU_BLOCK}, `TAU_REVIEW` {sf.TAU_REVIEW} "
             f"({'they match the fit below' if (sf.TAU_BLOCK, sf.TAU_REVIEW) == (tb, tr) else 'they differ from the fit below'}).")
    o.append("- A pair's score is the sum of the weights of the distinct signals that fired (strong 1.0, soft 0.5). "
             "Block when it is `>= tau_block`; review when `tau_review <= score < tau_block`.\n")

    o.append("## Recommendation\n")
    o.append(f"**tau_block = {tb:.1f}, tau_review = {tr:.1f}.**\n")
    para = (f"A false block refuses an honest edit, so `tau_block` is set first from the supported pairs: the highest "
            f"score on any pair labelled `supported` is {top['score']:.1f}"
            + (f" (`{top['id']}`)" if top["score"] else " (no supported pair fires any signal)")
            + f". The rule puts `tau_block` one grid step ({STEP}) above that and never below the weight of one strong "
              f"signal ({BLOCK_FLOOR}), so it is {tb:.1f}: it blocks {st['fp']} of {st['n_supported']} supported pairs. "
              f"At that value the check blocks {st['tp']} of {st['n_should']} should-block pairs ({_pct(st['recall'])}, "
              f"precision {_pct(st['precision'])}): contradicts {st['by_label']['contradicts'][0]}/"
              f"{st['by_label']['contradicts'][1]}, adds_unsupported {st['by_label']['adds_unsupported'][0]}/"
              f"{st['by_label']['adds_unsupported'][1]}; counting the #123 numbers-and-names gate beside it, "
              f"{st['with_123']}/{st['n_should']}. `tau_review` = {tr:.1f} is the value that surfaces the most "
              f"should-block pairs for the fewest supported ones within a cap of {rec['noise_limit']} supported pair(s) "
              f"({_pct(REVIEW_NOISE_LIMIT)} of {st['n_supported']}): it surfaces {bs['should']} more should-block pairs "
              f"at the cost of {bs['supported']} supported one(s), leaving {bs['missed']} should-block pairs to pass "
              f"silently.")
    o.append(para + "\n")
    o.append("**The labelled supported pairs do not stress the lists.** No supported pair fires a signal at all, "
             "so the data cannot place `tau_block` between 0.5 and 1.0; the floor does, by rule. The lists were "
             "written from the failure classes #350 found and then run on this set, so the figures below are the "
             "set the lists were authored on, not a held-out accuracy. `tests/test_support_fallback.py` adds honest "
             "and dishonest bullets written after the lists, as a regression floor.\n")

    o.append("### Outcome at the recommended cutoffs\n")
    o.append(_table(["", "pairs", "block", "review", "pass"], [
        ["should block", st["n_should"], oc["block"], oc["review"], oc["pass"]],
        ["supported", st["n_supported"], oc_sup["block"], oc_sup["review"], oc_sup["pass"]]]))
    o.append("")
    o.append("The harmful error is a should-block pair kept silently (pass) and, worse, a supported pair "
             "blocked. By category (block / review / pass):\n")
    body = []
    for c, rs in groups(rows):
        cs = outcome(rs, tb, tr)
        body.append([f"`{c}`", len(rs), sum(should_block(r) for r in rs),
                     f"{cs['block']} / {cs['review']} / {cs['pass']}",
                     sum(r["g123"] for r in rs if should_block(r))])
    o.append(_table(["category", "pairs", "should block", "block / review / pass", "#123 blocks"], body))
    o.append("")

    # grid
    o.append("## tau_block grid\n")
    body = []
    for t in GRID_BLOCK:
        s = block_stats(rows, t)
        body.append([f"{t:.1f}", f"{s['tp']}/{s['n_should']}", _pct(s["recall"]),
                     f"{s['by_label']['contradicts'][0]}/{s['by_label']['contradicts'][1]}",
                     f"{s['by_label']['adds_unsupported'][0]}/{s['by_label']['adds_unsupported'][1]}",
                     f"{s['fp']}/{s['n_supported']}", f"{s['with_123']}/{s['n_should']}"])
    o.append(_table(["tau_block", "blocked", "recall", "contradicts", "adds_unsupported", "false blocks",
                     "blocked with #123"], body))
    o.append("")
    o.append("### Review band\n")
    o.append("Cell: pairs with `tau_review <= score < tau_block`, as `total (should-block / supported)`.\n")
    grid = [t for t in GRID_BLOCK if t <= 2.0]
    body = []
    for r_ in GRID_REVIEW:
        row = [f"{r_:.1f}"]
        for t in grid:
            if r_ >= t:
                row.append("-")
            else:
                b = band_stats(rows, r_, t)
                row.append(f"{b['n']} ({b['should']}/{b['supported']})")
        body.append(row)
    o.append(_table(["tau_review \\ tau_block"] + [f"{t:.1f}" for t in grid], body))
    o.append("")
    o.append(f"Should-block pairs that pass silently at tau_review {tr:.1f}: "
             + (", ".join(f"`{i}`" for i in bs["missed_ids"]) or "none") + "\n")

    # signals
    o.append("## Which signals fire\n")
    kinds = sorted({k for r in rows for k in r["kinds"]})
    body = []
    for k in kinds:
        sbk = [r for r in rows if should_block(r) and k in r["kinds"]]
        spk = [r for r in rows if not should_block(r) and k in r["kinds"]]
        body.append([k, len(sbk), len(spk)])
    o.append(_table(["signal kind", "should-block pairs with it", "supported pairs with it"], body))
    o.append("")

    if jev_rows is not None:
        j = jev_rows
        res = with_key(rows, j, (tb, tr), (support.TAU_BLOCK, support.TAU_REVIEW))
        a, f = res["alone"], res["floor"]
        o.append("## With a key: should the fallback also run beside Jev?\n")
        o.append("The term match runs beside Jev's negative-pin check as a floor because it is exact. This check is a "
                 "word list. Replaying Jev's recorded answers (`recordings.json`, "
                 f"`TAU_BLOCK` {support.TAU_BLOCK}, `TAU_REVIEW` {support.TAU_REVIEW}) against this check on the same "
                 f"{n} pairs, with the fallback as a floor (a block from either blocks):\n")
        o.append(_table(["", "Jev alone", "Jev with the fallback as a floor"], [
            ["should-block pairs blocked", f"{a['block']}/{st['n_should']}", f"{f['block']}/{st['n_should']}"],
            ["should-block pairs surfaced for review", a["review"], f["review"]],
            ["should-block pairs kept silently (the harmful error)", a["silent"], f["silent"]],
            ["supported pairs blocked (false blocks)", f"{a['fp']}/{st['n_supported']}", f"{f['fp']}/{st['n_supported']}"],
            ["supported pairs surfaced for review", a["fr"], f["fr"]]]))
        o.append("")
        o.append("Pairs the fallback blocks that Jev does not: "
                 + (", ".join(f"`{i}`" for i in res["added_blocks"] + res["false_added"]) or "none")
                 + f" (of which supported: {len(res['false_added'])}). Pairs it would only surface for review that Jev "
                   "passes: " + (", ".join(f"`{i}`" for i in res["added_reviews"] + res["false_added_reviews"]) or "none")
                 + f" (supported: {len(res['false_added_reviews'])}).\n")
        o.append("**Decision.** " + _floor_decision(res, st) + "\n")

    o.append("## Every pair\n")
    o.append(_table(["id", "category", "label", "score", "fallback label", "#123", "signals"], [
        [f"`{r['id']}`", r["category"], r["label"], f"{r['score']:.1f}", r["fb_label"] if r["score"] else "-",
         "blocks" if r["g123"] else "", "; ".join(r["signals"])] for r in rows]))
    return "\n".join(o)


def floor_adopted(res) -> bool:
    """The rule, fixed before the numbers: the fallback runs beside Jev only if that lowers the
    should-block pairs kept silently (the harmful error) with no more false blocks. A block it adds
    where Jev already shows the user the pair for review changes nothing the user is not shown, and
    costs a false-block risk Jev's own fit does not carry."""
    a, f = res["alone"], res["floor"]
    return f["silent"] < a["silent"] and f["fp"] <= a["fp"]


def _floor_decision(res, st) -> str:
    a, f = res["alone"], res["floor"]
    ids = ", ".join("`" + i + "`" for i in res["added_blocks"]) or "none"
    numbers = (f"As a floor it blocks {f['block'] - a['block']} more should-block pair(s) ({ids}), "
               f"{res['added_in_band']} of them already in Jev's review band, and blocks {len(res['false_added'])} "
               f"more supported pair(s); should-block pairs kept silently go from {a['silent']} to {f['silent']}.")
    if floor_adopted(res):
        return ("Yes. " + numbers + " It lowers the harmful error with no more false blocks, so it runs beside Jev "
                "the way the term match does.")
    return ("No. " + numbers + " It lowers no harmful error, and every block it adds is one Jev already shows the "
            "user as a review. Any false block the word lists make on text outside these 89 synthetic pairs would be "
            "one Jev did not make (Jev's fit leaves 0.08 of headroom on measured answers; the lists have none "
            "measured). So Jev decides alone when it answers, and the fallback reads only the bullets Jev did not.")


# ── commands ─────────────────────────────────────────────────────────────────

def _jev_rows(pairs):
    """Jev's recorded answers to the same pairs, replayed offline into a private store."""
    tmp = Path(tempfile.mkdtemp(prefix="art_support_fallback_analyze_"))
    jev_fit._pin_store(tmp)
    os.environ["ART_JEV_MODE"] = "replay"
    os.environ.pop("TYPESAFE_API_KEY", None)
    from harness.decisions import engine, recordings
    recordings.import_recordings(json.loads(RECORDINGS_PATH.read_text(encoding="utf-8")))
    engine.reset_stats()
    rows = jev_fit.run_pairs(pairs)
    st = engine.stats()["support"]
    if st["hit_rate"] != 1.0 or st["jev"] or st["fallback"]:
        raise SystemExit(f"replay was not a 100% cache hit: {st}")
    return rows


def cmd_analyze(args) -> int:
    pairs = load_pairs(Path(args.pairs))
    jev = _jev_rows(pairs)
    os.environ["ART_JEV_MODE"] = "off"
    rows = run_pairs(pairs)
    Path(args.report).write_text(render_report(rows, jev) + "\n", encoding="utf-8")
    rec = recommend(rows)
    print(f"{len(rows)} pairs; recommended tau_block {rec['tau_block']:.1f}, tau_review {rec['tau_review']:.1f}; "
          f"wrote {args.report}")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("analyze")
    s.add_argument("--pairs", default=str(PAIRS_PATH))
    s.add_argument("--report", default=str(REPORT_PATH))
    s.set_defaults(fn=cmd_analyze)
    args = p.parse_args(list(sys.argv[1:] if argv is None else argv))
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
