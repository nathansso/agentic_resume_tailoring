"""Fit the support check's thresholds on a labelled set (issue #237).

`harness/decisions/support.py` asks Jev, per revised bullet, how the cited
evidence relates to the new text (`support@v1`) and turns the answer into three
tiers with two thresholds on a blocking score: block at `>= TAU_BLOCK`, verify at
`>= TAU_REVIEW`. Both shipped provisional (#193) and were set from this fit
(#237). The script fits them on
`eval/support_labels/pairs.json`, hand-labelled pairs over the synthetic
benchmark profiles:

    python eval/fit_support_threshold.py record     # live Jev, once, needs TYPESAFE_API_KEY
    python eval/fit_support_threshold.py analyze    # offline: replay the recordings, write the report

`record` runs every pair through ART's own engine (`support.build_state` and
`support.QUESTION`, so the question text lives in one place) in `auto` mode
against a **private** SQLite store, never the user's database, and exports the
cache with `harness.decisions.recordings` to `eval/support_labels/recordings.json`.
Re-running it hits that store's cache, so a pair is asked at most once.

`analyze` imports the recordings into a fresh temporary store and runs every
pair in `replay` mode: a miss raises, and the run refuses to report unless the
hit rate is 100%. It writes `REPORT.md` (the numbers) and `REVIEW.md` (what the
user reads to audit the labels), so re-running it after a
label is corrected refits the thresholds without touching the API.

The analysis mirrors the gate exactly: a pair's blocking score is
`support._worst`'s score, p(adds_unsupported) + p(contradicts), the same number
`violations` and `reviews` compare with the thresholds. The labels in
`pairs.json` were confirmed by the user after reviewing `REVIEW.md`.
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

LABEL_DIR = Path(__file__).resolve().parent / "support_labels"
PAIRS_PATH = LABEL_DIR / "pairs.json"
RECORDINGS_PATH = LABEL_DIR / "recordings.json"
REPORT_PATH = LABEL_DIR / "REPORT.md"
REVIEW_PATH = LABEL_DIR / "REVIEW.md"
DEFAULT_RECORD_DIR = Path(tempfile.gettempdir()) / "art_support_record"

LABELS = ("supported", "adds_unsupported", "contradicts")
BLOCKING_LABELS = ("adds_unsupported", "contradicts")
GRID_BLOCK = [round(0.5 + 0.05 * i, 2) for i in range(10)]     # 0.50 .. 0.95
GRID_REVIEW = [round(0.2 + 0.05 * i, 2) for i in range(9)]     # 0.20 .. 0.60
HEADROOM = 0.05                     # tau_block sits one grid step above the worst supported pair
PROVISIONAL = (0.80, 0.40)        # #193: shipped, scoring by the larger blocking label
REVIEW_NOISE_LIMIT = 0.10           # at most this share of supported pairs may land in the review band


# ── pairs and running them through the engine ───────────────────────────────

def load_pairs(path: Path = PAIRS_PATH) -> List[Dict[str, Any]]:
    return json.loads(Path(path).read_text(encoding="utf-8"))["pairs"]


def pair_state(pair: Dict[str, Any]) -> Dict[str, Any]:
    from harness.decisions.support import build_state
    return build_state(pair["evidence"], pair["bullet"], pair.get("original"))


def ask(pair: Dict[str, Any]):
    """The one question `support.py` asks, for this pair, in the current mode."""
    from harness.decisions import engine
    from harness.decisions.support import QUESTION
    return engine.decide("support", pair_state(pair), [QUESTION], fallback=None)[0]


def result_row(pair: Dict[str, Any], answer) -> Dict[str, Any]:
    """One pair joined to Jev's answer, scored the way the gate scores a finding."""
    from harness.decisions import support
    worst_label, worst_p = support._worst(support._finding(pair["id"], pair["bullet"], answer))
    probs = {l: float((answer.probabilities or {}).get(l, 0.0)) for l in LABELS}
    return {**pair, "jev": answer.value, "probs": probs, "p_worst": float(worst_p),
            "p_max": max(probs[l] for l in BLOCKING_LABELS),
            "worst_label": worst_label, "source": answer.source, "model": answer.model}


def run_pairs(pairs: Sequence[Dict[str, Any]], on_row=None) -> List[Dict[str, Any]]:
    rows = []
    for i, pair in enumerate(pairs, 1):
        answer = ask(pair)
        if answer.fell_back:
            raise RuntimeError(f"{pair['id']}: no Jev answer ({answer.reason})")
        rows.append(result_row(pair, answer))
        if on_row:
            on_row(i, len(pairs), rows[-1])
    return rows


# ── the analysis (pure: rows in, numbers out) ────────────────────────────────

def should_block(row) -> bool:
    return row["label"] != "supported"


def is_blocked(row, tau_block: float) -> bool:
    return row["p_worst"] >= tau_block


def in_band(row, tau_review: float, tau_block: float) -> bool:
    return tau_review <= row["p_worst"] < tau_block


def confusion(rows) -> Dict[str, Dict[str, int]]:
    m = {a: {b: 0 for b in LABELS} for a in LABELS}
    for r in rows:
        m[r["label"]][r["jev"]] += 1
    return m


def disagreements(rows) -> List[Dict[str, Any]]:
    return [r for r in rows if r["label"] != r["jev"]]


def rescored(rows, key: str = "p_max"):
    """The same rows scored by another blocking score (`p_max`: the larger of the two blocking labels,
    the score #193 shipped), so every function below can be reused unchanged."""
    return [{**r, "p_worst": r[key]} for r in rows]


def relabelled_to_jev(rows):
    """Adjudicate every disagreement that crosses the supported / blocking line in Jev's favour."""
    return [{**r, "label": r["jev"]} if (r["label"] == "supported") != (r["jev"] == "supported") else r
            for r in rows]


def agreed_only(rows):
    return [r for r in rows if r["label"] == r["jev"]]


def block_stats(rows, tau: float) -> Dict[str, Any]:
    sb = [r for r in rows if should_block(r)]
    sup = [r for r in rows if not should_block(r)]
    tp = [r for r in sb if is_blocked(r, tau)]
    fp = [r for r in sup if is_blocked(r, tau)]
    by = {l: ([r for r in sb if r["label"] == l], [r for r in sb if r["label"] == l and is_blocked(r, tau)])
          for l in BLOCKING_LABELS}
    return {"tau": tau, "n_should": len(sb), "n_supported": len(sup), "tp": len(tp), "fp": len(fp),
            "fp_ids": [r["id"] for r in fp],
            "precision": len(tp) / (len(tp) + len(fp)) if (tp or fp) else None,
            "recall": len(tp) / len(sb) if sb else None,
            "by_label": {l: (len(hit), len(all_)) for l, (all_, hit) in by.items()}}


def band_stats(rows, tau_review: float, tau_block: float) -> Dict[str, Any]:
    band = [r for r in rows if in_band(r, tau_review, tau_block)]
    below = [r for r in rows if should_block(r) and r["p_worst"] < tau_review]
    return {"tau_review": tau_review, "tau_block": tau_block, "n": len(band),
            "should": sum(should_block(r) for r in band),
            "supported": sum(not should_block(r) for r in band),
            "missed": len(below), "missed_contradicts": sum(r["label"] == "contradicts" for r in below),
            "missed_ids": [r["id"] for r in below]}


def recommend(rows) -> Dict[str, Any]:
    """tau_block: the lowest grid value one step above the highest blocking score on any
    pair labelled `supported` (so no honest edit is refused, with a step of headroom);
    when no grid value clears that, the lowest one with the fewest false blocks.
    tau_review: among grid values below tau_block whose review band holds at most
    REVIEW_NOISE_LIMIT of the supported pairs, the one that surfaces the most
    should-block pairs for verification, then the fewest supported ones, then the
    lowest (the widest band, a hedge for the range no labelled pair scores in)."""
    supported = [r for r in rows if not should_block(r)]
    top = max(supported, key=lambda r: r["p_worst"]) if supported else None
    floor = (top["p_worst"] if top else 0.0) + HEADROOM
    clear = [t for t in GRID_BLOCK if t >= floor - 1e-9]
    if clear:
        tau_block = clear[0]
    else:
        tau_block = min(GRID_BLOCK, key=lambda t: (block_stats(rows, t)["fp"], t))
    limit = math.floor(REVIEW_NOISE_LIMIT * len(supported))
    ok = [t for t in GRID_REVIEW if t < tau_block and band_stats(rows, t, tau_block)["supported"] <= limit]
    tau_review = (min(ok, key=lambda t: (-band_stats(rows, t, tau_block)["should"],
                                         band_stats(rows, t, tau_block)["supported"], t))
                  if ok else round(tau_block / 2, 2))
    return {"tau_block": tau_block, "tau_review": tau_review, "top_supported": top,
            "cleared_headroom": bool(clear), "noise_limit": limit}


def groups(rows) -> List[tuple]:
    cats = sorted({r["category"] for r in rows})
    out = [(c, [r for r in rows if r["category"] == c]) for c in cats]
    out.append(("negation: true", [r for r in rows if r.get("negation")]))
    out.append(("negation: false", [r for r in rows if not r.get("negation")]))
    out.append(("touches_numbers: true", [r for r in rows if r.get("touches_numbers")]))
    return out


def outcome(rows, tau_block: float, tau_review: float) -> Dict[str, int]:
    o = {"block": 0, "review": 0, "pass": 0}
    for r in rows:
        o["block" if is_blocked(r, tau_block) else "review" if in_band(r, tau_review, tau_block) else "pass"] += 1
    return o


# ── rendering ────────────────────────────────────────────────────────────────

def _pct(x: Optional[float]) -> str:
    return "n/a" if x is None else f"{x:.0%}"


def _table(head: Sequence[str], body: Sequence[Sequence[Any]]) -> str:
    lines = ["| " + " | ".join(head) + " |", "|" + "|".join("---" for _ in head) + "|"]
    lines += ["| " + " | ".join(str(c) for c in row) + " |" for row in body]
    return "\n".join(lines)


def _probs(r) -> str:
    return f"sup {r['probs']['supported']:.2f} / adds {r['probs']['adds_unsupported']:.2f} / contra {r['probs']['contradicts']:.2f}"


def render_report(rows, meta: Dict[str, Any]) -> str:
    from harness.decisions.support import TAU_BLOCK, TAU_REVIEW
    cur_b, cur_r = TAU_BLOCK, TAU_REVIEW
    rec = recommend(rows)
    tb, tr = rec["tau_block"], rec["tau_review"]
    n, sup = len(rows), [r for r in rows if not should_block(r)]
    st, bs = block_stats(rows, tb), band_stats(rows, tr, tb)
    negs = [r for r in rows if r.get("negation")]
    neg_st = block_stats(negs, tb)
    con = confusion(rows)
    dis = disagreements(rows)
    cross = [r for r in dis if (r["label"] == "supported") != (r["jev"] == "supported")]

    o: List[str] = []
    o.append("# Support check: threshold fit (issue #237)\n")
    o.append("Generated by `python eval/fit_support_threshold.py analyze` from `pairs.json` and `recordings.json`. "
             "The labels are the ones the user confirmed from `REVIEW.md`; correct a label in `pairs.json` and "
             "re-run `analyze` to refit.\n")
    o.append(f"- Recorded {meta.get('recorded', 'n/a')} with model {', '.join(meta.get('models') or ['n/a'])}, "
             f"question `{meta.get('question', 'support@v1')}`; {n} pairs, replayed at "
             f"{meta.get('hit_rate', 'n/a')} cache hit rate.")
    o.append(f"- Labels: " + ", ".join(f"{l} {sum(r['label'] == l for r in rows)}" for l in LABELS)
             + f". `should block` = label is not `supported` ({st['n_should']}); supported = {st['n_supported']}.")
    o.append(f"- Thresholds now in `support.py`: `TAU_BLOCK` {cur_b}, `TAU_REVIEW` {cur_r} "
             f"({'they match the fit below' if (cur_b, cur_r) == (tb, tr) else 'they differ from the fit below'}).")
    o.append("- The blocking score of a pair is `support._worst`'s score: p(adds_unsupported) + p(contradicts), "
             "i.e. 1 - p(supported). Block when it is `>= tau_block`; review when `tau_review <= score < tau_block`.\n")

    # recommendation
    top = rec["top_supported"]
    o.append("## Recommendation\n")
    o.append(f"**tau_block = {tb:.2f}, tau_review = {tr:.2f}.**\n")
    para = (f"A false block refuses an honest edit, so tau_block is set first from the supported pairs: the highest "
            f"blocking score on any pair labelled `supported` is {top['p_worst']:.2f} (`{top['id']}`), and {tb:.2f} is "
            f"the first grid value one step ({HEADROOM:.2f}) above it, so it blocks {st['fp']} of {st['n_supported']} "
            f"supported pairs" if top else "No supported pairs. ")
    if not rec["cleared_headroom"]:
        para += " (no grid value clears that with headroom; this is the value with the fewest false blocks)"
    by = st["by_label"]
    step = round(tb - 0.05, 2)
    if top and step in GRID_BLOCK and block_stats(rows, step)["fp"] == 0:
        s2 = block_stats(rows, step)
        para += (f" (the next step down, {step:.2f}, also blocks no supported pair and catches {s2['tp']}/"
                 f"{s2['n_should']}, but with only {step - top['p_worst']:.2f} of headroom above the worst "
                 f"supported pair)")
    para += (f". At that value it blocks {st['tp']} of {st['n_should']} should-block pairs ({_pct(st['recall'])}): "
             f"contradicts {by['contradicts'][0]}/{by['contradicts'][1]}, adds_unsupported "
             f"{by['adds_unsupported'][0]}/{by['adds_unsupported'][1]}, at {_pct(st['precision'])} precision. "
             f"tau_review = {tr:.2f} is the lowest value that surfaces the most should-block pairs for the fewest "
             f"supported ones, within a cap of {rec['noise_limit']} supported pair(s) "
             f"({_pct(REVIEW_NOISE_LIMIT)} of {st['n_supported']}); it surfaces {bs['should']} more should-block pairs for the host to verify at the cost of "
             f"{bs['supported']} supported one(s), leaving {bs['missed']} should-block pairs "
             f"({bs['missed_contradicts']} contradicts) passing silently. On the {len(negs)} negation pairs, "
             f"tau_block = {tb:.2f} blocks {neg_st['tp']}/{neg_st['n_should']} of the should-block ones with "
             f"{neg_st['fp']} false block(s). Jev's confidence is not calibrated, so these are cutoffs on this "
             f"model's scores on this set, not probabilities; refit whenever the model version or the question changes.")
    o.append(para + "\n")
    o.append("### At the recommended thresholds and at the provisional ones\n")
    body = []
    old = rescored(rows)
    for name, rs, (b, r_) in (("recommended (sum score)", rows, (tb, tr)),
                              ("in support.py (sum score)", rows, (cur_b, cur_r)),
                              ("provisional #193 (max-label score)", old, PROVISIONAL)):
        s, bd = block_stats(rs, b), band_stats(rs, r_, b)
        body.append([name, f"{b:.2f}", f"{r_:.2f}", f"{s['tp']}/{s['n_should']} ({_pct(s['recall'])})",
                     f"{s['by_label']['contradicts'][0]}/{s['by_label']['contradicts'][1]}",
                     f"{s['by_label']['adds_unsupported'][0]}/{s['by_label']['adds_unsupported'][1]}",
                     _pct(s["precision"]), f"{s['fp']}/{s['n_supported']}",
                     f"{bd['n']} ({bd['should']} should / {bd['supported']} supported)", bd["missed"]])
    o.append(_table(["thresholds", "tau_block", "tau_review", "blocked (recall)", "contradicts", "adds_unsupported",
                     "precision", "false blocks", "review band", "should-block passed silently"], body))
    o.append("")
    o.append("Outcome by category at the recommended thresholds (block / review / pass):\n")
    cats = [(c, rs) for c, rs in groups(rows)]
    body = []
    for c, rs in cats:
        oc = outcome(rs, tb, tr)
        body.append([c, len(rs), sum(should_block(r) for r in rs), f"{oc['block']} / {oc['review']} / {oc['pass']}"])
    o.append(_table(["group", "n", "should block", "block / review / pass"], body))
    o.append("")
    if st["fp_ids"]:
        o.append(f"False blocks at tau_block {tb:.2f}: " + ", ".join(f"`{i}`" for i in st["fp_ids"]) + "\n")
    highest = sorted(sup, key=lambda r: -r["p_worst"])[:5]
    o.append("Supported pairs with the highest blocking score (the ones that set the floor):\n")
    o.append(_table(["id", "category", "Jev label", "blocking score", "scores"],
                    [[f"`{r['id']}`", r["category"], r["jev"], f"{r['p_worst']:.3f}", _probs(r)] for r in highest]))
    o.append("")

    o.append("## Sensitivity to label adjudication\n")
    o.append("The recommendation is set by the supported pairs with the highest blocking scores, and those are the "
             "pairs most likely to be mislabelled, so the same fit is repeated under other label sets. "
             "`cross` disagreements are the ones where the label and Jev sit on opposite sides of the "
             "supported / blocking line.\n")
    body = []
    for name, rs in (("labels as confirmed", rows),
                     ("cross disagreements relabelled to Jev's answer", relabelled_to_jev(rows)),
                     ("agreed pairs only (disputed pairs dropped)", agreed_only(rows))):
        rc = recommend(rs)
        b_, r_ = rc["tau_block"], rc["tau_review"]
        s_, bd_ = block_stats(rs, b_), band_stats(rs, r_, b_)
        cu = block_stats(rs, cur_b)
        body.append([name, len(rs), s_["n_should"], s_["n_supported"], f"{b_:.2f}", f"{r_:.2f}",
                     f"{s_['tp']}/{s_['n_should']}", f"{s_['fp']}/{s_['n_supported']}",
                     f"{bd_['n']} ({bd_['should']}/{bd_['supported']})", bd_["missed"],
                     f"{cu['tp']}/{cu['n_should']} / {cu['fp']}/{cu['n_supported']}"])
    o.append(_table(["label set", "pairs", "should block", "supported", "fitted tau_block", "fitted tau_review",
                     "blocked", "false blocks", "review band (should/supp)", "passed silently",
                     f"at support.py's {cur_b:.2f}: blocked / false blocks"], body))
    o.append("")
    alt = rescored(rows)
    arec = recommend(alt)
    atb, atr = arec["tau_block"], arec["tau_review"]
    ast_, abs_ = block_stats(alt, atb), band_stats(alt, atr, atb)
    o.append("## Why the sum score: the max-label score it replaced\n")
    o.append("#193 shipped scoring a pair by the larger of its two blocking labels, so an answer split between "
             "them (`adds_unsupported` 0.51 / `contradicts` 0.49) scored 0.51 although both block. Scoring by their "
             "sum (1 - p(supported)) treats the split as the block it is (#237).\n")
    body = []
    for t in GRID_BLOCK:
        a_, w = block_stats(rows, t), block_stats(alt, t)
        body.append([f"{t:.2f}", f"{a_['tp']}/{a_['n_should']} ({_pct(a_['recall'])})",
                     f"{a_['by_label']['contradicts'][0]}/{a_['by_label']['contradicts'][1]}",
                     f"{a_['by_label']['adds_unsupported'][0]}/{a_['by_label']['adds_unsupported'][1]}",
                     f"{a_['fp']}/{a_['n_supported']}",
                     f"{w['tp']}/{w['n_should']} ({_pct(w['recall'])})",
                     f"{w['by_label']['contradicts'][0]}/{w['by_label']['contradicts'][1]}",
                     f"{w['fp']}/{w['n_supported']}"])
    o.append(_table(["tau_block", "sum: blocked (recall)", "sum: contradicts", "sum: adds_unsupported",
                     "sum: false blocks", "max: blocked (recall)", "max: contradicts", "max: false blocks"], body))
    o.append(f"\nFit on the max-label score with the same rule: tau_block {atb:.2f}, tau_review {atr:.2f}; blocks "
             f"{ast_['tp']}/{ast_['n_should']} ({_pct(ast_['recall'])}) with {ast_['fp']}/{ast_['n_supported']} "
             f"false blocks; the review band holds {abs_['n']} ({abs_['should']} should-block / {abs_['supported']} "
             f"supported) and {abs_['missed']} should-block pairs pass silently.\n")

    # confusion
    o.append("## Label vs Jev's answer\n")
    o.append("Rows: label. Columns: Jev's argmax.\n")
    o.append(_table(["label \\ Jev"] + list(LABELS) + ["total"],
                    [[a] + [con[a][b] for b in LABELS] + [sum(con[a].values())] for a in LABELS]))
    agree = sum(con[a][a] for a in LABELS)
    o.append(f"\nAgreement {agree}/{n} ({_pct(agree / n)}). {len(dis)} disagreements; {len(cross)} of them cross the "
             f"supported / blocking line and {len(dis) - len(cross)} confuse `adds_unsupported` with `contradicts` "
             f"(both block).\n")

    # disagreements
    o.append("## Disagreements between the label and Jev\n")
    if dis:
        o.append(_table(["id", "category", "label", "Jev", "sup", "adds", "contra", "blocking score", "kind"],
                        [[f"`{r['id']}`", r["category"], r["label"], r["jev"], f"{r['probs']['supported']:.3f}",
                          f"{r['probs']['adds_unsupported']:.3f}", f"{r['probs']['contradicts']:.3f}",
                          f"{r['p_worst']:.3f}", "cross" if r in cross else "same side"]
                         for r in sorted(dis, key=lambda r: (r not in cross, r["id"]))]))
    else:
        o.append("None.")
    o.append("")

    # grid
    o.append("## tau_block grid\n")
    o.append("`should block` = label is not `supported`. False blocks are supported pairs at or above tau_block.\n")
    body = []
    for t in GRID_BLOCK:
        s = block_stats(rows, t)
        body.append([f"{t:.2f}", f"{s['tp'] + s['fp']}", _pct(s["precision"]), f"{s['tp']}/{s['n_should']}",
                     _pct(s["recall"]), f"{s['by_label']['contradicts'][0]}/{s['by_label']['contradicts'][1]}",
                     f"{s['by_label']['adds_unsupported'][0]}/{s['by_label']['adds_unsupported'][1]}",
                     f"{s['fp']}/{s['n_supported']}"])
    o.append(_table(["tau_block", "blocked", "precision", "TP/should", "recall", "contradicts recall",
                     "adds_unsupported recall", "false blocks"], body))
    o.append("")
    o.append("### Blocked / should-block, by group\n")
    head = ["group", "should"] + [f"{t:.2f}" for t in GRID_BLOCK]
    body = []
    for c, rs in groups(rows):
        sb = [r for r in rs if should_block(r)]
        if sb:
            body.append([c, len(sb)] + [sum(is_blocked(r, t) for r in sb) for t in GRID_BLOCK])
    o.append(_table(head, body))
    o.append("")
    o.append("### False blocks (supported pairs blocked), by group\n")
    head = ["group", "supported"] + [f"{t:.2f}" for t in GRID_BLOCK]
    body = []
    for c, rs in groups(rows):
        sp = [r for r in rs if not should_block(r)]
        if sp:
            body.append([c, len(sp)] + [sum(is_blocked(r, t) for r in sp) for t in GRID_BLOCK])
    o.append(_table(head, body))
    o.append("")
    o.append("### Negation pairs (`negation: true`), per tau_block\n")
    body = []
    for t in GRID_BLOCK:
        s = block_stats(negs, t)
        body.append([f"{t:.2f}", f"{s['tp']}/{s['n_should']}", _pct(s["recall"]), f"{s['fp']}/{s['n_supported']}",
                     _pct(s["precision"])])
    o.append(_table(["tau_block", "TP/should", "recall", "false blocks", "precision"], body))
    o.append("")
    o.append("### Role-inflation pairs, per tau_block\n")
    infl = [r for r in rows if r["category"] == "role_inflation"]
    body = []
    for t in GRID_BLOCK:
        s = block_stats(infl, t)
        body.append([f"{t:.2f}", f"{s['tp']}/{s['n_should']}", _pct(s["recall"])])
    o.append(_table(["tau_block", "TP/should", "recall"], body))
    o.append("")

    # review band
    o.append("## Review band\n")
    o.append("Cell: pairs with `tau_review <= score < tau_block`, as `total (should-block / supported)`.\n")
    head = ["tau_review \\ tau_block"] + [f"{t:.2f}" for t in GRID_BLOCK]
    body = []
    for r_ in GRID_REVIEW:
        row = [f"{r_:.2f}"]
        for t in GRID_BLOCK:
            if r_ >= t:
                row.append("-")
            else:
                b = band_stats(rows, r_, t)
                row.append(f"{b['n']} ({b['should']}/{b['supported']})")
        body.append(row)
    o.append(_table(head, body))
    o.append("")
    o.append("Should-block pairs that score below tau_review and so pass silently:\n")
    body = []
    for r_ in GRID_REVIEW:
        below = [r for r in rows if should_block(r) and r["p_worst"] < r_]
        body.append([f"{r_:.2f}", len(below), sum(r["label"] == "contradicts" for r in below),
                     sum(r["label"] == "adds_unsupported" for r in below)])
    o.append(_table(["tau_review", "passed silently", "contradicts", "adds_unsupported"], body))
    o.append("")
    o.append(f"Should-block pairs passing silently at tau_review {tr:.2f}: "
             + (", ".join(f"`{i}`" for i in bs["missed_ids"]) or "none") + "\n")

    # per pair
    o.append("## Every pair\n")
    o.append(_table(["id", "category", "label", "Jev", "p(supported)", "p(adds_unsupported)", "p(contradicts)", "flags"],
                    [[f"`{r['id']}`", r["category"], r["label"], r["jev"] + ("" if r["jev"] == r["label"] else " *"),
                      f"{r['probs']['supported']:.3f}", f"{r['probs']['adds_unsupported']:.3f}",
                      f"{r['probs']['contradicts']:.3f}",
                      ", ".join(f for f, on in (("negation", r.get("negation")), ("numbers", r.get("touches_numbers"))) if on)]
                     for r in rows]))
    o.append("\n`*` marks a disagreement with the label.\n")
    return "\n".join(o)


def render_review(rows) -> str:
    dis = disagreements(rows)
    agree = [r for r in rows if r["label"] == r["jev"]]

    def block(i: int, r) -> str:
        ev = " / ".join(f'"{e}"' for e in r["evidence"])
        tags = "".join(f" `{t}`" for t, on in (("negation", r.get("negation")), ("numbers", r.get("touches_numbers"))) if on)
        return "\n".join([
            f"**{i}. `{r['id']}`** ({r['category']}){tags}",
            f"- Evidence: {ev}",
            f"- Original: " + (f'"{r["original"]}"' if r.get("original") else "none"),
            f"- Bullet: \"{r['bullet']}\"",
            f"- Label: `{r['label']}`. {r['rationale']}",
            f"- Jev: `{r['jev']}` ({_probs(r)})",
            ""])

    o = ["# Support-check labels for review (issue #237)\n",
         "Each pair is one revised bullet, the cited evidence it must be supported by, and the label the user confirmed for it."
         "`supported`: every claim is stated or directly implied by the evidence. `adds_unsupported`: the bullet "
         "claims something the evidence does not state (a larger role, a scope, a tool, an outcome). `contradicts`: "
         "the evidence says otherwise. The `original` is the bullet being revised and is context only, never "
         "evidence. Numbers are the regex gate's business (#123), so pairs that touch one are tagged `numbers`.\n",
         "These are the labels the user confirmed (2026-09-29), kept for audit; to change one, correct `label` in "
         "`pairs.json` and re-run `python eval/fit_support_threshold.py analyze` to refit. Disagreements with Jev "
         "come first.\n",
         f"## Disagreements with Jev ({len(dis)})\n"]
    n = 0
    for r in sorted(dis, key=lambda r: ((r["label"] == "supported") == (r["jev"] == "supported"), r["id"])):
        n += 1
        o.append(block(n, r))
    o.append(f"## Agreements ({len(agree)})\n")
    for r in sorted(agree, key=lambda r: (r["category"], r["id"])):
        n += 1
        o.append(block(n, r))
    return "\n".join(o)


# ── commands ─────────────────────────────────────────────────────────────────

def _pin_store(data_dir: Path) -> None:
    """Point ART at a private SQLite file before `config` is imported. `config.load_dotenv()`
    never overrides a variable that is already set, so this is what keeps a `DATABASE_URL`
    in the developer's `.env` from being used (the `art jev` entry point does the same)."""
    data_dir.mkdir(parents=True, exist_ok=True)
    os.environ["ART_DATA_DIR"] = str(data_dir)
    os.environ["DATABASE_URL"] = f"sqlite:///{data_dir / 'art.db'}"
    from harness.runtime import prepare_local_store
    import database.db as db
    prepare_local_store(os.environ["DATABASE_URL"])
    url = db.engine.url
    private = Path(os.environ["ART_DATA_DIR"]).resolve() / "art.db"
    if url.get_backend_name() != "sqlite" or Path(url.database or "").resolve() != private:
        raise SystemExit("refusing to run: the engine is not the private store")


def _meta(doc: Dict[str, Any], hit_rate: Optional[float]) -> Dict[str, Any]:
    from harness.decisions.support import QUESTION
    decisions = doc.get("decisions") or []
    return {"recorded": (doc.get("exported_at") or "n/a")[:10],
            "models": sorted({d.get("resolved_model") for d in decisions if d.get("resolved_model")}),
            "question": QUESTION.version,
            "hit_rate": "n/a" if hit_rate is None else f"{hit_rate:.0%}"}


def cmd_record(args) -> int:
    pairs = load_pairs(Path(args.pairs))
    _pin_store(Path(args.data_dir))
    os.environ["ART_JEV_MODE"] = "auto"
    from harness.decisions import engine, recordings
    if engine.get_client() is None:
        print("no TYPESAFE_API_KEY in the environment or .env; nothing was recorded", file=sys.stderr)
        return 2
    engine.reset_stats()
    failures = []

    rows = []
    for i, pair in enumerate(pairs, 1):
        answer = ask(pair)
        if answer.fell_back:
            failures.append(f"{pair['id']}: {answer.reason}")
            print(f"[{i}/{len(pairs)}] FAILED {pair['id']}: {answer.reason}", flush=True)
            continue
        row = result_row(pair, answer)
        rows.append(row)
        print(f"[{i}/{len(pairs)}] {row['id']}: {row['jev']} "
              f"(sup {row['probs']['supported']:.2f} adds {row['probs']['adds_unsupported']:.2f} "
              f"contra {row['probs']['contradicts']:.2f}) via {row['source']}", flush=True)
    st = engine.stats().get("support", {})
    print(json.dumps({"pairs": len(pairs), "answered": len(rows), "failed": len(failures),
                      "live_answers": st.get("jev", 0), "cache_answers": st.get("cache", 0),
                      "requests": st.get("requests", 0), "input_tokens": round(st.get("input_tokens", 0)),
                      "output_tokens": round(st.get("output_tokens", 0))}))
    if failures:
        print("re-run `record` to retry only the failed pairs (answered ones are cached)", file=sys.stderr)
        return 1
    doc = recordings.export_recordings()
    Path(args.recordings).write_text(json.dumps(doc, sort_keys=True, indent=2, ensure_ascii=False) + "\n",
                                     encoding="utf-8")
    print(f"wrote {len(doc['decisions'])} recordings to {args.recordings}")
    return 0


def cmd_analyze(args) -> int:
    pairs = load_pairs(Path(args.pairs))
    doc = json.loads(Path(args.recordings).read_text(encoding="utf-8"))
    tmp = Path(tempfile.mkdtemp(prefix="art_support_analyze_"))
    _pin_store(tmp)
    os.environ["ART_JEV_MODE"] = "replay"
    os.environ.pop("TYPESAFE_API_KEY", None)                # replay never calls the API; make it impossible
    from harness.decisions import engine, recordings
    from harness.decisions.client import JevReplayMiss
    recordings.import_recordings(doc)
    engine.reset_stats()
    try:
        rows = run_pairs(pairs)
    except JevReplayMiss as exc:
        print(f"replay missed: {exc}", file=sys.stderr)
        return 2
    st = engine.stats()["support"]
    if st["hit_rate"] != 1.0 or st["jev"] or st["fallback"]:
        print(f"replay was not a 100% cache hit: {st}", file=sys.stderr)
        return 2
    Path(args.report).write_text(render_report(rows, _meta(doc, st["hit_rate"])) + "\n", encoding="utf-8")
    Path(args.review).write_text(render_review(rows) + "\n", encoding="utf-8")
    rec = recommend(rows)
    print(f"replayed {len(rows)} pairs, hit rate {st['hit_rate']:.0%}; recommended tau_block "
          f"{rec['tau_block']:.2f}, tau_review {rec['tau_review']:.2f}; wrote {args.report} and {args.review}")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, fn in (("record", cmd_record), ("analyze", cmd_analyze)):
        s = sub.add_parser(name)
        s.add_argument("--pairs", default=str(PAIRS_PATH))
        s.add_argument("--recordings", default=str(RECORDINGS_PATH))
        s.set_defaults(fn=fn)
        if name == "record":
            s.add_argument("--data-dir", default=str(DEFAULT_RECORD_DIR),
                           help="Private store the recording run uses (its cache makes a re-run free).")
        else:
            s.add_argument("--report", default=str(REPORT_PATH))
            s.add_argument("--review", default=str(REVIEW_PATH))
    args = p.parse_args(list(sys.argv[1:] if argv is None else argv))
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
