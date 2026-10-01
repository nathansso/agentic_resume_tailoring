"""Fit the bullet library's two Jev thresholds on labelled sets (issue #199).

`harness/decisions/library.py` asks Jev two `choice` questions: which approved variant of an item fits a
job (`variant_choice@v1`, over the variants plus `no_match`) and which saved track a job starts from
(`track_baseline@v1`, over the tracks plus `none`). A pick needs Jev's probability for it to be at least
`TAU_VARIANT` / `TAU_BASELINE`. This script fits both on `eval/library_labels/variants.json` and
`baselines.json`, synthetic cases labelled by hand, and compares Jev with the #229 fallbacks (word overlap,
and the role-family lookup) on the same cases. It is the shape of `eval/fit_memory_gate_threshold.py` (#202),
whose store pinning and table helpers it reuses:

    python eval/fit_library_thresholds.py record     # live Jev, once, needs TYPESAFE_API_KEY
    python eval/fit_library_thresholds.py analyze    # offline: replay the recordings, write the report

`record` runs every case through ART's own engine (`harness.decisions.library.ask_variant` / `ask_track`, so
the questions and the state live in one place) in `auto` mode against a **private** SQLite store, never the
user's database. A case is asked once; re-running hits that store's cache. It exports the cache to
`eval/library_labels/recordings.json`.

`analyze` imports the recordings into a fresh temporary store and replays every case in `replay` mode: a miss
raises, and the run refuses to report unless the hit rate is 100%. It writes `REPORT.md` (the numbers) and
`REVIEW.md` (what the user reads to confirm the labels, disagreements first), so re-running it after a label
is corrected refits without touching the API.

The rules, in priority order, each over the grid values at or above `TAU_FLOOR` (0.5: a pick needs Jev to call
it more likely than not), each then taking the grid value closest to the middle of its gap, ties to the higher
(#126's tie-break):

- **TAU_BASELINE**: no wrong baseline (a track outside the label's acceptable set, or any track for a job no
  track fits) first, since a wrong baseline starts the job from the wrong track's content; then the most
  correct picks.
- **TAU_VARIANT**: no pick of a variant labelled `poor_fit` first; then the most correct picks. The
  variant-drift guard bounds the damage of a pick, so this is the milder rule.

**Both are fitted as if the code rules did not exist** (#202's rule): the host's explicit `role_family` winning
without a question, the drift guard, and the fallbacks are defence in depth and never grounds for a looser
threshold. The fit sees Jev's raw argmax and its probability, nothing else.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import uuid
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from eval.fit_support_threshold import _pct, _pin_store, _table  # noqa: E402  the shared helpers

LABEL_DIR = Path(__file__).resolve().parent / "library_labels"
VARIANTS_PATH = LABEL_DIR / "variants.json"
BASELINES_PATH = LABEL_DIR / "baselines.json"
RECORDINGS_PATH = LABEL_DIR / "recordings.json"
REPORT_PATH = LABEL_DIR / "REPORT.md"
REVIEW_PATH = LABEL_DIR / "REVIEW.md"
PROFILES = ROOT / "eval" / "profiles"
DEFAULT_RECORD_DIR = Path(tempfile.gettempdir()) / "art_library_record"

GRID = [round(0.05 * i, 2) for i in range(10, 20)]          # 0.50 .. 0.95
VARIANT_CATEGORIES = ("clear_best", "close_call", "no_match", "synonym", "keyword_trap", "single")
BASELINE_CATEGORIES = ("clear_match", "hybrid_title", "title_mismatch", "no_track", "near_miss")


# ── the cases, and running them through the engine ───────────────────────────

def load_variants(path: Path = VARIANTS_PATH) -> Dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_baselines(path: Path = BASELINES_PATH) -> Dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def variant_id(doc: Dict[str, Any], key: str) -> str:
    """The variant's id as production would hold one: a UUID, stable per variant."""
    return str(uuid.uuid5(uuid.UUID(doc["id_namespace"]), key))


def variant_inputs(doc: Dict[str, Any], case: Dict[str, Any]):
    """`(job title, requirements, item title, variants)` for one case; each variant is
    `{variant_id, text, key}`, in the case's order."""
    job, item = doc["jobs"][case["job"]], doc["items"][case["item"]]
    variants = [{"variant_id": variant_id(doc, k), "text": item["variants"][k]["text"], "key": k}
                for k in case["variants"]]
    return job["title"], job["requirements"], item["title"], variants


def track_rows(doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [{"track": t["track"], "title": t["title"]} for t in doc["tracks"]]


def ask_variant_case(doc: Dict[str, Any], case: Dict[str, Any]):
    """Jev's decision for one variant case, in the current mode, with no threshold (`tau=0`): the
    decision's `pick` is Jev's own argmax when that is a variant."""
    from harness.decisions import library as jl

    title, reqs, item_title, variants = variant_inputs(doc, case)
    return jl.ask_variant(title, reqs, item_title, variants, tau=0.0)


def ask_baseline_case(doc: Dict[str, Any], case: Dict[str, Any]):
    from harness.decisions import library as jl

    return jl.ask_track(case["title"], case["requirements"], track_rows(doc), tau=0.0)


def fallback_variant(doc: Dict[str, Any], case: Dict[str, Any]) -> Optional[str]:
    """What #229's overlap pick chooses for this case: the job's text is its title and requirements."""
    from harness import library

    title, reqs, _, variants = variant_inputs(doc, case)
    terms = library.job_terms(None, "\n".join([title, *[r["text"] for r in reqs]]))
    chosen, _best = library.best_variant(variants, terms)
    return chosen["key"] if chosen else None


def fallback_baseline(doc: Dict[str, Any], case: Dict[str, Any]) -> Optional[str]:
    """What #229's role-family lookup chooses: the track named after the title's family, else none."""
    from agents.job_card import role_family_from_title
    from harness.library import normalize_track

    family = normalize_track(role_family_from_title(case["title"]))
    return family if family in {t["track"] for t in doc["tracks"]} else None


def variant_row(doc, case, decision) -> Dict[str, Any]:
    key_of = {variant_id(doc, k): k for k in case["variants"]}
    return {**case, "decision": decision, "jev_pick": key_of.get(decision.pick) if decision.pick else None,
            "top": key_of.get(decision.top) if decision.top else None, "p": decision.p,
            "propensity": {key_of.get(k, k): v for k, v in decision.propensity.items()},
            "fallback_pick": fallback_variant(doc, case), "source": decision.source}


def baseline_row(doc, case, decision) -> Dict[str, Any]:
    return {**case, "decision": decision, "jev_pick": decision.pick, "top": decision.top, "p": decision.p,
            "propensity": dict(decision.propensity), "fallback_pick": fallback_baseline(doc, case),
            "source": decision.source}


def run_variants(doc, on_row=None, limit: int = 0) -> List[Dict[str, Any]]:
    rows, cases = [], doc["cases"][: limit or None]
    for i, case in enumerate(cases, 1):
        decision = ask_variant_case(doc, case)
        if decision is None:
            raise RuntimeError(f"{case['id']}: no Jev answer")
        rows.append(variant_row(doc, case, decision))
        if on_row:
            on_row(i, len(cases), rows[-1])
    return rows


def run_baselines(doc, on_row=None, limit: int = 0) -> List[Dict[str, Any]]:
    rows, cases = [], doc["cases"][: limit or None]
    for i, case in enumerate(cases, 1):
        decision = ask_baseline_case(doc, case)
        if decision is None:
            raise RuntimeError(f"{case['id']}: no Jev answer")
        rows.append(baseline_row(doc, case, decision))
        if on_row:
            on_row(i, len(cases), rows[-1])
    return rows


# ── outcomes (pure) ──────────────────────────────────────────────────────────

def picked(row, tau: float) -> Optional[str]:
    """Jev's pick at threshold `tau`: its own argmax when that is a real option and its probability is at
    least `tau`, else none."""
    return row["jev_pick"] if row["jev_pick"] and (row["p"] or 0.0) >= tau - 1e-9 else None


def variant_outcome(case, pick: Optional[str]) -> str:
    """`correct` (an acceptable variant, or no_match when none fits), `lost` (no_match though one fits),
    `poor` (a variant labelled poor_fit) or `wrong` (any other variant)."""
    if pick is None:
        return "lost" if case["best"] else "correct"
    if pick in case["best"]:
        return "correct"
    return "poor" if pick in case["poor_fit"] else "wrong"


def baseline_outcome(case, pick: Optional[str]) -> str:
    """`correct`, `lost` (none though a track fits) or `wrong` (a track outside the acceptable set,
    including any track for a job no track fits)."""
    if pick is None:
        return "lost" if case["best"] else "correct"
    return "correct" if pick in case["best"] else "wrong"


def _mid_pick(candidates: Sequence[float], floor_p: float, kept_p: Sequence[float]) -> float:
    """The grid value closest to the middle of the gap between the worst score that must stay on the wrong
    side (`floor_p`) and the lowest score that must stay on the right side; ties to the higher."""
    mid = (floor_p + (min(kept_p) if kept_p else floor_p)) / 2
    return min(candidates, key=lambda t: (round(abs(t - mid), 6), -t))


def _fit(rows, outcome, bad: str) -> Dict[str, Any]:
    """The rule for both thresholds: over the grid at or above the floor, no pick whose outcome is `bad`
    first, then the most correct picks, then the middle of the gap, ties to the higher value. Fitted on
    Jev's raw argmax and probability alone, as if no code rule existed."""
    def stats(t):
        outs = [(r, outcome(r, picked(r, t))) for r in rows]
        picks = [(r, o) for r, o in outs if picked(r, t)]
        return {"bad": sum(o == bad for _, o in picks),
                "right": sum(r["jev_pick"] in r["best"] for r, o in picks if o == "correct"),
                "wrong": sum(o == "wrong" for _, o in picks),
                "picks": len(picks), "lost": sum(o == "lost" for _, o in outs),
                "correct": sum(o == "correct" for _, o in outs)}
    table = {t: stats(t) for t in GRID}
    best = min((s["bad"], -s["right"]) for s in table.values())
    cands = [t for t, s in table.items() if (s["bad"], -s["right"]) == best]
    lo = min(cands)
    danger = sorted(((r["p"], r["id"]) for r in rows
                     if r["jev_pick"] and outcome(r, r["jev_pick"]) == bad), reverse=True)
    below = [p for p, _ in danger if p < lo - 1e-9]
    kept = [r["p"] for r in rows if r["jev_pick"] and outcome(r, r["jev_pick"]) == "correct"
            and r["jev_pick"] in r["best"] and r["p"] >= lo - 1e-9]
    tau = _mid_pick(cands, max(below, default=0.0), kept)
    return {"tau": tau, "table": table, "candidates": cands, "bad": table[tau]["bad"],
            "right": table[tau]["right"], "danger": danger, "danger_max": danger[0][0] if danger else None,
            "danger_id": danger[0][1] if danger else None, "kept_min": min(kept, default=None)}


def fit_variant(rows) -> Dict[str, Any]:
    return _fit(rows, variant_outcome, "poor")


def fit_baseline(rows) -> Dict[str, Any]:
    return _fit(rows, baseline_outcome, "wrong")


def recommend(variant_rows, baseline_rows) -> Dict[str, Any]:
    v, b = fit_variant(variant_rows), fit_baseline(baseline_rows)
    return {"tau_variant": v["tau"], "tau_baseline": b["tau"], "variant": v, "baseline": b}


def groups(rows, order) -> List[tuple]:
    return [(c, [r for r in rows if r["category"] == c]) for c in order if any(r["category"] == c for r in rows)]


def tally(rows, outcome, pick_of) -> Dict[str, int]:
    """Outcomes over `rows`, where `pick_of(row)` is the pick being judged."""
    c = Counter(outcome(r, pick_of(r)) for r in rows)
    picks = sum(pick_of(r) is not None for r in rows)
    return {"n": len(rows), "correct": c["correct"], "lost": c["lost"], "poor": c["poor"],
            "wrong": c["wrong"], "picks": picks,
            "right_picks": sum(pick_of(r) is not None and outcome(r, pick_of(r)) == "correct" for r in rows)}


def disagreements(rows, outcome) -> List[Dict[str, Any]]:
    """Cases where Jev's own answer (its argmax, no threshold) is not an acceptable one."""
    return [r for r in rows if outcome(r, r["jev_pick"]) != "correct"]


def alt_variant_pick(row, tau: float) -> Optional[str]:
    """The alternative rule, not shipped: Jev's best variant when the probability it puts on *any* variant
    (one minus its `no_match`) is at least `tau`. A close call splits Jev's mass between two good phrasings, so
    neither reaches the floor alone while `no_match` stays low."""
    mass = 1.0 - row["propensity"].get("no_match", 0.0)
    return row["top"] if row["top"] and mass >= tau - 1e-9 else None


# ── the report ───────────────────────────────────────────────────────────────

def _c(r) -> str:
    return f"`{r['id']}`"


def _dist(r, n: int = 3) -> str:
    items = sorted(r["propensity"].items(), key=lambda kv: (-kv[1], kv[0]))[:n]
    return ", ".join(f"{k} {v:.2f}" for k, v in items)


def _pick(x: Optional[str], none: str) -> str:
    return x if x else none


def _label_variant(r) -> str:
    best = ", ".join(r["best"]) if r["best"] else "no_match"
    return best + (f" (poor: {', '.join(r['poor_fit'])})" if r["poor_fit"] else "")


def _label_baseline(r) -> str:
    return " or ".join(r["best"]) if r["best"] else "none"


def _meta(doc: Dict[str, Any], hit_rate: Optional[float]) -> Dict[str, Any]:
    from harness.decisions import library as jl
    decisions = doc.get("decisions") or []
    return {"recorded": (doc.get("exported_at") or "n/a")[:10],
            "models": sorted({d.get("resolved_model") for d in decisions if d.get("resolved_model")}),
            "questions": f"{jl.VARIANT_VERSION}, {jl.BASELINE_VERSION}",
            "hit_rate": "n/a" if hit_rate is None else f"{hit_rate:.0%}",
            "input_tokens": round(sum(d.get("input_tokens") or 0 for d in decisions)),
            "output_tokens": round(sum(d.get("output_tokens") or 0 for d in decisions)),
            "decisions": len(decisions)}


def _section(title: str, rows, order, outcome, bad: str, fit, tau: float, fallback_name: str,
             label, none: str) -> List[str]:
    o = [f"## {title}\n"]
    n = len(rows)
    raw = tally(rows, outcome, lambda r: r["jev_pick"])
    at = tally(rows, outcome, lambda r: picked(r, tau))
    fb = tally(rows, outcome, lambda r: r["fallback_pick"])
    o.append(f"{n} cases. Fitted threshold **{tau:.2f}** (floor {GRID[0]:.2f}). "
             f"`correct` counts a right `{none}` as well as a right pick.\n")
    head = ["", "correct", "picks made", "right picks", "poor-fit picks" if bad == "poor" else "wrong picks",
            "lost (said none though one fits)"]
    badn = lambda t: t["poor"] if bad == "poor" else t["wrong"]          # noqa: E731
    o.append(_table(head, [
        ["Jev, its own argmax (no threshold)", f"{raw['correct']}/{n} ({_pct(raw['correct'] / n)})", raw["picks"],
         raw["right_picks"], badn(raw), raw["lost"]],
        [f"**Jev at the threshold {tau:.2f}**", f"**{at['correct']}/{n} ({_pct(at['correct'] / n)})**", at["picks"],
         at["right_picks"], badn(at), at["lost"]],
        [f"The #229 fallback ({fallback_name})", f"{fb['correct']}/{n} ({_pct(fb['correct'] / n)})", fb["picks"],
         fb["right_picks"], badn(fb), fb["lost"]]]))
    o.append("")
    if bad == "poor":
        o.append(f"Wrong picks that are not poor-fit: Jev at the threshold {at['wrong']}, the fallback {fb['wrong']}.\n")
    o.append("### By category\n")
    body = []
    for cat, rs in groups(rows, order):
        a, f_, j = tally(rs, outcome, lambda r: picked(r, tau)), tally(rs, outcome, lambda r: r["fallback_pick"]), \
            tally(rs, outcome, lambda r: r["jev_pick"])
        body.append([cat, len(rs), f"{j['correct']}/{len(rs)}", f"{a['correct']}/{len(rs)}", a["picks"],
                     a["poor"] if bad == "poor" else a["wrong"], f"{f_['correct']}/{len(rs)}", f_["picks"],
                     f_["poor"] if bad == "poor" else f_["wrong"]])
    o.append(_table(["category", "cases", "Jev argmax correct", "Jev@τ correct", "Jev@τ picks",
                     "Jev@τ " + ("poor" if bad == "poor" else "wrong"), "fallback correct", "fallback picks",
                     "fallback " + ("poor" if bad == "poor" else "wrong")], body))
    o.append("")
    o.append(f"### The threshold grid\n")
    o.append("At each grid value (Jev's own argmax kept only when its probability is at least the value): the "
             f"picks made, the right ones, the {'poor-fit' if bad == 'poor' else 'wrong'} ones (the rule's first key), "
             "and the cases left as none.\n")
    if bad == "poor":
        o.append(_table(["τ", "picks", "right picks", "poor-fit picks", "other wrong picks", "lost", "correct overall"],
                        [[f"{t:.2f}" + (" ←" if t == tau else ""), s["picks"], s["right"], s["bad"], s["wrong"],
                          s["lost"], f"{s['correct']}/{n}"] for t, s in fit["table"].items()]))
    else:
        o.append(_table(["τ", "picks", "right picks", "wrong picks", "lost", "correct overall"],
                        [[f"{t:.2f}" + (" ←" if t == tau else ""), s["picks"], s["right"], s["bad"], s["lost"],
                          f"{s['correct']}/{n}"] for t, s in fit["table"].items()]))
    o.append("")
    dm = fit["danger"]
    o.append(f"Highest {'poor-fit' if bad == 'poor' else 'wrong'} pick by Jev: "
             + (f"`{fit['danger_id']}` at {fit['danger_max']:.2f}" if dm else "none")
             + f"; lowest right pick the fit keeps: {fit['kept_min']:.2f}; candidates with the same counts: "
             f"{', '.join(f'{c:.2f}' for c in fit['candidates'])}; the fit takes the one nearest the middle of the "
             "gap, ties to the higher.\n")
    dis = disagreements(rows, outcome)
    o.append(f"### Disagreements between the label and Jev ({len(dis)})\n")
    if dis:
        o.append(_table(["id", "category", "label", "Jev (argmax, p)", "Jev distribution", "fallback"],
                        [[_c(r), r["category"], label(r), f"{_pick(r['jev_pick'], none)} {r['p']:.2f}",
                          _dist(r), _pick(r["fallback_pick"], none)] for r in dis]))
    o.append("")
    o.append("### Every case\n")
    o.append(_table(["id", "category", "label", "Jev (argmax, p)", "Jev@τ", "fallback", "Jev@τ outcome",
                     "fallback outcome"],
                    [[_c(r), r["category"], label(r), f"{_pick(r['jev_pick'], none)} {r['p']:.2f}",
                      _pick(picked(r, tau), none), _pick(r["fallback_pick"], none),
                      outcome(r, picked(r, tau)), outcome(r, r["fallback_pick"])] for r in rows]))
    o.append("")
    return o


def render_report(vrows, brows, meta: Dict[str, Any]) -> str:
    from harness.decisions import library as jl

    rec = recommend(vrows, brows)
    tv, tb = jl.TAU_VARIANT, jl.TAU_BASELINE
    o = ["# Bullet library: Jev variant choice and track baseline (issue #199)\n",
         f"Recorded {meta['recorded']} with {', '.join(meta['models'])} ({meta['decisions']} decisions, "
         f"{meta['input_tokens']} input and {meta['output_tokens']} output tokens); questions {meta['questions']}; "
         f"replay hit rate {meta['hit_rate']}. Labels: {load_variants()['status']}.\n",
         "Both thresholds are fitted on Jev's own answers alone, as if no code rule existed (the host's explicit "
         "`role_family` winning, the drift guard and the fallbacks): they only ever remove or soften a pick. "
         f"Floor {GRID[0]:.2f} for both. Shipped: `TAU_VARIANT` {tv:.2f}, `TAU_BASELINE` {tb:.2f}; the fit recommends "
         f"{rec['tau_variant']:.2f} and {rec['tau_baseline']:.2f}.\n"]
    o += _section("Variant choice", vrows, VARIANT_CATEGORIES, variant_outcome, "poor", rec["variant"], tv,
                  "word overlap", _label_variant, "no_match")
    o.append("### An alternative rule, not shipped\n")
    o.append("The shipped rule picks Jev's argmax when it is a variant and its own probability is at least the "
             "threshold. When two good phrasings of one bullet are on offer Jev splits its mass between them, so "
             "neither reaches the threshold alone while `no_match` stays low. Gating on the mass Jev puts on "
             "*any* variant (one minus its `no_match`) instead, and picking its best variant, on the same answers:\n")
    body = []
    for t in (GRID[0], 0.6, 0.7, 0.8):
        a = tally(vrows, variant_outcome, lambda r, t=t: alt_variant_pick(r, t))
        body.append([f"{t:.2f}", f"{a['correct']}/{len(vrows)}", a["picks"], a["right_picks"], a["poor"],
                     a["wrong"], a["lost"]])
    o.append(_table(["mass off no_match at least", "correct", "picks", "right picks", "poor-fit picks",
                     "other wrong picks", "lost"], body))
    o.append("")
    o += _section("Track baseline", brows, BASELINE_CATEGORIES, baseline_outcome, "wrong", rec["baseline"], tb,
                  "role-family lookup", _label_baseline, "none")
    o.append("## What the comparison does and does not say\n")
    o.append("The fallback is run on the same cases, with the job's text as its title and requirements (a real "
             "posting has more words, and the overlap pick sees all of them). The role-family lookup sees only "
             "the title, which is what it uses in production; its track names here are the role families "
             "(`data_science`, `machine_learning`, ...), the best case for it. The cases are synthetic and the "
             "labels are one planner's, pending the user's spot-check: read the numbers as a comparison between "
             "two methods on the same cases, not as an accuracy to expect.\n")
    return "\n".join(o)


def render_review(vrows, brows) -> str:
    vdoc, bdoc = load_variants(), load_baselines()
    rec = recommend(vrows, brows)
    vdis = {r["id"] for r in disagreements(vrows, variant_outcome)}
    bdis = {r["id"] for r in disagreements(brows, baseline_outcome)}

    def vblock(i: int, r) -> str:
        job = vdoc["jobs"][r["job"]]
        item = vdoc["items"][r["item"]]
        lines = [f"**{i}. `{r['id']}`** ({r['category']})",
                 f"- Job: {job['title']}",
                 "- Requirements: " + "; ".join(q["text"] for q in job["requirements"]),
                 f"- Item: {item['title']}"]
        for k in r["variants"]:
            mark = "best" if k in r["best"] else "poor fit" if k in r["poor_fit"] else "-"
            lines.append(f"  - `{k}` ({mark}, Jev {r['propensity'].get(k, 0):.2f}"
                         f"{', fallback pick' if r['fallback_pick'] == k else ''}): {item['variants'][k]['text']}")
        lines += [f"- Label: {_label_variant(r)}. {r['rationale']}",
                  f"- Jev: {_pick(r['jev_pick'], 'no_match')} at {r['p']:.2f}; no_match {r['propensity'].get('no_match', 0):.2f}",
                  f"- Fallback: {_pick(r['fallback_pick'], 'no_match')}", ""]
        return "\n".join(lines)

    def bblock(i: int, r) -> str:
        lines = [f"**{i}. `{r['id']}`** ({r['category']})", f"- Job: {r['title']}",
                 "- Requirements: " + "; ".join(q["text"] for q in r["requirements"]),
                 f"- Label: {_label_baseline(r)}. {r['rationale']}",
                 f"- Jev: {_pick(r['jev_pick'], 'none')} at {r['p']:.2f} ({_dist(r, 4)})",
                 f"- Fallback: {_pick(r['fallback_pick'], 'none')}", ""]
        return "\n".join(lines)

    o = ["# Bullet-library labels for review (issue #199)\n",
         f"**Label status: {vdoc['status']}.** Each case is synthetic. A **variant** case is one job and one item's approved "
         "variants (alternative phrasings of that item's own bullets, each angled toward a role): the label is the variants that "
         "fit the job (any one of them is right), `no_match` when none does, and the ones that clearly do not fit (`poor fit`: "
         "picking one is the worst error). A **baseline** case is one job against four saved tracks (`data_science`, "
         "`machine_learning`, `data_engineering`, `software_engineering`, each started from a job of the same name): the label is "
         "the acceptable track or tracks, or none. To change a label, correct it in `variants.json` or `baselines.json` and re-run "
         "`python eval/fit_library_thresholds.py analyze` to refit. Disagreements with Jev come first.\n",
         f"## Variant disagreements with Jev ({len(vdis)})\n"]
    n = 0
    for r in vrows:
        if r["id"] in vdis:
            n += 1
            o.append(vblock(n, r))
    o.append(f"## Baseline disagreements with Jev ({len(bdis)})\n")
    for r in brows:
        if r["id"] in bdis:
            n += 1
            o.append(bblock(n, r))
    o.append(f"## Variant agreements ({len(vrows) - len(vdis)})\n")
    for r in vrows:
        if r["id"] not in vdis:
            n += 1
            o.append(vblock(n, r))
    o.append(f"## Baseline agreements ({len(brows) - len(bdis)})\n")
    for r in brows:
        if r["id"] not in bdis:
            n += 1
            o.append(bblock(n, r))
    return "\n".join(o)


# ── commands ─────────────────────────────────────────────────────────────────

def cmd_record(args) -> int:
    vdoc, bdoc = load_variants(Path(args.variants)), load_baselines(Path(args.baselines))
    _pin_store(Path(args.data_dir))
    os.environ["ART_JEV_MODE"] = "auto"
    from harness.decisions import engine, library as jl, recordings
    if engine.get_client() is None:
        print("no TYPESAFE_API_KEY in the environment or .env; nothing was recorded", file=sys.stderr)
        return 2
    engine.reset_stats()
    failures = []
    jobs = [("variant", c) for c in vdoc["cases"][: args.limit or None]] + \
           [("baseline", c) for c in bdoc["cases"][: args.limit or None]]
    for i, (kind, case) in enumerate(jobs, 1):
        decision = ask_variant_case(vdoc, case) if kind == "variant" else ask_baseline_case(bdoc, case)
        if decision is None:
            failures.append(case["id"])
            print(f"[{i}/{len(jobs)}] FAILED {kind} {case['id']}", flush=True)
            if len(failures) >= 3 and not args.keep_going:
                print("three failures; stopping (use --keep-going to continue)", file=sys.stderr)
                break
            continue
        print(f"[{i}/{len(jobs)}] {kind} {case['id']}: {decision.top} p={decision.top_p} "
              f"pick={decision.pick} via {decision.source}", flush=True)
    st = engine.stats()
    summary = {p: {"live_answers": st.get(p, {}).get("jev", 0), "cache_answers": st.get(p, {}).get("cache", 0),
                   "requests": st.get(p, {}).get("requests", 0),
                   "input_tokens": round(st.get(p, {}).get("input_tokens", 0)),
                   "output_tokens": round(st.get(p, {}).get("output_tokens", 0))}
               for p in (jl.VARIANT_POINT, jl.BASELINE_POINT)}
    print(json.dumps({"cases": len(jobs), "failed": len(failures), **summary}))
    if failures:
        print("re-run `record` to retry only the failed cases (answered ones are cached)", file=sys.stderr)
        return 1
    out = recordings.export_recordings()
    Path(args.recordings).write_text(json.dumps(out, sort_keys=True, indent=2, ensure_ascii=False) + "\n",
                                     encoding="utf-8")
    print(f"wrote {len(out['decisions'])} recordings to {args.recordings}")
    return 0


def replay_rows(vdoc: Dict[str, Any], bdoc: Dict[str, Any]):
    """Replay every case from the cache, in `replay` mode (a miss raises `JevReplayMiss`). The store must
    already hold the recordings. Returns `(variant_rows, baseline_rows, stats)`."""
    from harness.decisions import engine

    engine.reset_stats()
    vrows, brows = run_variants(vdoc), run_baselines(bdoc)
    return vrows, brows, engine.stats()


def replay_all(vdoc, bdoc, recordings_doc: Dict[str, Any]):
    """Import the recordings into a fresh private store and replay every case, offline."""
    _pin_store(Path(tempfile.mkdtemp(prefix="art_library_analyze_")))
    os.environ["ART_JEV_MODE"] = "replay"
    os.environ.pop("TYPESAFE_API_KEY", None)                # replay never calls the API; make it impossible
    from harness.decisions import recordings
    recordings.import_recordings(recordings_doc)
    return replay_rows(vdoc, bdoc)


def cmd_analyze(args) -> int:
    vdoc, bdoc = load_variants(Path(args.variants)), load_baselines(Path(args.baselines))
    recorded = json.loads(Path(args.recordings).read_text(encoding="utf-8"))
    from harness.decisions.client import JevReplayMiss
    try:
        vrows, brows, st = replay_all(vdoc, bdoc, recorded)
    except JevReplayMiss as exc:
        print(f"replay missed: {exc}", file=sys.stderr)
        return 2
    bad = {p: s for p, s in st.items() if s["hit_rate"] != 1.0 or s["jev"] or s["fallback"]}
    if bad:
        print(f"replay was not a 100% cache hit: {bad}", file=sys.stderr)
        return 2
    total = sum(s["cache"] for s in st.values())
    Path(args.report).write_text(render_report(vrows, brows, _meta(recorded, 1.0)) + "\n", encoding="utf-8")
    Path(args.review).write_text(render_review(vrows, brows) + "\n", encoding="utf-8")
    rec = recommend(vrows, brows)
    print(f"replayed {len(vrows)} variant cases and {len(brows)} baseline cases ({total} answers), hit rate 100%; "
          f"recommended TAU_VARIANT {rec['tau_variant']:.2f}, TAU_BASELINE {rec['tau_baseline']:.2f}; "
          f"wrote {args.report} and {args.review}")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, fn in (("record", cmd_record), ("analyze", cmd_analyze)):
        s = sub.add_parser(name)
        s.add_argument("--variants", default=str(VARIANTS_PATH))
        s.add_argument("--baselines", default=str(BASELINES_PATH))
        s.add_argument("--recordings", default=str(RECORDINGS_PATH))
        s.set_defaults(fn=fn)
        if name == "record":
            s.add_argument("--data-dir", default=str(DEFAULT_RECORD_DIR),
                           help="Private store the recording run uses (its cache makes a re-run free).")
            s.add_argument("--limit", type=int, default=0, help="Only the first N cases of each set (a smoke test).")
            s.add_argument("--keep-going", action="store_true", help="Do not stop after three failures.")
        else:
            s.add_argument("--report", default=str(REPORT_PATH))
            s.add_argument("--review", default=str(REVIEW_PATH))
    args = p.parse_args(list(sys.argv[1:] if argv is None else argv))
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
