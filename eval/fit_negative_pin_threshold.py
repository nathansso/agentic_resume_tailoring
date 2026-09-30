"""Fit the negative-pin check's thresholds on a labelled set (issue #232).

`harness/decisions/negative_pins.py` asks Jev, per changed bullet or item field and per pin,
whether the text mentions or refers to the pinned topic (`negative_pin@v1`, a `noul`), and
turns the yes-probability into three tiers with two thresholds: block at `>= TAU_BLOCK`,
verify at `>= TAU_REVIEW`. This script fits them on `eval/negative_pin_labels/pairs.json`,
hand-labelled (text, pin) pairs over the synthetic benchmark profiles. It is the shape of
`eval/fit_support_threshold.py` (#237), whose helpers it reuses:

    python eval/fit_negative_pin_threshold.py record     # live Jev, once, needs TYPESAFE_API_KEY
    python eval/fit_negative_pin_threshold.py analyze    # offline: replay the recordings, write the report

`record` runs every pair through ART's own engine (`negative_pins.question_for`,
`pin_topic` and `build_state`, so the question and how a pin becomes a topic live in one
place) in `auto` mode against a **private** SQLite store, never the user's database. Pairs
that share a text go in one request, as the gate sends them, and each pair is asked at most
once: re-running hits that store's cache. It exports the cache to
`eval/negative_pin_labels/recordings.json`.

`analyze` imports the recordings into a fresh temporary store and replays every pair in
`replay` mode: a miss raises, and the run refuses to report unless the hit rate is 100%. It
writes `REPORT.md` (the numbers) and `REVIEW.md` (what the user reads to adjudicate the
labels), so re-running it after a label is corrected refits without touching the API.

The rule for the thresholds, in priority order: no false block on a near-miss or unrelated
pair, with a step of headroom over the highest score any not-a-mention pair reaches; then
the most recall on paraphrase and indirect mentions, which is the lowest such threshold.
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

from eval.fit_support_threshold import _pct, _pin_store, _table  # noqa: E402  the shared helpers

LABEL_DIR = Path(__file__).resolve().parent / "negative_pin_labels"
PAIRS_PATH = LABEL_DIR / "pairs.json"
RECORDINGS_PATH = LABEL_DIR / "recordings.json"
REPORT_PATH = LABEL_DIR / "REPORT.md"
REVIEW_PATH = LABEL_DIR / "REVIEW.md"
DEFAULT_RECORD_DIR = Path(tempfile.gettempdir()) / "art_negative_pin_record"

LABELS = ("mentions", "does_not_mention")
POSITIVE_CATEGORIES = ("direct", "paraphrase", "indirect")
CORE_NEGATIVE_CATEGORIES = ("near_miss", "unrelated")
CATEGORY_ORDER = ("direct", "paraphrase", "indirect", "near_miss", "unrelated", "negation")
GRID_BLOCK = [round(0.40 + 0.05 * i, 2) for i in range(12)]    # 0.40 .. 0.95
GRID_REVIEW = [round(0.10 + 0.05 * i, 2) for i in range(11)]   # 0.10 .. 0.60
HEADROOM = 0.05                    # tau_block sits one grid step above the worst not-a-mention pair
REVIEW_NOISE_LIMIT = 0.10          # at most this share of not-a-mention pairs may land in the review band
JEV_YES = 0.5                      # Jev "says" mentions at or above this yes-probability


# ── pairs and running them through the engine ───────────────────────────────

def load_pairs(path: Path = PAIRS_PATH) -> List[Dict[str, Any]]:
    return json.loads(Path(path).read_text(encoding="utf-8"))["pairs"]


def pair_topic(pair: Dict[str, Any]) -> str:
    from harness.decisions.negative_pins import pin_topic
    return pin_topic(pair["pin"]["term"], pair["pin"].get("statement"))


def pair_state(pair: Dict[str, Any]) -> Dict[str, Any]:
    from harness.decisions.negative_pins import build_state
    return build_state(pair["text"], pair.get("kind", "bullet"))


def ask(pair: Dict[str, Any]):
    """The one question `negative_pins.py` asks, for this pair, in the current mode."""
    from harness.decisions import engine
    from harness.decisions.negative_pins import POINT, question_for
    return engine.decide(POINT, pair_state(pair), [question_for(pair_topic(pair))], fallback=None)[0]


def result_row(pair: Dict[str, Any], answer) -> Dict[str, Any]:
    p = round(float(answer.p), 4)
    return {**pair, "topic": pair_topic(pair), "p": p, "jev": "mentions" if p >= JEV_YES else "does_not_mention",
            "source": answer.source, "model": answer.model}


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
    return row["label"] == "mentions"


def is_blocked(row, tau_block: float) -> bool:
    return row["p"] >= tau_block - 1e-9


def in_band(row, tau_review: float, tau_block: float) -> bool:
    return tau_review - 1e-9 <= row["p"] < tau_block - 1e-9


def confusion(rows) -> Dict[str, Dict[str, int]]:
    m = {a: {b: 0 for b in LABELS} for a in LABELS}
    for r in rows:
        m[r["label"]][r["jev"]] += 1
    return m


def disagreements(rows) -> List[Dict[str, Any]]:
    return [r for r in rows if r["label"] != r["jev"]]


def relabelled_to_jev(rows):
    """Adjudicate every disagreement in Jev's favour."""
    return [{**r, "label": r["jev"]} for r in rows]


def agreed_only(rows):
    return [r for r in rows if r["label"] == r["jev"]]


def block_stats(rows, tau: float) -> Dict[str, Any]:
    pos = [r for r in rows if should_block(r)]
    neg = [r for r in rows if not should_block(r)]
    tp = [r for r in pos if is_blocked(r, tau)]
    fp = [r for r in neg if is_blocked(r, tau)]
    return {"tau": tau, "n_should": len(pos), "n_not": len(neg), "tp": len(tp), "fp": len(fp),
            "fp_ids": [r["id"] for r in fp], "missed_ids": [r["id"] for r in pos if not is_blocked(r, tau)],
            "precision": len(tp) / (len(tp) + len(fp)) if (tp or fp) else None,
            "recall": len(tp) / len(pos) if pos else None}


def band_stats(rows, tau_review: float, tau_block: float) -> Dict[str, Any]:
    band = [r for r in rows if in_band(r, tau_review, tau_block)]
    below = [r for r in rows if should_block(r) and r["p"] < tau_review - 1e-9]
    return {"tau_review": tau_review, "tau_block": tau_block, "n": len(band),
            "should": sum(should_block(r) for r in band), "not": sum(not should_block(r) for r in band),
            "missed": len(below), "missed_ids": [r["id"] for r in below]}


def recommend(rows) -> Dict[str, Any]:
    """tau_block: the lowest grid value one step above the highest score any not-a-mention pair
    reaches, so no near-miss, unrelated or other honest text is refused and there is headroom;
    when no grid value clears that, one step above the highest near-miss or unrelated score;
    when none does, the value with the fewest false blocks on those. The lowest such value is
    also the most recall on paraphrase and indirect mentions.
    tau_review: among grid values below tau_block whose review band holds at most
    REVIEW_NOISE_LIMIT of the not-a-mention pairs, the one that surfaces the most mentions
    for verification, then the fewest not-a-mention ones, then the lowest."""
    neg = [r for r in rows if not should_block(r)]
    core = [r for r in neg if r["category"] in CORE_NEGATIVE_CATEGORIES]
    top = max(neg, key=lambda r: r["p"]) if neg else None
    top_core = max(core, key=lambda r: r["p"]) if core else None
    rule = "all"
    clear = [t for t in GRID_BLOCK if t >= (top["p"] if top else 0.0) + HEADROOM - 1e-9]
    if not clear:
        rule = "core"
        clear = [t for t in GRID_BLOCK if t >= (top_core["p"] if top_core else 0.0) + HEADROOM - 1e-9]
    if clear:
        tau_block = clear[0]
    else:
        rule = "fewest"
        tau_block = min(GRID_BLOCK, key=lambda t: (block_stats(core, t)["fp"], t))
    limit = math.floor(REVIEW_NOISE_LIMIT * len(neg))
    ok = [t for t in GRID_REVIEW if t < tau_block and band_stats(rows, t, tau_block)["not"] <= limit]
    tau_review = (min(ok, key=lambda t: (-band_stats(rows, t, tau_block)["should"],
                                         band_stats(rows, t, tau_block)["not"], t))
                  if ok else round(tau_block / 2, 2))
    return {"tau_block": tau_block, "tau_review": tau_review, "top_not": top, "top_core": top_core,
            "rule": rule, "noise_limit": limit}


def groups(rows) -> List[tuple]:
    cats = [c for c in CATEGORY_ORDER if any(r["category"] == c for r in rows)]
    out = [(c, [r for r in rows if r["category"] == c]) for c in cats]
    out.append(("field text (company / name)", [r for r in rows if r.get("kind", "bullet") != "bullet"]))
    return out


def outcome(rows, tau_block: float, tau_review: float) -> Dict[str, int]:
    o = {"block": 0, "review": 0, "pass": 0}
    for r in rows:
        o["block" if is_blocked(r, tau_block) else "review" if in_band(r, tau_review, tau_block) else "pass"] += 1
    return o


# ── rendering ────────────────────────────────────────────────────────────────

def _cell(x: Optional[float]) -> str:
    return "n/a" if x is None else f"{x:.0%}"


def _c(r) -> str:
    return "`" + r["id"] + "`"


def render_report(rows, meta: Dict[str, Any]) -> str:
    from harness.decisions.negative_pins import TAU_BLOCK, TAU_REVIEW, VERSION
    cur_b, cur_r = TAU_BLOCK, TAU_REVIEW
    rec = recommend(rows)
    tb, tr = rec["tau_block"], rec["tau_review"]
    n = len(rows)
    st, bs = block_stats(rows, tb), band_stats(rows, tr, tb)
    con = confusion(rows)
    dis = disagreements(rows)
    by_cat = dict(groups(rows))

    o: List[str] = []
    o.append("# Negative-pin check: threshold fit (issue #232)\n")
    o.append("Generated by `python eval/fit_negative_pin_threshold.py analyze` from `pairs.json` and `recordings.json`. "
             "The labels are the ones the user confirmed from `REVIEW.md` (2026-09-30); correct a label in `pairs.json` and "
             "re-run `analyze` to refit.\n")
    o.append(f"- Recorded {meta.get('recorded', 'n/a')} with model {', '.join(meta.get('models') or ['n/a'])}, "
             f"question `{meta.get('question', VERSION)}`; {n} pairs, replayed at {meta.get('hit_rate', 'n/a')} cache hit rate.")
    o.append("- Labels: " + ", ".join(f"{l} {sum(r['label'] == l for r in rows)}" for l in LABELS)
             + f". `should block` = label is `mentions` ({st['n_should']}); the other {st['n_not']} must not block.")
    o.append(f"- Thresholds now in `negative_pins.py`: `TAU_BLOCK` {cur_b}, `TAU_REVIEW` {cur_r} "
             f"({'they match the fit below' if (cur_b, cur_r) == (tb, tr) else 'they differ from the fit below'}).")
    o.append("- A pair's score is Jev's yes-probability that the text mentions the topic. Block when it is "
             "`>= tau_block`; review when `tau_review <= score < tau_block`. `Jev says mentions` below means score "
             f">= {JEV_YES}. The topic Jev is asked about is `pin_topic(term, statement)`: the statement without its "
             "leading directive, or the bare term when a negation or comparison would remain.\n")

    o.append("## Recommendation\n")
    o.append(f"**tau_block = {tb:.2f}, tau_review = {tr:.2f}.**\n")
    top, core = rec["top_not"], rec["top_core"]
    para = (f"A false block refuses an honest edit, so tau_block is set first from the pairs that must not block. The "
            f"highest score any of them reaches is {top['p']:.2f} (`{top['id']}`, {top['category']}); the highest on a "
            f"near-miss or unrelated pair is {core['p']:.2f} (`{core['id']}`). ")
    para += {"all": f"{tb:.2f} is the lowest grid value one step ({HEADROOM:.2f}) above the first, ",
             "core": f"No grid value clears the first with headroom, so {tb:.2f} is the lowest one step ({HEADROOM:.2f}) above the second, ",
             "fewest": f"No grid value clears either with headroom, so {tb:.2f} is the value with the fewest near-miss and unrelated false blocks, "}[rec["rule"]]
    para += (f"which blocks {st['fp']} of {st['n_not']} not-a-mention pairs and {block_stats(rows, tb)['tp']} of "
             f"{st['n_should']} mentions ({_cell(st['recall'])}, precision {_cell(st['precision'])}). ")
    for c in POSITIVE_CATEGORIES:
        if c in by_cat:
            s = block_stats(by_cat[c], tb)
            para += f"{c} {s['tp']}/{s['n_should']}; "
    para = para.rstrip("; ") + ". "
    para += (f"tau_review = {tr:.2f} is the lowest value that surfaces the most mentions for the fewest other pairs, within "
             f"a cap of {rec['noise_limit']} not-a-mention pair(s) ({_cell(REVIEW_NOISE_LIMIT)} of {st['n_not']}); its band "
             f"holds {bs['n']} pairs ({bs['should']} mentions, {bs['not']} not) and {bs['missed']} mentions pass silently. "
             f"Jev's confidence is not calibrated, so these are cutoffs on this model's scores on this set, not "
             f"probabilities; refit whenever the model version or the question changes.")
    o.append(para + "\n")

    o.append("### By category at the recommended thresholds\n")
    body = []
    for c, rs in groups(rows):
        s, oc = block_stats(rs, tb), outcome(rs, tb, tr)
        body.append([c, len(rs), s["n_should"], f"{oc['block']} / {oc['review']} / {oc['pass']}",
                     f"{s['tp']}/{s['n_should']}" if s["n_should"] else "n/a",
                     _cell(s["precision"]), _cell(s["recall"]), f"{s['fp']}/{s['n_not']}" if s["n_not"] else "n/a"])
    o.append(_table(["group", "n", "mentions", "block / review / pass", "blocked mentions", "block precision",
                     "block recall", "false blocks"], body))
    o.append("\nPrecision is within the group (n/a with no block; 100% in a group of mentions only), so read it beside the "
             "pooled figures: at the recommended threshold the whole set blocks "
             f"{st['tp']}/{st['n_should']} mentions with {st['fp']}/{st['n_not']} false blocks.\n")
    if st["fp_ids"]:
        o.append("False blocks: " + ", ".join(f"`{i}`" for i in st["fp_ids"]) + "\n")
    highest = sorted([r for r in rows if not should_block(r)], key=lambda r: -r["p"])[:6]
    o.append("Not-a-mention pairs with the highest score (the ones that set the floor):\n")
    o.append(_table(["id", "category", "Jev p", "topic"], [[_c(r), r["category"], f"{r['p']:.3f}", r["topic"]] for r in highest]))
    o.append("")
    o.append("Mentions with the lowest score (the ones that set the recall):\n")
    lowest = sorted([r for r in rows if should_block(r)], key=lambda r: r["p"])[:6]
    o.append(_table(["id", "category", "Jev p", "topic"], [[_c(r), r["category"], f"{r['p']:.3f}", r["topic"]] for r in lowest]))
    o.append("")

    o.append("## Direct, paraphrase, indirect and near-miss, separately\n")
    o.append("`direct` is the control: the term is in the text, so the term match already blocks it, and Jev only "
             "confirms. `paraphrase` and `indirect` are the mentions the term match misses, which is what this check "
             "adds. `near_miss` (related, not the topic) and `unrelated` must not block.\n")
    for c in ("direct", "paraphrase", "indirect", "near_miss"):
        if c not in by_cat:
            continue
        rs = by_cat[c]
        o.append(f"### {c} ({len(rs)})\n")
        want = "mentions" if c != "near_miss" else "does not mention"
        body = []
        for t in GRID_BLOCK:
            s = block_stats(rs, t)
            body.append([f"{t:.2f}", f"{sum(is_blocked(r, t) for r in rs)}/{len(rs)}",
                         _cell(s["recall"]) if s["n_should"] else "n/a", str(s["fp"]) if s["n_not"] else "n/a"])
        o.append(f"All {len(rs)} are labelled {want}.\n")
        o.append(_table(["tau_block", "blocked", "recall", "false blocks"], body))
        o.append("")

    o.append("## tau_block grid, whole set\n")
    body = []
    for t in GRID_BLOCK:
        s = block_stats(rows, t)
        body.append([f"{t:.2f}", s["tp"] + s["fp"], _cell(s["precision"]), f"{s['tp']}/{s['n_should']}", _cell(s["recall"]),
                     f"{s['fp']}/{s['n_not']}"] +
                    [f"{sum(is_blocked(r, t) for r in by_cat[c])}/{len(by_cat[c])}" for c in CATEGORY_ORDER if c in by_cat])
    o.append(_table(["tau_block", "blocked", "precision", "TP/should", "recall", "false blocks"]
                    + [f"{c} blocked" for c in CATEGORY_ORDER if c in by_cat], body))
    o.append("")

    o.append("## Review band\n")
    o.append("Cell: pairs with `tau_review <= score < tau_block`, as `total (mentions / not)`.\n")
    head = ["tau_review \\ tau_block"] + [f"{t:.2f}" for t in GRID_BLOCK]
    body = []
    for r_ in GRID_REVIEW:
        row = [f"{r_:.2f}"]
        for t in GRID_BLOCK:
            if r_ >= t:
                row.append("-")
            else:
                b = band_stats(rows, r_, t)
                row.append(f"{b['n']} ({b['should']}/{b['not']})")
        body.append(row)
    o.append(_table(head, body))
    o.append("")
    o.append(f"Review-band counts by category at tau_review {tr:.2f}, tau_block {tb:.2f}:\n")
    o.append(_table(["group", "in the review band", "of which mentions", "of which not"],
                    [[c, band_stats(rs, tr, tb)["n"], band_stats(rs, tr, tb)["should"], band_stats(rs, tr, tb)["not"]]
                     for c, rs in groups(rows)]))
    o.append("")
    o.append("Mentions that score below tau_review and so pass silently, per tau_review:\n")
    o.append(_table(["tau_review", "passed silently"] + [f"{c}" for c in POSITIVE_CATEGORIES + ("negation",) if c in by_cat],
                    [[f"{r_:.2f}", sum(should_block(r) and r["p"] < r_ - 1e-9 for r in rows)]
                     + [sum(should_block(r) and r["p"] < r_ - 1e-9 for r in by_cat[c])
                        for c in POSITIVE_CATEGORIES + ("negation",) if c in by_cat] for r_ in GRID_REVIEW]))
    o.append("")
    o.append(f"Mentions passing silently at tau_review {tr:.2f}: " + (", ".join(f"`{i}`" for i in bs["missed_ids"]) or "none") + "\n")

    o.append("## Sensitivity to label adjudication\n")
    o.append("The fit is set by the highest-scoring not-a-mention pairs and the lowest-scoring mentions, the pairs most "
             "likely to be mislabelled, so it is repeated under other label sets.\n")
    body = []
    for name, rs in (("labels as confirmed", rows), ("every disagreement relabelled to Jev's answer", relabelled_to_jev(rows)),
                     ("agreed pairs only (disputed pairs dropped)", agreed_only(rows))):
        rc = recommend(rs)
        b_, r_ = rc["tau_block"], rc["tau_review"]
        s_, bd_ = block_stats(rs, b_), band_stats(rs, r_, b_)
        cu = block_stats(rs, cur_b)
        body.append([name, len(rs), s_["n_should"], s_["n_not"], f"{b_:.2f}", f"{r_:.2f}", f"{s_['tp']}/{s_['n_should']}",
                     f"{s_['fp']}/{s_['n_not']}", f"{bd_['n']} ({bd_['should']}/{bd_['not']})", bd_["missed"],
                     f"{cu['tp']}/{cu['n_should']} / {cu['fp']}/{cu['n_not']}"])
    o.append(_table(["label set", "pairs", "mentions", "not", "fitted tau_block", "fitted tau_review", "blocked",
                     "false blocks", "review band (mentions/not)", "passed silently",
                     f"at negative_pins.py's {cur_b:.2f}: blocked / false blocks"], body))
    o.append("")

    o.append("## Label vs Jev's answer\n")
    o.append(f"Rows: label. Columns: Jev says mentions (score >= {JEV_YES}) or not.\n")
    o.append(_table(["label \\ Jev"] + list(LABELS) + ["total"], [[a] + [con[a][b] for b in LABELS] + [sum(con[a].values())]
                                                                   for a in LABELS]))
    agree = sum(con[a][a] for a in LABELS)
    o.append(f"\nAgreement {agree}/{n} ({_cell(agree / n)}). {len(dis)} disagreement(s).\n")
    o.append("## Disagreements between the label and Jev\n")
    if dis:
        o.append(_table(["id", "category", "label", "Jev", "score", "outcome at the thresholds", "topic asked"],
                        [[_c(r), r["category"], r["label"], r["jev"], f"{r['p']:.3f}",
                          "block" if is_blocked(r, tb) else "review" if in_band(r, tr, tb) else "pass", r["topic"]]
                         for r in sorted(dis, key=lambda r: (r["label"], r["id"]))]))
    else:
        o.append("None.")
    o.append("")

    o.append("## Every pair\n")
    o.append(_table(["id", "category", "kind", "label", "score", "outcome", "topic asked"],
                    [[_c(r), r["category"], r.get("kind", "bullet"), r["label"],
                      f"{r['p']:.3f}" + ("" if r["jev"] == r["label"] else " *"),
                      "block" if is_blocked(r, tb) else "review" if in_band(r, tr, tb) else "pass", r["topic"]] for r in rows]))
    o.append("\n`*` marks a disagreement with the label.\n")
    return "\n".join(o)


def render_review(rows) -> str:
    dis = disagreements(rows)
    agree = [r for r in rows if r["label"] == r["jev"]]

    def block(i: int, r) -> str:
        pin = r["pin"]
        return "\n".join([
            f"**{i}. `{r['id']}`** ({r['category']}, {r.get('kind', 'bullet')})",
            f"- Pin: term \"{pin['term']}\", statement \"{pin.get('statement') or ''}\"; Jev is asked about: \"{r['topic']}\"",
            f"- Text: \"{r['text']}\"",
            f"- Label: `{r['label']}`. {r['rationale']}",
            f"- Jev: score {r['p']:.2f} ({r['jev']})",
            ""])

    o = ["# Negative-pin labels for review (issue #232)\n",
         "Each pair is one pin (a topic the user said must never render) and one text a plan might write: a bullet, or an "
         "item's company or project name. `mentions`: the text mentions or refers to the pinned topic, by name, in other "
         "words, or by a product, employer or project that belongs to it. `does_not_mention`: it does not; a related but "
         "different topic is not a mention. These are the labels the user confirmed (2026-09-30), kept for audit; to change one, correct `label` "
         "in `pairs.json` and re-run `python eval/fit_negative_pin_threshold.py analyze` to refit. Disagreements with Jev "
         "come first.\n",
         f"## Disagreements with Jev ({len(dis)})\n"]
    n = 0
    for r in sorted(dis, key=lambda r: (r["label"], r["id"])):
        n += 1
        o.append(block(n, r))
    o.append(f"## Agreements ({len(agree)})\n")
    for r in sorted(agree, key=lambda r: (CATEGORY_ORDER.index(r["category"]), r["id"])):
        n += 1
        o.append(block(n, r))
    return "\n".join(o)


# ── commands ─────────────────────────────────────────────────────────────────

def _meta(doc: Dict[str, Any], hit_rate: Optional[float]) -> Dict[str, Any]:
    from harness.decisions.negative_pins import VERSION
    decisions = doc.get("decisions") or []
    return {"recorded": (doc.get("exported_at") or "n/a")[:10],
            "models": sorted({d.get("resolved_model") for d in decisions if d.get("resolved_model")}),
            "question": VERSION, "hit_rate": "n/a" if hit_rate is None else f"{hit_rate:.0%}"}


def cmd_record(args) -> int:
    pairs = load_pairs(Path(args.pairs))
    _pin_store(Path(args.data_dir))
    os.environ["ART_JEV_MODE"] = "auto"
    from harness.decisions import engine, recordings
    from harness.decisions.negative_pins import POINT, question_for
    if engine.get_client() is None:
        print("no TYPESAFE_API_KEY in the environment or .env; nothing was recorded", file=sys.stderr)
        return 2
    engine.reset_stats()
    # A text's pins go in one request, as the gate sends them.
    by_state: Dict[str, List[Dict[str, Any]]] = {}
    for pair in pairs:
        by_state.setdefault(json.dumps(pair_state(pair), sort_keys=True), []).append(pair)
    failures, done = [], 0
    for group in by_state.values():
        answers = engine.decide(POINT, pair_state(group[0]), [question_for(pair_topic(p)) for p in group], fallback=None)
        for pair, answer in zip(group, answers):
            done += 1
            if answer.fell_back:
                failures.append(f"{pair['id']}: {answer.reason}")
                print(f"[{done}/{len(pairs)}] FAILED {pair['id']}: {answer.reason}", flush=True)
                continue
            row = result_row(pair, answer)
            print(f"[{done}/{len(pairs)}] {row['id']}: p={row['p']:.2f} ({row['jev']}) via {row['source']}", flush=True)
    st = engine.stats().get(POINT, {})
    print(json.dumps({"pairs": len(pairs), "failed": len(failures), "live_answers": st.get("jev", 0),
                      "cache_answers": st.get("cache", 0), "requests": st.get("requests", 0),
                      "input_tokens": round(st.get("input_tokens", 0)), "output_tokens": round(st.get("output_tokens", 0))}))
    if failures:
        print("re-run `record` to retry only the failed pairs (answered ones are cached)", file=sys.stderr)
        return 1
    doc = recordings.export_recordings()
    Path(args.recordings).write_text(json.dumps(doc, sort_keys=True, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {len(doc['decisions'])} recordings to {args.recordings}")
    return 0


def cmd_analyze(args) -> int:
    pairs = load_pairs(Path(args.pairs))
    doc = json.loads(Path(args.recordings).read_text(encoding="utf-8"))
    _pin_store(Path(tempfile.mkdtemp(prefix="art_negative_pin_analyze_")))
    os.environ["ART_JEV_MODE"] = "replay"
    os.environ.pop("TYPESAFE_API_KEY", None)                # replay never calls the API; make it impossible
    from harness.decisions import engine, recordings
    from harness.decisions.client import JevReplayMiss
    from harness.decisions.negative_pins import POINT
    recordings.import_recordings(doc)
    engine.reset_stats()
    try:
        rows = run_pairs(pairs)
    except JevReplayMiss as exc:
        print(f"replay missed: {exc}", file=sys.stderr)
        return 2
    st = engine.stats()[POINT]
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
