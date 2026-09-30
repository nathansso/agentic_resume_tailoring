"""Fit the semantic-coverage threshold on a labelled set (issue #126).

`harness/decisions/coverage.py` asks Jev, per (bullet, requirement), whether the bullet shows the
candidate meets the requirement (`requirement_covered@v1`, a `noul`), and calls a requirement
covered when some bullet's yes-probability is `>= TAU_COVER`. This script fits that one threshold
on `eval/coverage_labels/pairs.json`, hand-proposed (requirement, bullet) pairs over the synthetic
benchmark profiles. It is the shape of `eval/fit_negative_pin_threshold.py` (#232), whose store
pinning and table helpers it reuses:

    python eval/fit_coverage_threshold.py record     # live Jev, once, needs TYPESAFE_API_KEY
    python eval/fit_coverage_threshold.py analyze    # offline: replay the recordings, write the report

`record` runs every pair through ART's own engine (`coverage.question_for` and `build_state`, so
the question lives in one place) in `auto` mode against a **private** SQLite store, never the
user's database. Pairs that share a bullet go in one request, as the checker sends them, and each
pair is asked at most once: re-running hits that store's cache. It exports the cache to
`eval/coverage_labels/recordings.json`.

`analyze` imports the recordings into a fresh temporary store and replays every pair in `replay`
mode: a miss raises, and the run refuses to report unless the hit rate is 100%. It writes
`REPORT.md` (the numbers) and `REVIEW.md` (what the user reads to adjudicate the labels, with the
disagreements first), so re-running it after a label is corrected refits without the API.

The rule for the threshold, in priority order (see `recommend`): no false cover, with a grid step of
headroom over the highest score any not-covered pair reaches, or over the aspiration and near-miss
pairs alone when nothing clears every pair; then the most recall on semantic matches; then the
value farthest from both sides of the gap, because a false cover costs more than a miss and Jev's
scores are not calibrated.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from eval.fit_support_threshold import _pct, _pin_store, _table  # noqa: E402  the shared helpers

LABEL_DIR = Path(__file__).resolve().parent / "coverage_labels"
PAIRS_PATH = LABEL_DIR / "pairs.json"
RECORDINGS_PATH = LABEL_DIR / "recordings.json"
REPORT_PATH = LABEL_DIR / "REPORT.md"
REVIEW_PATH = LABEL_DIR / "REVIEW.md"
DEFAULT_RECORD_DIR = Path(tempfile.gettempdir()) / "art_coverage_record"

LABELS = ("covered", "not_covered")
POSITIVE_CATEGORIES = ("literal", "semantic")
CORE_NEGATIVE_CATEGORIES = ("aspiration", "near_miss", "soft_skill")
CATEGORY_ORDER = ("literal", "semantic", "partial", "aspiration", "near_miss", "unrelated", "boundary",
                  "soft_skill", "education")
EXPECTED_LABEL = {"literal": "covered", "semantic": "covered", "partial": "not_covered",
                  "aspiration": "not_covered", "near_miss": "not_covered", "unrelated": "not_covered",
                  "boundary": "mixed", "soft_skill": "mixed", "education": "mixed"}
GRID = [round(0.30 + 0.05 * i, 2) for i in range(14)]      # 0.30 .. 0.95
HEADROOM = 0.05              # tau sits one grid step above the worst aspiration / near-miss pair
JEV_YES = 0.5                # Jev "says" covered at or above this yes-probability


# ── pairs and running them through the engine ───────────────────────────────

def load_pairs(path: Path = PAIRS_PATH) -> List[Dict[str, Any]]:
    return json.loads(Path(path).read_text(encoding="utf-8"))["pairs"]


def pair_kind(pair: Dict[str, Any]) -> str:
    """`bullet`, or `education` for an education entry."""
    return pair.get("kind", "bullet")


def pair_point(pair: Dict[str, Any]) -> str:
    from harness.decisions.coverage import EDU_POINT, POINT
    return EDU_POINT if pair_kind(pair) == "education" else POINT


def pair_state(pair: Dict[str, Any]) -> Dict[str, Any]:
    from harness.decisions.coverage import build_state
    return build_state(pair["text"], pair_kind(pair))


def pair_question(pair: Dict[str, Any]):
    from harness.decisions.coverage import education_question_for, question_for
    make = education_question_for if pair_kind(pair) == "education" else question_for
    return make(pair["requirement"]["text"])


def ask(pair: Dict[str, Any]):
    """The one question `coverage.py` asks for this pair, in the current mode."""
    from harness.decisions import engine
    return engine.decide(pair_point(pair), pair_state(pair), [pair_question(pair)], fallback=None)[0]


def result_row(pair: Dict[str, Any], answer) -> Dict[str, Any]:
    p = round(float(answer.p), 4)
    return {**pair, "p": p, "jev": "covered" if p >= JEV_YES else "not_covered",
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

def should_cover(row) -> bool:
    return row["label"] == "covered"


def is_covered(row, tau: float) -> bool:
    return row["p"] >= tau - 1e-9


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


def cover_stats(rows, tau: float) -> Dict[str, Any]:
    pos = [r for r in rows if should_cover(r)]
    neg = [r for r in rows if not should_cover(r)]
    tp = [r for r in pos if is_covered(r, tau)]
    fp = [r for r in neg if is_covered(r, tau)]
    return {"tau": tau, "n_should": len(pos), "n_not": len(neg), "tp": len(tp), "fp": len(fp),
            "fp_ids": [r["id"] for r in fp], "missed_ids": [r["id"] for r in pos if not is_covered(r, tau)],
            "precision": len(tp) / (len(tp) + len(fp)) if (tp or fp) else None,
            "recall": len(tp) / len(pos) if pos else None}


def recommend(rows) -> Dict[str, Any]:
    """tau_cover, in priority order:

    1. No false cover, with a grid step of headroom over the highest score any not-covered
       pair reaches (rule `all`); when no grid value clears that, the same over the aspiration
       and near-miss pairs alone (rule `core`); when none does, the value with the fewest
       false covers on those two (rule `fewest`).
    2. Among the values that do, the most recall on semantic matches, then on all covered pairs.
    3. Among those, the value farthest from both sides: it maximizes the smaller of its margin
       over the highest not-covered score and its margin under the lowest covered score that it
       covers (ties go to the lower value). A false cover costs more than a miss, and Jev's
       scores are not calibrated, so the cutoff sits in the middle of the gap, not at its edge.
    """
    neg = [r for r in rows if not should_cover(r)]
    pos = [r for r in rows if should_cover(r)]
    core = [r for r in neg if r["category"] in CORE_NEGATIVE_CATEGORIES]
    sem = [r for r in rows if r["category"] == "semantic" and should_cover(r)]
    top_core = max(core, key=lambda r: r["p"]) if core else None
    top_not = max(neg, key=lambda r: r["p"]) if neg else None

    def clearing(top):
        return [t for t in GRID if t >= (top["p"] if top else 0.0) + HEADROOM - 1e-9]

    rule, cands = "all", clearing(top_not)
    if not cands:
        rule, cands = "core", clearing(top_core)
    if not cands:
        rule = "fewest"
        fewest = min(cover_stats(core, t)["fp"] for t in GRID)
        cands = [t for t in GRID if cover_stats(core, t)["fp"] == fewest]
    best = max((cover_stats(sem, t)["tp"], cover_stats(pos, t)["tp"]) for t in cands)
    cands = [t for t in cands if (cover_stats(sem, t)["tp"], cover_stats(pos, t)["tp"]) == best]
    floor = top_not if rule == "all" else top_core
    top_p = floor["p"] if floor else 0.0

    def margin(t):
        covered = [r["p"] for r in pos if is_covered(r, t)]
        return min(t - top_p, (min(covered) - t) if covered else 0.0)

    tau = max(cands, key=lambda t: (round(margin(t), 4), -t))
    return {"tau_cover": tau, "rule": rule, "top_core": top_core, "top_not": top_not,
            "candidates": cands, "margin": round(margin(tau), 4),
            "lowest_covered": min(pos, key=lambda r: r["p"]) if pos else None}


def groups(rows) -> List[tuple]:
    return [(c, [r for r in rows if r["category"] == c]) for c in CATEGORY_ORDER
            if any(r["category"] == c for r in rows)]


# ── rendering ────────────────────────────────────────────────────────────────

def _cell(x: Optional[float]) -> str:
    return "n/a" if x is None else f"{x:.0%}"


def _c(r) -> str:
    return "`" + r["id"] + "`"


def _req(r) -> str:
    return r["requirement"]["text"]


def render_report(rows, meta: Dict[str, Any]) -> str:
    from harness.decisions.coverage import TAU_COVER, VERSION
    rec = recommend(rows)
    tau = rec["tau_cover"]
    n = len(rows)
    st = cover_stats(rows, tau)
    con = confusion(rows)
    dis = disagreements(rows)
    by_cat = dict(groups(rows))
    core = [r for r in rows if r["category"] in CORE_NEGATIVE_CATEGORIES]

    o: List[str] = []
    o.append("# Semantic coverage: threshold fit (issue #126)\n")
    o.append("Generated by `python eval/fit_coverage_threshold.py analyze` from `pairs.json` and `recordings.json`. "
             + meta.get("labels_note", "The first 97 labels were confirmed by the user (2026-09-30); the `soft_skill` and "
                        "`education` labels are proposed, awaiting review of `REVIEW.md`. Correct a label in `pairs.json` "
                        "and re-run `analyze` to refit.") + "\n")
    o.append(f"- Recorded {meta.get('recorded', 'n/a')} with model {', '.join(meta.get('models') or ['n/a'])}, "
             f"question `{meta.get('question', VERSION)}`; {n} pairs, replayed at {meta.get('hit_rate', 'n/a')} cache hit rate.")
    o.append("- Labels: " + ", ".join(f"{l} {sum(r['label'] == l for r in rows)}" for l in LABELS)
             + f". `should cover` = label is `covered` ({st['n_should']}); the other {st['n_not']} must not be called covered.")
    o.append(f"- Threshold now in `coverage.py`: `TAU_COVER` {TAU_COVER} "
             f"({'it matches the fit below' if TAU_COVER == tau else 'it differs from the fit below'}).")
    o.append("- A pair's score is Jev's yes-probability that the bullet (or education entry) shows the candidate meets the requirement. "
             "A requirement is covered when some bullet or entry on the page scores `>= tau_cover`. `Jev says covered` below means score "
             f">= {JEV_YES}.\n")

    o.append("## Recommendation\n")
    o.append(f"**tau_cover = {tau:.2f}.**\n")
    top, low = rec["top_not"], rec["lowest_covered"]
    cand = ", ".join(f"{t:.2f}" for t in rec["candidates"])
    para = (f"A false cover tells the planner a requirement is met when it is not, and a miss only hides an improvement, so "
            f"tau_cover is set first so that no not-covered pair is called covered, with a grid step ({HEADROOM:.2f}) of headroom. "
            f"The highest score any not-covered pair reaches is {top['p']:.2f} (`{top['id']}`, {top['category']}); the highest "
            f"aspiration or near-miss score is {rec['top_core']['p']:.2f} (`{rec['top_core']['id']}`). ")
    if rec["rule"] == "all":
        missed = sorted((r for r in rows if should_cover(r) and not is_covered(r, tau)), key=lambda r: r["p"])
        kept = min((r for r in rows if should_cover(r) and is_covered(r, tau)), key=lambda r: r["p"])
        para += (f"The grid values that clear that and keep the best recall are {cand}; the one farthest from both sides is "
                 f"taken: {tau:.2f}, {tau - top['p']:.2f} over the highest not-covered score and {kept['p'] - tau:.2f} under "
                 f"the lowest covered score it keeps (`{kept['id']}`, {kept['p']:.2f}). "
                 + ("It misses " + ", ".join(f"`{r['id']}` ({r['p']:.2f})" for r in missed) + ". " if missed else ""))
    elif rec["rule"] == "core":
        para += (f"No grid value clears every not-covered pair with headroom, so {tau:.2f} is the value farthest from both "
                 f"sides among those ({cand}) that clear the aspiration and near-miss pairs. ")
    else:
        para += f"No grid value clears even those with headroom, so {tau:.2f} is the value with the fewest such false covers. "
    para += (f"It covers {st['tp']} of {st['n_should']} covered pairs ({_cell(st['recall'])}, precision {_cell(st['precision'])}): ")
    for c in POSITIVE_CATEGORIES:
        if c in by_cat:
            s = cover_stats(by_cat[c], tau)
            para += f"{c} {s['tp']}/{s['n_should']}; "
    para = para.rstrip("; ") + ". "
    para += (f"False covers over all {st['n_not']} not-covered pairs: {st['fp']}"
             + (" (" + ", ".join(f"`{i}`" for i in st["fp_ids"]) + ")" if st["fp_ids"] else "") + "; "
             f"over the {len(core)} aspiration and near-miss pairs: {cover_stats(core, tau)['fp']}. ")
    para += ("Jev's confidence is not calibrated, so this is a cutoff on this model's scores on this set, not a probability; "
             "refit whenever the model version or the question changes.")
    o.append(para + "\n")

    o.append("### By category at the recommended threshold\n")
    body = []
    for c, rs in groups(rows):
        s = cover_stats(rs, tau)
        body.append([c, len(rs), EXPECTED_LABEL[c], f"{sum(is_covered(r, tau) for r in rs)}/{len(rs)}",
                     f"{s['tp']}/{s['n_should']}" if s["n_should"] else "n/a", _cell(s["recall"]),
                     f"{s['fp']}/{s['n_not']}" if s["n_not"] else "n/a"])
    o.append(_table(["category", "n", "expected", "called covered", "true covers", "recall", "false covers"], body))
    o.append(f"\nPooled: {st['tp']}/{st['n_should']} covered pairs covered, {st['fp']}/{st['n_not']} false covers.\n")
    if st["fp_ids"]:
        o.append("False covers: " + ", ".join(f"`{i}`" for i in st["fp_ids"]) + "\n")
    highest = sorted([r for r in rows if not should_cover(r)], key=lambda r: -r["p"])[:8]
    o.append("Not-covered pairs with the highest score (the ones that set the floor):\n")
    o.append(_table(["id", "category", "Jev p", "requirement"], [[_c(r), r["category"], f"{r['p']:.3f}", _req(r)] for r in highest]))
    o.append("")
    o.append("Covered pairs with the lowest score (the ones that set the recall):\n")
    lowest = sorted([r for r in rows if should_cover(r)], key=lambda r: r["p"])[:8]
    o.append(_table(["id", "category", "Jev p", "requirement"], [[_c(r), r["category"], f"{r['p']:.3f}", _req(r)] for r in lowest]))
    o.append("")

    o.append("## Each category across the grid\n")
    o.append("`literal` is the control: the requirement's own words are in the bullet, so the literal target already sees it. "
             "`semantic` is what this target adds. `aspiration` and `near_miss` must never be called covered; `partial` names "
             "the tool but asks for more, and is the most contestable of the not-covered labels.\n")
    for c, rs in groups(rows):
        want = (EXPECTED_LABEL[c].replace("_", " ") if EXPECTED_LABEL[c] != "mixed" else
                f"{sum(should_cover(r) for r in rs)} covered, {sum(not should_cover(r) for r in rs)} not covered")
        body = []
        for t in GRID:
            s = cover_stats(rs, t)
            body.append([f"{t:.2f}", f"{sum(is_covered(r, t) for r in rs)}/{len(rs)}",
                         _cell(s["recall"]) if s["n_should"] else "n/a", str(s["fp"]) if s["n_not"] else "n/a"])
        o.append(f"### {c} ({len(rs)}, {'labelled ' + want if EXPECTED_LABEL[c] != 'mixed' else want})\n")
        o.append(_table(["tau_cover", "called covered", "recall", "false covers"], body))
        o.append("")

    o.append("## tau_cover grid, whole set\n")
    body = []
    for t in GRID:
        s = cover_stats(rows, t)
        body.append([f"{t:.2f}", s["tp"] + s["fp"], _cell(s["precision"]), f"{s['tp']}/{s['n_should']}", _cell(s["recall"]),
                     f"{s['fp']}/{s['n_not']}"] + [f"{sum(is_covered(r, t) for r in by_cat[c])}/{len(by_cat[c])}" for c in CATEGORY_ORDER if c in by_cat])
    o.append(_table(["tau_cover", "called covered", "precision", "TP/should", "recall", "false covers"]
                    + [f"{c} covered" for c in CATEGORY_ORDER if c in by_cat], body))
    o.append("")

    o.append("## Sensitivity to label adjudication\n")
    o.append("The fit is set by the highest-scoring aspiration and near-miss pairs, the pairs most likely to be mislabelled, so it "
             "is repeated under other label sets.\n")
    body = []
    for name, rs in (("labels as proposed", rows), ("every disagreement relabelled to Jev's answer", relabelled_to_jev(rows)),
                     ("agreed pairs only (disputed pairs dropped)", agreed_only(rows))):
        rc = recommend(rs)
        t_ = rc["tau_cover"]
        s_ = cover_stats(rs, t_)
        cu = cover_stats(rs, TAU_COVER)
        body.append([name, len(rs), s_["n_should"], s_["n_not"], f"{t_:.2f}", f"{s_['tp']}/{s_['n_should']}", f"{s_['fp']}/{s_['n_not']}",
                     f"{cu['tp']}/{cu['n_should']} / {cu['fp']}/{cu['n_not']}"])
    o.append(_table(["label set", "pairs", "covered", "not", "fitted tau_cover", "covered correctly", "false covers",
                     f"at coverage.py's {TAU_COVER:.2f}: covered / false covers"], body))
    o.append("")

    o.append("## Label vs Jev's answer\n")
    o.append(f"Rows: label. Columns: Jev says covered (score >= {JEV_YES}) or not.\n")
    o.append(_table(["label \\ Jev"] + list(LABELS) + ["total"], [[a] + [con[a][b] for b in LABELS] + [sum(con[a].values())] for a in LABELS]))
    agree = sum(con[a][a] for a in LABELS)
    o.append(f"\nAgreement {agree}/{n} ({_cell(agree / n)}). {len(dis)} disagreement(s).\n")
    o.append("## Disagreements between the label and Jev\n")
    if dis:
        o.append(_table(["id", "category", "label", "Jev", "score", "at tau_cover", "requirement"],
                        [[_c(r), r["category"], r["label"], r["jev"], f"{r['p']:.3f}",
                          "covered" if is_covered(r, tau) else "not covered", _req(r)]
                         for r in sorted(dis, key=lambda r: (r["label"], r["id"]))]))
    else:
        o.append("None.")
    o.append("")

    o.append("## Every pair\n")
    o.append(_table(["id", "category", "label", "score", "at tau_cover", "requirement"],
                    [[_c(r), r["category"], r["label"], f"{r['p']:.3f}" + ("" if r["jev"] == r["label"] else " *"),
                      "covered" if is_covered(r, tau) else "not covered", _req(r)] for r in rows]))
    o.append("\n`*` marks a disagreement with the label.\n")
    return "\n".join(o)


def render_review(rows) -> str:
    dis = disagreements(rows)
    agree = [r for r in rows if r["label"] == r["jev"]]

    def block(i: int, r) -> str:
        return "\n".join([
            f"**{i}. `{r['id']}`** ({r['category']}, {r['origin']} {pair_kind(r)}, "
            f"{'label proposed' if r.get('proposed') else 'label confirmed'})",
            f"- Requirement: \"{_req(r)}\" ({r['requirement'].get('type', 'required')})",
            f"- {'Education entry' if pair_kind(r) == 'education' else 'Bullet'}: \"{r['text']}\"",
            f"- Label: `{r['label']}`. {r['rationale']}",
            f"- Jev: score {r['p']:.2f} ({r['jev']})",
            ""])

    o = ["# Semantic-coverage labels for review (issue #126)\n",
         "Each pair is one requirement from a job posting and one resume bullet (or, for `education` pairs, one education entry "
         "as text). `covered`: it shows the candidate meets the requirement, even in other words or at a more specific level. "
         "`not_covered`: it does not: it only names a related tool or area, states interest or a plan, or falls short of what the "
         "requirement asks (years, leadership, production, scale; a different field or level of degree; a degree that is only "
         "expected where an earned one is asked). The first 97 labels were confirmed by the user (2026-09-30); the `soft_skill` "
         "and `education` labels are PROPOSED, awaiting the user's review. To change one, correct `label` in `pairs.json` and "
         "re-run `python eval/fit_coverage_threshold.py analyze` to refit. Disagreements with Jev come first.\n",
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
    from harness.decisions.coverage import EDU_VERSION, VERSION
    decisions = doc.get("decisions") or []
    return {"recorded": (doc.get("exported_at") or "n/a")[:10],
            "models": sorted({d.get("resolved_model") for d in decisions if d.get("resolved_model")}),
            "question": f"{VERSION}` and `{EDU_VERSION}", "hit_rate": "n/a" if hit_rate is None else f"{hit_rate:.0%}"}


def _total_stats(stats: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """The engine's counters summed over the bullet and the education decision points."""
    from harness.decisions.coverage import EDU_POINT, POINT
    total = {k: 0 for k in ("cache", "jev", "fallback", "replay_miss", "requests", "input_tokens", "output_tokens")}
    for point in (POINT, EDU_POINT):
        for k in total:
            total[k] += stats.get(point, {}).get(k, 0)
    answered = total["cache"] + total["jev"] + total["fallback"]
    total["hit_rate"] = round(total["cache"] / answered, 4) if answered else None
    return total


def cmd_record(args) -> int:
    pairs = load_pairs(Path(args.pairs))
    _pin_store(Path(args.data_dir))
    os.environ["ART_JEV_MODE"] = "auto"
    from harness.decisions import cache, engine, recordings
    from harness.decisions.client import configured_model
    if engine.get_client() is None:
        print("no TYPESAFE_API_KEY in the environment or .env; nothing was recorded", file=sys.stderr)
        return 2
    model = configured_model()
    wanted = {cache.cache_key(pair_state(p), pair_question(p), model) for p in pairs}
    todo = len(wanted - set(cache.get_many(wanted)))
    print(f"{todo} new questions to ask", flush=True)
    if todo > args.max_questions:
        print(f"refusing to record: {todo} questions is over --max-questions {args.max_questions}", file=sys.stderr)
        return 2
    engine.reset_stats()
    # A bullet's (or an entry's) requirements go in one request, as the checker sends them.
    by_state: Dict[str, List[Dict[str, Any]]] = {}
    for pair in pairs:
        by_state.setdefault(json.dumps([pair_point(pair), pair_state(pair)], sort_keys=True), []).append(pair)
    failures, done = [], 0
    for group in by_state.values():
        answers = engine.decide(pair_point(group[0]), pair_state(group[0]), [pair_question(p) for p in group], fallback=None)
        for pair, answer in zip(group, answers):
            done += 1
            if answer.fell_back:
                failures.append(f"{pair['id']}: {answer.reason}")
                print(f"[{done}/{len(pairs)}] FAILED {pair['id']}: {answer.reason}", flush=True)
                continue
            row = result_row(pair, answer)
            print(f"[{done}/{len(pairs)}] {row['id']}: p={row['p']:.2f} ({row['jev']}) via {row['source']}", flush=True)
    st = _total_stats(engine.stats())
    print(json.dumps({"pairs": len(pairs), "failed": len(failures), "live_answers": st["jev"],
                      "cache_answers": st["cache"], "requests": st["requests"],
                      "input_tokens": round(st["input_tokens"]), "output_tokens": round(st["output_tokens"])}))
    if failures:
        print("re-run `record` to retry only the failed pairs (answered ones are cached)", file=sys.stderr)
        return 1
    doc = recordings.export_recordings()
    # Only the answers to these pairs' current questions: a reworded question leaves the old rows in
    # the private store, and they are not part of the recording.
    doc["decisions"] = [d for d in doc["decisions"] if d["cache_key"] in wanted]
    Path(args.recordings).write_text(json.dumps(doc, sort_keys=True, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {len(doc['decisions'])} recordings to {args.recordings}")
    return 0


def cmd_analyze(args) -> int:
    pairs = load_pairs(Path(args.pairs))
    doc = json.loads(Path(args.recordings).read_text(encoding="utf-8"))
    _pin_store(Path(tempfile.mkdtemp(prefix="art_coverage_analyze_")))
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
    st = _total_stats(engine.stats())
    if st["hit_rate"] != 1.0 or st["jev"] or st["fallback"]:
        print(f"replay was not a 100% cache hit: {st}", file=sys.stderr)
        return 2
    Path(args.report).write_text(render_report(rows, _meta(doc, st["hit_rate"])) + "\n", encoding="utf-8")
    Path(args.review).write_text(render_review(rows) + "\n", encoding="utf-8")
    rec = recommend(rows)
    print(f"replayed {len(rows)} pairs, hit rate {st['hit_rate']:.0%}; recommended tau_cover "
          f"{rec['tau_cover']:.2f}; wrote {args.report} and {args.review}")
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
            s.add_argument("--max-questions", type=int, default=60, help="Refuse to ask more new questions than this.")
        else:
            s.add_argument("--report", default=str(REPORT_PATH))
            s.add_argument("--review", default=str(REVIEW_PATH))
    args = p.parse_args(list(sys.argv[1:] if argv is None else argv))
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
