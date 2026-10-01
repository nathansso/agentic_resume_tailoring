"""Fit the memory gate's thresholds on a labelled set (issue #202).

`harness/decisions/memory_gate.py` asks Jev four questions about a user message (`memory_gate@v1`:
is it a standing preference, which direction, how strong, which catalog item) behind a heuristic
prefilter, and routes on the answers: drop below `TAU_LO`, hand to the host between `TAU_LO` and
`TAU_HI` (or whenever a condition fails), write at or above `TAU_HI`. This script fits the
thresholds on `eval/memory_gate_labels/pairs.json`, synthetic messages labelled by hand. It is the
shape of `eval/fit_coverage_threshold.py` (#126), whose store pinning and table helpers it reuses:

    python eval/fit_memory_gate_threshold.py record     # live Jev, once, needs TYPESAFE_API_KEY
    python eval/fit_memory_gate_threshold.py analyze    # offline: replay the recordings, write the report

`record` runs every message through ART's own engine (`memory_gate.ask`, so the questions and the
state live in one place) in `auto` mode against a **private** SQLite store, never the user's
database, **without the prefilter**, so the prefilter's cost can be measured. A message is asked
once; re-running hits that store's cache. The context pairs are asked twice, with and without the
previous assistant turn. It exports the cache to `eval/memory_gate_labels/recordings.json`.

`analyze` imports the recordings into a fresh temporary store and replays every message in `replay`
mode: a miss raises, and the run refuses to report unless the hit rate is 100%. It writes
`REPORT.md` (the numbers) and `REVIEW.md` (what the user reads to confirm the labels, disagreements
first), so re-running it after a label is corrected refits without touching the API.

The rules, in priority order:

- **TAU_TARGET**: no wrong binding among the true preferences Jev binds to an item, then the most
  right ones, then the grid value closest to the middle of the gap.
- **TAU_HI**: no wrong automatic write (a non-preference, a wrong direction, a wrong target, a
  strength more than one off, a wrong scope), then the most automatic writes, then the middle of
  the gap, ties to the higher value.
- **TAU_LO**: no true preference dropped, then the fewest hand-offs of non-preferences, then the
  middle of the gap, ties to the higher value.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from eval.fit_support_threshold import _pct, _pin_store, _table  # noqa: E402  the shared helpers

LABEL_DIR = Path(__file__).resolve().parent / "memory_gate_labels"
PAIRS_PATH = LABEL_DIR / "pairs.json"
RECORDINGS_PATH = LABEL_DIR / "recordings.json"
REPORT_PATH = LABEL_DIR / "REPORT.md"
REVIEW_PATH = LABEL_DIR / "REVIEW.md"
PROFILES = ROOT / "eval" / "profiles"
DEFAULT_RECORD_DIR = Path(tempfile.gettempdir()) / "art_memory_gate_record"

CATEGORY_ORDER = ("explicit_positive", "explicit_negative", "double_negation", "implicit", "format_rule",
                  "one_off_edit", "question", "experience_fact", "chit_chat", "job_scoped")
NEGATION_CATEGORIES = ("explicit_negative", "double_negation")
GRID = [round(0.05 * i, 2) for i in range(1, 20)]            # 0.05 .. 0.95
JEV_YES = 0.5                      # Jev "says" preference at or above this yes-probability


# ── the pairs, the catalog, and running them through the engine ──────────────

def load_doc(path: Path = PAIRS_PATH) -> Dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_pairs(path: Path = PAIRS_PATH) -> List[Dict[str, Any]]:
    return load_doc(path)["pairs"]


def load_context_pairs(path: Path = PAIRS_PATH) -> List[Dict[str, Any]]:
    return load_doc(path).get("context_pairs", [])


def profile_catalog(name: Optional[str] = None) -> List[Dict[str, str]]:
    """The synthetic catalog: one `eval/profiles/` profile's skills, roles and projects, and the sections."""
    from eval.profile_fixture import load_profile
    from harness.decisions.memory_gate import make_catalog

    name = name or load_doc()["profile"]
    prof = load_profile(PROFILES / f"{name}.md")
    return make_catalog(skills=[s["name"] for s in prof.skills],
                        experiences=[(e["title"], e["company"]) for e in prof.experiences],
                        projects=[p["name"] for p in prof.projects])


def ask(pair: Dict[str, Any], catalog, previous: Optional[str] = None):
    """The four answers `memory_gate.py` asks for this message, in the current mode."""
    from harness.decisions import memory_gate
    return memory_gate.ask(pair["message"], catalog, previous)


def make_row(pair: Dict[str, Any], catalog, answers, previous: Optional[str] = None) -> Dict[str, Any]:
    from harness.decisions import memory_gate as mg

    pre = mg.prefilter(pair["message"])
    guess = mg.parse_answers(answers, catalog, pair["message"])
    return {**pair, "pre": pre, "guess": guess, "previous": previous, "answers": answers,
            "jev_pref": bool(guess and guess.p >= JEV_YES)}


def run_pairs(pairs: Sequence[Dict[str, Any]], catalog, on_row=None) -> List[Dict[str, Any]]:
    rows = []
    for i, pair in enumerate(pairs, 1):
        answers = ask(pair, catalog)
        if any(a.fell_back for a in answers):
            raise RuntimeError(f"{pair['id']}: no Jev answer ({answers[0].reason})")
        rows.append(make_row(pair, catalog, answers))
        if on_row:
            on_row(i, len(pairs), rows[-1])
    return rows


def run_context(pairs: Sequence[Dict[str, Any]], catalog) -> List[Dict[str, Any]]:
    """Each context pair twice: `alone` (the message) and `with_previous` (its previous assistant turn too)."""
    out = []
    for pair in pairs:
        alone = make_row(pair, catalog, ask(pair, catalog))
        withp = make_row(pair, catalog, ask(pair, catalog, pair["previous"]), pair["previous"])
        out.append({"pair": pair, "alone": alone, "with_previous": withp})
    return out


# ── routing a row ────────────────────────────────────────────────────────────

def route_row(row, tau_lo: Optional[float] = None, tau_hi: Optional[float] = None,
              tau_target: Optional[float] = None, backstop: bool = True) -> Dict[str, Any]:
    """The routing decision `observe` would take for this message, the job known."""
    from harness.decisions import memory_gate as mg
    return mg.route(row["message"], row["pre"], row["guess"], job_known=True, write_ok=True,
                    tau_lo=tau_lo, tau_hi=tau_hi, tau_target=tau_target, backstop=backstop)


def reachable(row) -> bool:
    """A message Jev is asked about in production: a candidate that states one thing and is not role-scoped."""
    pre = row["pre"]
    return pre.candidate and pre.statements <= 1 and pre.scope != "role"


def write_is_wrong(row, d: Dict[str, Any]) -> bool:
    """Whether an automatic write for this message would be wrong: it is not a standing preference, or its
    direction, target, scope or strength (more than one off) disagrees with the label."""
    if d["action"] != "write":
        return False
    g = d["guess"]
    if not row["is_preference"]:
        return True
    return (g["direction"] != row["direction"] or g["target"] != row["target"]
            or abs(g["strength"] - (row["strength"] or 0)) > 1 or (d["scope"] == "job") != row["job_scoped"])


def write_is_inexact(row, d: Dict[str, Any]) -> bool:
    return d["action"] == "write" and not write_is_wrong(row, d) and d["guess"]["strength"] != row["strength"]


# ── the analysis (pure: rows in, numbers out) ────────────────────────────────

def _mid_pick(candidates: Sequence[float], floor_p: float, kept_p: Sequence[float]) -> float:
    """The grid value closest to the middle of the gap between the worst score that must stay on the wrong
    side (`floor_p`) and the lowest score that must stay on the right side (`kept_p`); ties to the higher."""
    mid = (floor_p + (min(kept_p) if kept_p else floor_p)) / 2
    return min(candidates, key=lambda t: (round(abs(t - mid), 6), -t))


def fit_target(rows) -> Dict[str, Any]:
    """TAU_TARGET: among the true preferences Jev binds to an item (target not no_match), no wrong binding,
    then the most right ones, then the middle of the gap."""
    every = [r for r in rows if r["is_preference"] and r["guess"] and r["guess"].target_key]
    unnamed = [r for r in every if not r["guess"].target_named]       # the message does not say the name
    binds = [r for r in every if r["guess"].target_named]
    right = [r for r in binds if r["guess"].target_key == r["target"]]
    wrong = [r for r in binds if r["guess"].target_key != r["target"]]

    def stats(t):
        return (sum(r["guess"].target_p >= t - 1e-9 for r in wrong),
                sum(r["guess"].target_p >= t - 1e-9 for r in right))
    best = min((stats(t)[0], -stats(t)[1]) for t in GRID)
    cands = [t for t in GRID if (stats(t)[0], -stats(t)[1]) == best]
    top_wrong = max((r["guess"].target_p for r in wrong), default=0.0)
    kept = [r["guess"].target_p for r in right if r["guess"].target_p >= min(cands) - 1e-9]
    tau = _mid_pick(cands, top_wrong if best[0] == 0 else 0.0, kept)
    return {"tau_target": tau, "binds": len(binds), "right": len(right), "wrong": len(wrong),
            "every": len(every), "unnamed_ids": [r["id"] for r in unnamed],
            "unnamed_wrong_ids": [r["id"] for r in unnamed if r["guess"].target_key != r["target"]],
            "right_unnamed_ids": [r["id"] for r in unnamed if r["guess"].target_key == r["target"]],
            "wrong_ids": [r["id"] for r in wrong], "top_wrong": top_wrong, "candidates": cands,
            "kept_right": sum(r["guess"].target_p >= tau - 1e-9 for r in right),
            "lowest_kept": min(kept) if kept else None}


def fit_hi(rows, tau_lo: float, tau_target: float) -> Dict[str, Any]:
    """TAU_HI: no wrong automatic write, then the most automatic writes, then the middle of the gap."""
    def stats(t):
        wrong = autos = 0
        for r in rows:
            d = route_row(r, tau_lo, t, tau_target)
            autos += d["action"] == "write"
            wrong += write_is_wrong(r, d)
        return wrong, autos
    table = {t: stats(t) for t in GRID if t > tau_lo}
    best = min((w, -a) for w, a in table.values())
    cands = [t for t, (w, a) in table.items() if (w, -a) == best]
    # The wrong writes the candidates avoid are the ones whose other conditions pass and whose p is under them.
    danger = []
    for r in rows:
        d = route_row(r, tau_lo, 0.0 + 1e-9, tau_target)        # everything but p: would it write at any p?
        if d["action"] == "write" and write_is_wrong(r, d):
            danger.append(r["guess"].p)
    kept = []
    for r in rows:
        d = route_row(r, tau_lo, min(cands), tau_target)
        if d["action"] == "write" and not write_is_wrong(r, d):
            kept.append(r["guess"].p)
    floor_p = max((p for p in danger if p < min(cands) - 1e-9), default=0.0)
    tau = _mid_pick(cands, floor_p, kept)
    return {"tau_hi": tau, "wrong": table[tau][0], "autos": table[tau][1], "table": table,
            "candidates": cands, "danger_max": max(danger, default=None), "kept_min": min(kept, default=None)}


def fit_lo(rows, tau_hi: float) -> Dict[str, Any]:
    """TAU_LO: no true preference dropped, then the fewest hand-offs of non-preferences, then the middle of the gap."""
    reach = [r for r in rows if reachable(r) and r["guess"]]
    prefs = [r for r in reach if r["is_preference"]]
    non = [r for r in reach if not r["is_preference"]]

    def stats(t):
        return (sum(r["guess"].p < t - 1e-9 for r in prefs),
                sum(t - 1e-9 <= r["guess"].p < tau_hi - 1e-9 for r in non))
    valid = [t for t in GRID if t < tau_hi and stats(t)[0] == 0]
    if valid:
        fewest = min(stats(t)[1] for t in valid)
        cands = [t for t in valid if stats(t)[1] == fewest]
        low_pref = min((r["guess"].p for r in prefs), default=0.0)
        below = [r["guess"].p for r in non if r["guess"].p < low_pref]
        tau = _mid_pick(cands, max(below, default=0.0), [low_pref])
        met = True
    else:                                                      # not even the lowest grid value keeps every preference
        tau, cands, met = min(GRID), [min(GRID)], False
    return {"tau_lo": tau, "met": met, "dropped_prefs": stats(tau)[0], "handoffs": stats(tau)[1],
            "candidates": cands, "table": {t: stats(t) for t in GRID if t < tau_hi},
            "lowest_pref": min(prefs, key=lambda r: r["guess"].p) if prefs else None}


def recommend(rows) -> Dict[str, Any]:
    """The three thresholds. TAU_TARGET first (it decides which bindings count), then TAU_HI on the writes
    it allows (it does not depend on TAU_LO), then TAU_LO below it."""
    tgt = fit_target(rows)
    hi = fit_hi(rows, 0.0, tgt["tau_target"])
    lo = fit_lo(rows, hi["tau_hi"])
    return {"tau_target": tgt["tau_target"], "tau_hi": hi["tau_hi"], "tau_lo": lo["tau_lo"],
            "target": tgt, "hi": hi, "lo": lo}


def confusion(rows, truth, pred, labels):
    m = {a: {b: 0 for b in labels} for a in labels}
    for r in rows:
        m[truth(r)][pred(r)] += 1
    return m


def prf(rows, truth, pred) -> Dict[str, Any]:
    tp = sum(truth(r) and pred(r) for r in rows)
    fp = sum((not truth(r)) and pred(r) for r in rows)
    fn = sum(truth(r) and not pred(r) for r in rows)
    return {"tp": tp, "fp": fp, "fn": fn, "precision": tp / (tp + fp) if tp + fp else None,
            "recall": tp / (tp + fn) if tp + fn else None}


def heuristic_direction(row) -> str:
    from harness.decisions import memory_gate as mg
    return mg.heuristic_guess(row["pre"])["direction"]


def groups(rows) -> List[tuple]:
    return [(c, [r for r in rows if r["category"] == c]) for c in CATEGORY_ORDER if any(r["category"] == c for r in rows)]


def direction_stats(rows) -> Dict[str, Any]:
    """Direction accuracy over the true preferences, Jev against the heuristic (suppress when negated)."""
    prefs = [r for r in rows if r["is_preference"]]
    jev = sum(r["guess"].direction == r["direction"] for r in prefs)
    heur = sum(heuristic_direction(r) == r["direction"] for r in prefs)
    return {"n": len(prefs), "jev": jev, "heuristic": heur}


def strength_stats(rows) -> Dict[str, Any]:
    prefs = [r for r in rows if r["is_preference"]]
    errs = [abs(r["guess"].strength - r["strength"]) for r in prefs]
    return {"n": len(prefs), "exact": sum(e == 0 for e in errs), "within1": sum(e <= 1 for e in errs),
            "mae": sum(errs) / len(errs) if errs else None}


def target_stats(rows) -> Dict[str, Any]:
    prefs = [r for r in rows if r["is_preference"]]
    named = [r for r in prefs if r["target"]]
    unnamed = [r for r in prefs if not r["target"]]
    return {"named": len(named), "right": sum(r["guess"].target_key == r["target"] for r in named),
            "unnamed": len(unnamed), "no_match": sum(r["guess"].target_key is None for r in unnamed)}


def disagreements(rows) -> List[Dict[str, Any]]:
    """Rows where Jev (or the gate) and the label part ways, with what differs."""
    out = []
    for r in rows:
        g = r["guess"]
        why = []
        if r["jev_pref"] != r["is_preference"]:
            why.append("standing")
        if r["is_preference"]:
            if g.direction != r["direction"]:
                why.append("direction")
            if abs(g.strength - r["strength"]) > 1:
                why.append("strength")
            if g.target_key != r["target"]:
                why.append("target")
        if why:
            out.append({**r, "differs": why})
    return out


# ── rendering ────────────────────────────────────────────────────────────────

def _c(r) -> str:
    return "`" + r["id"] + "`"


def _label(r) -> str:
    if not r["is_preference"]:
        return "not a preference"
    t = r["target"] or "no item"
    return f"{r['direction']} {t}, strength {r['strength']}" + (", job-scoped" if r["job_scoped"] else "")


def _guess(r) -> str:
    g = r["guess"]
    return (f"p {g.p:.2f}; {g.direction} {g.target_key or 'no_match'} (p {g.target_p:.2f}); strength {g.strength}")


def _outcome(r, rec) -> str:
    d = route_row(r, rec["tau_lo"], rec["tau_hi"], rec["tau_target"])
    s = d["action"] if d["action"] != "host" else f"host ({d['reason']})"
    return s + (" WRONG" if write_is_wrong(r, d) else "")


def render_report(rows, ctx_rows, meta: Dict[str, Any]) -> str:
    from harness.decisions import memory_gate as mg

    rec = recommend(rows)
    cur = (mg.TAU_LO, mg.TAU_HI, mg.TAU_TARGET)
    tlo, thi, ttg = rec["tau_lo"], rec["tau_hi"], rec["tau_target"]
    n = len(rows)
    prefs = [r for r in rows if r["is_preference"]]
    decisions = {r["id"]: route_row(r, tlo, thi, ttg) for r in rows}
    writes = [r for r in rows if decisions[r["id"]]["action"] == "write"]
    wrong = [r for r in writes if write_is_wrong(r, decisions[r["id"]])]
    inexact = [r for r in writes if write_is_inexact(r, decisions[r["id"]])]
    reach = [r for r in rows if reachable(r)]

    o: List[str] = []
    o.append("# Memory gate: threshold fit and measurement (issue #202)\n")
    o.append("Generated by `python eval/fit_memory_gate_threshold.py analyze` from `pairs.json` and `recordings.json`. "
             "The labels are user-confirmed (the user reviewed them from `REVIEW.md` and kept every proposal as is); "
             "correct a label in `pairs.json` and re-run `analyze` to refit.\n")
    o.append(f"- Recorded {meta.get('recorded', 'n/a')} with model {', '.join(meta.get('models') or ['n/a'])}, "
             f"question `{meta.get('question', mg.VERSION)}`; {n} messages (and {len(ctx_rows)} context messages, "
             f"asked with and without the previous assistant turn), replayed at {meta.get('hit_rate', 'n/a')} cache hit rate.")
    o.append(f"- Messages: {len(prefs)} user-confirmed standing preferences, {n - len(prefs)} not. The catalog is "
             f"`{meta.get('profile')}`'s ({meta.get('catalog')} items and `no_match`).")
    o.append(f"- Thresholds now in `memory_gate.py`: `TAU_LO` {cur[0]}, `TAU_HI` {cur[1]}, `TAU_TARGET` {cur[2]} "
             f"({'they match the fit below' if cur == (tlo, thi, ttg) else 'they differ from the fit below'}).")
    o.append("- Every message was asked of Jev, prefilter or not, so the prefilter's cost can be measured. In production "
             "only a candidate that states one thing and is not role-scoped is asked "
             f"({len(reach)} of {n} here). The job is taken as known when routing.\n")

    # the thresholds
    o.append("## Recommendation\n")
    o.append(f"**TAU_LO = {tlo:.2f}, TAU_HI = {thi:.2f}, TAU_TARGET = {ttg:.2f}.**\n")
    tg, hi, lo = rec["target"], rec["hi"], rec["lo"]
    o.append(f"- **TAU_TARGET {ttg:.2f}**: Jev bound {tg['every']} true preferences to a catalog item; the message names the item "
             f"in {tg['binds']} of them ({tg['right']} right, {tg['wrong']} wrong"
             + (f": {', '.join('`'+i+'`' for i in tg['wrong_ids'])}; the highest wrong binding scores {tg['top_wrong']:.2f}" if tg["wrong"] else "")
             + f"). The name check alone removes {len(tg['unnamed_ids'])} bindings the message does not name "
             f"({len(tg['unnamed_wrong_ids'])} of them wrong"
             + (f": {', '.join('`'+i+'`' for i in tg['unnamed_wrong_ids'])}" if tg["unnamed_wrong_ids"] else "")
             + f"; {len(tg['right_unnamed_ids'])} right ones go to the host with them"
             + (f": {', '.join('`'+i+'`' for i in tg['right_unnamed_ids'])}" if tg["right_unnamed_ids"] else "")
             + f"). At {ttg:.2f} the threshold keeps {tg['kept_right']} of {tg['right']} right named bindings" +
             (f" (the lowest kept scores {tg['lowest_kept']:.2f})" if tg["lowest_kept"] is not None else "") + ".")
    o.append(f"- **TAU_HI {thi:.2f}**: automatic writes {len(writes)}, wrong {len(wrong)}"
             + (f", highest-scoring would-be wrong write {hi['danger_max']:.2f}" if hi["danger_max"] is not None else "")
             + (f", lowest-scoring right write {hi['kept_min']:.2f}" if hi["kept_min"] is not None else "") + ". "
             f"Candidates with the fewest wrong and the most writes: {', '.join(f'{c:.2f}' for c in hi['candidates'])}; "
             "the middle of the gap picks one.")
    o.append(f"- **TAU_LO {tlo:.2f}**: of the {len([r for r in reach if r['is_preference']])} true preferences Jev is asked about, "
             f"{lo['dropped_prefs']} are dropped"
             + ("" if lo["met"] else " (no grid value drops none, so this is the lowest)")
             + f"; {lo['handoffs']} non-preferences are handed to the host between TAU_LO and TAU_HI. "
             + (f"The lowest-scoring true preference is `{lo['lowest_pref']['id']}` at {lo['lowest_pref']['guess'].p:.2f}." if lo["lowest_pref"] else ""))
    o.append("\nJev's confidence is not calibrated, so these are cutoffs on this model's scores on this set, not "
             "probabilities. Refit whenever the model version or a question changes.\n")

    # gate precision and recall
    o.append("## Gate precision and recall: Jev against heuristics alone\n")
    o.append("`is a preference` is the label. The classifiers: the prefilter's candidate flag alone; Jev's yes-probability "
             f"at {JEV_YES} on the candidates the prefilter lets through, and on every message; the gate's own "
             "outputs at the fitted thresholds (kept = host or write, so it is the message that reaches the host or the "
             "store; write = saved with no one asking).\n")
    cand = [r for r in rows if r["pre"].candidate]
    kinds = [
        ("heuristics alone (candidate flag)", rows, lambda r: r["pre"].candidate),
        (f"Jev p >= {JEV_YES}, on the candidates only", rows, lambda r: r["pre"].candidate and r["jev_pref"]),
        (f"Jev p >= {JEV_YES}, on every message (no prefilter)", rows, lambda r: r["jev_pref"]),
        (f"gate kept (host or write) at TAU_LO {tlo:.2f}", rows, lambda r: decisions[r["id"]]["action"] != "drop"),
        (f"gate wrote at TAU_HI {thi:.2f}", rows, lambda r: decisions[r["id"]]["action"] == "write"),
    ]
    body = []
    for name, rs, pred in kinds:
        s = prf(rs, lambda r: r["is_preference"], pred)
        body.append([name, s["tp"], s["fp"], s["fn"], _pct(s["precision"]), _pct(s["recall"])])
    o.append(_table(["classifier", "TP", "FP", "FN", "precision", "recall"], body))
    heur = prf(rows, lambda r: r["is_preference"], lambda r: r["pre"].candidate)
    jev_c = prf(rows, lambda r: r["is_preference"], lambda r: r["pre"].candidate and r["jev_pref"])
    jev_all = prf(rows, lambda r: r["is_preference"], lambda r: r["jev_pref"])
    o.append(f"\nHeuristics alone: precision {_pct(heur['precision'])}, recall {_pct(heur['recall'])}. Jev behind the "
             f"prefilter: precision {_pct(jev_c['precision'])}, recall {_pct(jev_c['recall'])}. Jev with no prefilter: "
             f"precision {_pct(jev_all['precision'])}, recall {_pct(jev_all['recall'])}. The prefilter's own recall is "
             f"{_pct(heur['recall'])}: the {heur['fn']} true preferences it drops (below) are never asked.\n")
    o.append("Messages by category: candidate (prefilter passes), Jev says preference (p >= "
             f"{JEV_YES}), and the gate's route at the fitted thresholds.\n")
    body = []
    for c, rs in groups(rows):
        acts = Counter(decisions[r["id"]]["action"] for r in rs)
        body.append([c, len(rs), sum(r["is_preference"] for r in rs), sum(r["pre"].candidate for r in rs),
                     sum(r["jev_pref"] for r in rs), acts["drop"], acts["host"], acts["write"]])
    o.append(_table(["category", "n", "preferences", "candidates", "Jev says preference", "drop", "host", "write"], body))
    missed = [r for r in prefs if not r["pre"].candidate]
    o.append("\nTrue preferences the prefilter drops (never asked of Jev): " +
             (", ".join(f"{_c(r)} (Jev p {r['guess'].p:.2f})" for r in missed) or "none") + ".\n")

    # direction
    o.append("## Direction\n")
    o.append("Accuracy over the labelled preferences. `Jev` is the direction choice; `heuristic` is the prefilter's reading "
             "alone (suppress when negated, else emphasize, so it can never say format_rule). The negation categories "
             "are reported on their own.\n")
    body = []
    for name, rs in [("all preferences", prefs)] + [(c, [r for r in rs if r["is_preference"]]) for c, rs in groups(rows)
                                                      if any(r["is_preference"] for r in rs)]:
        s = direction_stats(rs)
        body.append([name, s["n"], f"{s['jev']}/{s['n']} ({_pct(s['jev'] / s['n'])})",
                     f"{s['heuristic']}/{s['n']} ({_pct(s['heuristic'] / s['n'])})"])
    neg = [r for r in prefs if r["category"] in NEGATION_CATEGORIES]
    s = direction_stats(neg)
    body.append(["**negation categories together**", s["n"], f"{s['jev']}/{s['n']} ({_pct(s['jev'] / s['n'])})",
                 f"{s['heuristic']}/{s['n']} ({_pct(s['heuristic'] / s['n'])})"])
    o.append(_table(["group", "n", "Jev", "heuristic"], body))
    labels = ["emphasize", "suppress", "format_rule", "none"]
    con = confusion(prefs, lambda r: r["direction"], lambda r: r["guess"].direction, labels)
    o.append("\nConfusion on the preferences (rows: label; columns: Jev):\n")
    o.append(_table(["label \\ Jev"] + labels, [[a] + [con[a][b] for b in labels] for a in labels[:3]]))
    errs = [r for r in prefs if r["guess"].direction != r["direction"] and r["direction"] in ("emphasize", "suppress")
            and r["guess"].direction in ("emphasize", "suppress")]
    caught = [r for r in errs if (r["pre"].negated and r["guess"].direction != "suppress")
              or (r["guess"].direction == "suppress" and not r["pre"].negated)]
    o.append(f"\nA wrong emphasize/suppress reading is a wrong-way write. Jev made {len(errs)} such errors on the "
             f"preferences; the negation backstop (the prefilter's `negated` flag against Jev's direction) catches "
             f"{len(caught)} of them" + (f" ({', '.join(_c(r) for r in errs if r not in caught)} get through it)" if len(caught) < len(errs) else "") + ".\n")

    # strength
    o.append("## Strength\n")
    ss = strength_stats(prefs)
    o.append(f"Over the {ss['n']} preferences: exact {ss['exact']}/{ss['n']} ({_pct(ss['exact'] / ss['n'])}), within one "
             f"level {ss['within1']}/{ss['n']} ({_pct(ss['within1'] / ss['n'])}), mean absolute error {ss['mae']:.2f}.\n")
    body = []
    for c, rs in groups(rows):
        ps = [r for r in rs if r["is_preference"]]
        if ps:
            s = strength_stats(ps)
            body.append([c, s["n"], f"{s['exact']}/{s['n']}", f"{s['within1']}/{s['n']}", f"{s['mae']:.2f}"])
    o.append(_table(["category", "n", "exact", "within 1", "mean abs error"], body))
    five = [r for r in prefs if r["strength"] == 5]
    said5 = [r for r in rows if r["guess"].strength >= 5]
    o.append(f"\nStrength 5 is the pin level the gate never writes. {len(five)} messages are labelled 5; Jev says 5 for "
             f"{sum(r['guess'].strength >= 5 for r in five)} of them. It says 5 for {len(said5)} messages in all, "
             f"{sum(not r['is_preference'] or (r['strength'] or 0) < 5 for r in said5)} of them labelled below 5 (those cost a "
             f"host confirmation, not a wrong write).\n")

    # target
    o.append("## Target\n")
    ts = target_stats(prefs)
    o.append(f"Preferences that name a catalog item: Jev picks the right one for {ts['right']}/{ts['named']} "
             f"({_pct(ts['right'] / ts['named'])}). Preferences that name no item (a topic, a general rule): Jev says "
             f"`no_match` for {ts['no_match']}/{ts['unnamed']} ({_pct(ts['no_match'] / ts['unnamed'])}). Both the right "
             "binding and the no-match matter: a wrong binding writes a rule about the wrong item.\n")
    body = []
    for c, rs in groups(rows):
        ps = [r for r in rs if r["is_preference"]]
        if ps:
            s = target_stats(ps)
            body.append([c, f"{s['right']}/{s['named']}" if s["named"] else "n/a",
                         f"{s['no_match']}/{s['unnamed']}" if s["unnamed"] else "n/a"])
    o.append(_table(["category", "named item: right", "no item: no_match"], body))
    o.append("")
    o.append(f"TAU_TARGET grid (true preferences Jev binds to an item the message names; {tg['binds']} of them):\n")
    right = [r for r in prefs if r["guess"].target_key and r["guess"].target_named and r["guess"].target_key == r["target"]]
    wrongb = [r for r in prefs if r["guess"].target_key and r["guess"].target_named and r["guess"].target_key != r["target"]]
    o.append(_table(["TAU_TARGET", "right kept", "wrong kept"],
                    [[f"{t:.2f}", sum(r["guess"].target_p >= t - 1e-9 for r in right),
                      sum(r["guess"].target_p >= t - 1e-9 for r in wrongb)] for t in GRID]))
    o.append("")

    # auto-write
    o.append("## Automatic writes\n")
    o.append(f"At TAU_LO {tlo:.2f}, TAU_HI {thi:.2f}, TAU_TARGET {ttg:.2f}, the gate writes {len(writes)} of {n} messages "
             f"({len([r for r in writes if r['is_preference']])} true preferences). **Wrong writes: {len(wrong)}**; "
             f"precision {_pct((len(writes) - len(wrong)) / len(writes)) if writes else 'n/a'}. Recall over the "
             f"{len(prefs)} preferences: {_pct((len(writes) - len(wrong)) / len(prefs))}. "
             f"{len(inexact)} writes have a strength one level off the label (within the tolerance). A write is wrong when "
             "the message is not a standing preference, or its direction, target or scope differs from the label, or its "
             "strength is more than one level off.\n")
    if writes:
        o.append(_table(["id", "category", "label", "the gate wrote"],
                        [[_c(r), r["category"], _label(r), _guess(r) + (" WRONG" if r in wrong else "")] for r in writes]))
        o.append("")
    held = [r for r in prefs if decisions[r["id"]]["action"] == "host"]
    why = Counter(decisions[r["id"]]["reason"] for r in held)
    o.append("True preferences handed to the host, by reason: " + (", ".join(f"{k} {v}" for k, v in why.most_common()) or "none")
             + ". The rest of the preferences are dropped by the prefilter or below TAU_LO "
             f"({len([r for r in prefs if decisions[r['id']]['action'] == 'drop'])}).\n")
    # what the safety rules and the backstop do
    o.append("What each rule is for, counted on this set at the fitted thresholds:\n")
    hard = [r for r in rows if route_row(r, tlo, thi, ttg)["reason"] == "hard_preference"]
    held_back = [r for r in rows if route_row(r, tlo, thi, ttg)["reason"] == "negation_disagrees"]
    bare = [route_row(r, tlo, thi, ttg, backstop=False) for r in rows]
    bare_wrong = [r for r, d in zip(rows, bare) if write_is_wrong(r, d)]
    body = [["strength 5 is never written (safety rule)",
             f"{len(hard)} messages Jev is confident are preferences were held back as strength 5 or likely 5 "
             f"({sum(r['is_preference'] for r in hard)} are labelled preferences)"],
            ["negation backstop",
             f"{len(held_back)} messages went to the host because the prefilter's negation and Jev's direction disagree; "
             f"without it the gate would write {sum(d['action'] == 'write' for d in bare)} messages, {len(bare_wrong)} of them wrong"
             + (f" ({', '.join(_c(r) for r in bare_wrong)})" if bare_wrong else "")]]
    o.append(_table(["rule", "effect"], body))
    o.append("")

    # grids
    o.append("## TAU_HI grid\n")
    o.append("At each TAU_HI (TAU_TARGET fixed): automatic writes and how many are wrong.\n")
    o.append(_table(["TAU_HI", "writes", "wrong"],
                    [[f"{t:.2f}", a, w] for t, (w, a) in hi["table"].items()]))
    o.append("")
    o.append("## TAU_LO grid\n")
    o.append(f"At each TAU_LO (TAU_HI fixed at {thi:.2f}): true preferences dropped (of those Jev is asked about), and the "
             "non-preferences handed to the host between TAU_LO and TAU_HI.\n")
    o.append(_table(["TAU_LO", "preferences dropped", "non-preferences handed to the host"],
                    [[f"{t:.2f}", d, h] for t, (d, h) in lo["table"].items()]))
    o.append("")

    # context
    o.append("## The previous assistant turn\n")
    o.append("Twelve short messages that lean on the turn before (`that`, `it`, `yes, always`), asked with the message "
             "alone and with the previous assistant turn in the state. The shipped gate asks with the message alone.\n")
    body = []
    for c in ctx_rows:
        a, w, p = c["alone"], c["with_previous"], c["pair"]
        ok_a = a["jev_pref"] == p["is_preference"]
        ok_w = w["jev_pref"] == p["is_preference"]
        body.append([f"`{p['id']}`", _label(p), f"{a['guess'].p:.2f} / {a['guess'].direction} / {a['guess'].target_key or 'no_match'}"
                     + ("" if ok_a else " *"),
                     f"{w['guess'].p:.2f} / {w['guess'].direction} / {w['guess'].target_key or 'no_match'}" + ("" if ok_w else " *")])
    o.append(_table(["id", "label", "alone (p / direction / target)", "with previous turn"], body))
    prefs_c = [c for c in ctx_rows if c["pair"]["is_preference"]]
    def right_dir(r, p):
        return r["guess"].direction == p["direction"]
    o.append(f"\n`*` marks a standing-preference call that disagrees with the label (p >= {JEV_YES}). On the "
             f"{len(prefs_c)} labelled preferences: standing called right {sum(c['alone']['jev_pref'] for c in prefs_c)}/{len(prefs_c)} alone, "
             f"{sum(c['with_previous']['jev_pref'] for c in prefs_c)}/{len(prefs_c)} with the previous turn; direction right "
             f"{sum(right_dir(c['alone'], c['pair']) for c in prefs_c)}/{len(prefs_c)} alone, "
             f"{sum(right_dir(c['with_previous'], c['pair']) for c in prefs_c)}/{len(prefs_c)} with it; target right "
             f"{sum(c['alone']['guess'].target_key == c['pair']['target'] for c in prefs_c)}/{len(prefs_c)} alone, "
             f"{sum(c['with_previous']['guess'].target_key == c['pair']['target'] for c in prefs_c)}/{len(prefs_c)} with it.\n")

    # label vs jev
    dis = disagreements(rows)
    o.append("## Disagreements between the label and Jev\n")
    o.append(f"{len(dis)} of {n} messages differ on at least one of: standing (p >= {JEV_YES}), direction, strength (more "
             "than one level), target.\n")
    if dis:
        o.append(_table(["id", "category", "differs", "label", "Jev", "route"],
                        [[_c(r), r["category"], ", ".join(r["differs"]), _label(r), _guess(r), _outcome(r, rec)] for r in dis]))
    o.append("")

    o.append("## Every message\n")
    o.append(_table(["id", "category", "candidate", "negated", "label", "Jev", "route"],
                    [[_c(r), r["category"], "yes" if r["pre"].candidate else "no", "yes" if r["pre"].negated else "",
                      _label(r), _guess(r), _outcome(r, rec)] for r in rows]))
    return "\n".join(o)


def render_review(rows, ctx_rows) -> str:
    rec = recommend(rows)
    dis = disagreements(rows)
    ids = {r["id"] for r in dis}
    agree = [r for r in rows if r["id"] not in ids]

    def block(i: int, r) -> str:
        pre = r["pre"]
        lines = [f"**{i}. `{r['id']}`** ({r['category']})",
                 f"- Message: \"{r['message']}\"",
                 f"- Label: {_label(r)}. {r['rationale']}",
                 f"- Jev: {_guess(r)}" + (f"; differs on {', '.join(r['differs'])}" if r.get("differs") else ""),
                 f"- Prefilter: " + (("candidate" + (", negated" if pre.negated else "") + f", cues {list(pre.cues)}")
                                     if pre.candidate else "not a candidate (never asked in production)"),
                 f"- The gate: {_outcome(r, rec)}", ""]
        return "\n".join(lines)

    o = ["# Memory-gate labels for review (issue #202)\n",
         "Each message is a synthetic user message and its user-confirmed label: whether it states a **lasting preference** about the "
         "resume (global, or for one named job), its **direction** (`emphasize`: feature or keep something; `suppress`: leave "
         "something out; `format_rule`: how the resume is written or laid out; none), its **strength** on #129's 1-5 scale (5 only "
         "for an absolute rule such as \"never\"), its **target** (a catalog item, or no item for a topic or a general rule) and "
         "whether it is **job-scoped**. A one-off edit, a question, a fact about the user's experience and small talk are not "
         "preferences. **These are the labels the user confirmed (every proposal kept as is), kept for audit.** To change one, correct it in "
         "`pairs.json` and re-run `python eval/fit_memory_gate_threshold.py analyze` to refit. Disagreements with Jev come first.\n",
         f"## Disagreements with Jev ({len(dis)})\n"]
    n = 0
    for r in sorted(dis, key=lambda r: (CATEGORY_ORDER.index(r["category"]), r["id"])):
        n += 1
        o.append(block(n, r))
    o.append(f"## Agreements ({len(agree)})\n")
    for r in agree:
        n += 1
        o.append(block(n, r))
    o.append(f"## Context messages ({len(ctx_rows)})\n")
    o.append("Asked alone and with the previous assistant turn; the label assumes the turn is known.\n")
    for c in ctx_rows:
        n += 1
        p = c["pair"]
        o.append("\n".join([f"**{n}. `{p['id']}`** (context)", f"- Previous assistant turn: \"{p['previous']}\"",
                            f"- Message: \"{p['message']}\"", f"- Label: {_label(p)}. {p['rationale']}",
                            f"- Jev alone: {_guess(c['alone'])}", f"- Jev with the turn: {_guess(c['with_previous'])}", ""]))
    return "\n".join(o)


# ── commands ─────────────────────────────────────────────────────────────────

def _meta(doc: Dict[str, Any], hit_rate: Optional[float], catalog) -> Dict[str, Any]:
    from harness.decisions.memory_gate import VERSION
    decisions = doc.get("decisions") or []
    return {"recorded": (doc.get("exported_at") or "n/a")[:10],
            "models": sorted({d.get("resolved_model") for d in decisions if d.get("resolved_model")}),
            "question": VERSION, "hit_rate": "n/a" if hit_rate is None else f"{hit_rate:.0%}",
            "profile": load_doc()["profile"], "catalog": len(catalog)}


def cmd_record(args) -> int:
    doc = load_doc(Path(args.pairs))
    pairs, ctx = doc["pairs"], doc.get("context_pairs", [])
    if args.limit:
        pairs, ctx = pairs[: args.limit], ctx[: max(0, args.limit - len(pairs))]
    _pin_store(Path(args.data_dir))
    os.environ["ART_JEV_MODE"] = "auto"
    from harness.decisions import engine, memory_gate, recordings
    if engine.get_client() is None:
        print("no TYPESAFE_API_KEY in the environment or .env; nothing was recorded", file=sys.stderr)
        return 2
    catalog = profile_catalog(doc["profile"])
    engine.reset_stats()
    failures = []
    jobs = [(p, None) for p in pairs] + [(p, None) for p in ctx] + [(p, p["previous"]) for p in ctx]
    for i, (pair, previous) in enumerate(jobs, 1):
        answers = memory_gate.ask(pair["message"], catalog, previous)
        bad = next((a for a in answers if a.fell_back), None)
        if bad is not None:
            failures.append(f"{pair['id']}: {bad.reason}")
            print(f"[{i}/{len(jobs)}] FAILED {pair['id']}: {bad.reason}", flush=True)
            if len(failures) >= 3 and not args.keep_going:
                print("three failures in a row; stopping (use --keep-going to continue)", file=sys.stderr)
                break
            continue
        g = memory_gate.parse_answers(answers, catalog, pair["message"])
        print(f"[{i}/{len(jobs)}] {pair['id']}{' (+prev)' if previous else ''}: p={g.p:.2f} {g.direction} "
              f"{g.target_key or 'no_match'} s{g.strength} via {g.source}", flush=True)
    st = engine.stats().get(memory_gate.POINT, {})
    print(json.dumps({"messages": len(jobs), "failed": len(failures), "live_answers": st.get("jev", 0),
                      "cache_answers": st.get("cache", 0), "requests": st.get("requests", 0),
                      "input_tokens": round(st.get("input_tokens", 0)), "output_tokens": round(st.get("output_tokens", 0))}))
    if failures:
        print("re-run `record` to retry only the failed messages (answered ones are cached)", file=sys.stderr)
        return 1
    out = recordings.export_recordings()
    Path(args.recordings).write_text(json.dumps(out, sort_keys=True, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {len(out['decisions'])} recordings to {args.recordings}")
    return 0


def replay_rows(doc: Dict[str, Any]):
    """Replay every message from the cache, in `replay` mode (a miss raises `JevReplayMiss`). The store
    must already hold the recordings. Returns `(rows, context_rows, catalog, stats)`."""
    from harness.decisions import engine, memory_gate

    engine.reset_stats()
    catalog = profile_catalog(doc["profile"])
    rows = run_pairs(doc["pairs"], catalog)
    ctx = run_context(doc.get("context_pairs", []), catalog)
    return rows, ctx, catalog, engine.stats()[memory_gate.POINT]


def replay_all(doc: Dict[str, Any], recordings_doc: Dict[str, Any]):
    """Import the recordings into a fresh private store and replay every message, offline."""
    _pin_store(Path(tempfile.mkdtemp(prefix="art_memory_gate_analyze_")))
    os.environ["ART_JEV_MODE"] = "replay"
    os.environ.pop("TYPESAFE_API_KEY", None)                # replay never calls the API; make it impossible
    from harness.decisions import recordings
    recordings.import_recordings(recordings_doc)
    return replay_rows(doc)


def cmd_analyze(args) -> int:
    doc = load_doc(Path(args.pairs))
    recorded = json.loads(Path(args.recordings).read_text(encoding="utf-8"))
    from harness.decisions.client import JevReplayMiss
    try:
        rows, ctx, catalog, st = replay_all(doc, recorded)
    except JevReplayMiss as exc:
        print(f"replay missed: {exc}", file=sys.stderr)
        return 2
    if st["hit_rate"] != 1.0 or st["jev"] or st["fallback"]:
        print(f"replay was not a 100% cache hit: {st}", file=sys.stderr)
        return 2
    Path(args.report).write_text(render_report(rows, ctx, _meta(recorded, st["hit_rate"], catalog)) + "\n", encoding="utf-8")
    Path(args.review).write_text(render_review(rows, ctx) + "\n", encoding="utf-8")
    rec = recommend(rows)
    print(f"replayed {len(rows)} messages and {len(ctx)} context messages, hit rate {st['hit_rate']:.0%}; recommended "
          f"TAU_LO {rec['tau_lo']:.2f}, TAU_HI {rec['tau_hi']:.2f}, TAU_TARGET {rec['tau_target']:.2f}; "
          f"wrote {args.report} and {args.review}")
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
            s.add_argument("--limit", type=int, default=0, help="Only the first N messages (a smoke test).")
            s.add_argument("--keep-going", action="store_true", help="Do not stop after three failures.")
        else:
            s.add_argument("--report", default=str(REPORT_PATH))
            s.add_argument("--review", default=str(REVIEW_PATH))
    args = p.parse_args(list(sys.argv[1:] if argv is None else argv))
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
