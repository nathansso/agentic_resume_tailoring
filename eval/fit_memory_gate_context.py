"""Fit `memory_gate@v2`'s thresholds and compare it with v1 on the context set (issue #244).

`memory_gate@v1` reads a message alone. A message such as "never list that again" depends on the assistant
turn before it, so `memory_gate@v2` asks the same four questions with that turn in the state (and says it is
only there to resolve what the message refers to). The v2 thresholds are fitted separately, on v2's own answers
to `eval/memory_gate_labels/context/pairs.json`: 40 synthetic messages, each with a synthetic previous
assistant turn (12 of them #202's context messages, 28 new). The rules are #202's, unchanged
(`fit_memory_gate_threshold.py`): TAU_TARGET: no wrong binding, then the most right ones; TAU_HI: no wrong
automatic write, then the most writes, then the middle of the gap, from the 0.5 floor and **as if the code
rules did not exist**; TAU_LO: no true preference dropped, then the fewest hand-offs. **Then the pin rule**
(`recommend`): while the context set's own would-be write set is thinner than v1's (`V1_WOULD_BE_WRITES`),
TAU_HI_V2 is `max(fit, TAU_HI)` and TAU_TARGET_V2 is `max(fit, TAU_TARGET)`, and TAU_LO is fitted below the
pinned TAU_HI_V2: a variant fitted on a handful of would-be writes is never looser than v1's evidence.

    python eval/fit_memory_gate_context.py record     # live Jev, once, needs TYPESAFE_API_KEY
    python eval/fit_memory_gate_context.py analyze    # offline: replay the recordings, write the report

`record` runs every pair through ART's own engine (`memory_gate.ask`) in `auto` mode against a **private**
SQLite store, never the user's database, **without the prefilter**: each message alone (v1) and with its
turn (v2). #202's recordings are imported first, so the 12 messages already asked alone, and any message
asked twice, cost nothing. It exports only the new answers to `context/recordings.json`. `analyze` imports
both files into a fresh store and replays in `replay` mode: a miss raises, and the run refuses to report
unless the hit rate is 100%. It writes `context/REPORT.md` and `context/REVIEW.md`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from eval import fit_memory_gate_threshold as base  # noqa: E402
from eval.fit_support_threshold import _pct, _pin_store, _table  # noqa: E402

CONTEXT_DIR = base.LABEL_DIR / "context"
PAIRS_PATH = CONTEXT_DIR / "pairs.json"
RECORDINGS_PATH = CONTEXT_DIR / "recordings.json"
REPORT_PATH = CONTEXT_DIR / "REPORT.md"
REVIEW_PATH = CONTEXT_DIR / "REVIEW.md"
DEFAULT_RECORD_DIR = Path(tempfile.gettempdir()) / "art_memory_gate_context_record"
MAX_QUESTIONS = 260                  # the recording's budget: stop before asking more than this live
CATEGORY_ORDER = ("referent_item", "referent_format", "one_off", "negated_referent", "misleading_previous",
                  "irrelevant_previous", "short_answer")


def load_doc(path: Path = PAIRS_PATH) -> Dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _mg():
    from harness.decisions import memory_gate
    return memory_gate


# ── running the pairs ────────────────────────────────────────────────────────

def ask_v1(pair, catalog):
    """The message alone: what the gate asks when it has no previous turn."""
    return _mg().ask(pair["message"], catalog)


def ask_v2(pair, catalog):
    return _mg().ask(pair["message"], catalog, pair["previous"])


def run_pairs(pairs: Sequence[Dict[str, Any]], catalog) -> List[Dict[str, Any]]:
    """Each pair twice: `v1` (the message alone) and `v2` (with its previous turn), plus whether detection
    would send the turn (`cues`). `shipped` is what the gate does: v2 when a reference is detected, else v1."""
    mg = _mg()
    rows = []
    for pair in pairs:
        v1 = base.make_row(pair, catalog, ask_v1(pair, catalog))
        v2 = base.make_row(pair, catalog, ask_v2(pair, catalog), pair["previous"], mg.VERSION_V2)
        cues = mg.unresolved_reference(pair["message"], catalog)
        rows.append({"pair": pair, "v1": v1, "v2": v2, "cues": cues, "shipped": v2 if cues else v1})
    return rows


def replay_all(doc: Dict[str, Any], recorded: Dict[str, Any], parent: Dict[str, Any]):
    """Import both recordings into a fresh private store and replay every pair, offline."""
    _pin_store(Path(tempfile.mkdtemp(prefix="art_memory_gate_context_analyze_")))
    os.environ["ART_JEV_MODE"] = "replay"
    os.environ.pop("TYPESAFE_API_KEY", None)                # replay never calls the API; make it impossible
    from harness.decisions import recordings
    recordings.import_recordings(parent)
    recordings.import_recordings(recorded)
    return replay_rows(doc)


def replay_rows(doc: Dict[str, Any]):
    """Replay from a store that already holds the recordings (a miss raises). `(rows, catalog, stats)`."""
    from harness.decisions import engine, memory_gate

    engine.reset_stats()
    catalog = base.profile_catalog(doc["profile"])
    rows = run_pairs(doc["pairs"], catalog)
    return rows, catalog, engine.stats()[memory_gate.POINT]


# ── the numbers ──────────────────────────────────────────────────────────────

def v2_rows(rows) -> List[Dict[str, Any]]:
    return [r["v2"] for r in rows]


def v1_rows(rows) -> List[Dict[str, Any]]:
    return [r["v1"] for r in rows]


def shipped_rows(rows) -> List[Dict[str, Any]]:
    return [r["shipped"] for r in rows]


def accuracy(rows) -> Dict[str, Any]:
    """Standing, direction, target and strength against the labels, and the gate's routing at the version's
    own thresholds (the guess carries its version)."""
    prefs = [r for r in rows if r["is_preference"]]
    decisions = [(r, base.route_row(r)) for r in rows]
    writes = [(r, d) for r, d in decisions if d["action"] == "write"]
    return {
        "n": len(rows), "prefs": len(prefs),
        "standing": sum(r["jev_pref"] == r["is_preference"] for r in rows),
        "recall": sum(r["jev_pref"] for r in prefs),
        "false_yes": sum(r["jev_pref"] and not r["is_preference"] for r in rows),
        "direction": sum(r["guess"].direction == r["direction"] for r in prefs),
        "target": sum(r["guess"].target_key == r["target"] for r in prefs),
        "strength1": sum(abs(r["guess"].strength - r["strength"]) <= 1 for r in prefs),
        "writes": len(writes), "wrong": sum(base.write_is_wrong(r, d) for r, d in writes),
        "write_ids": [r["id"] for r, _ in writes],
        "wrong_ids": [r["id"] for r, d in writes if base.write_is_wrong(r, d)],
        "host": sum(d["action"] == "host" for _, d in decisions),
        "drop": sum(d["action"] == "drop" for _, d in decisions),
        "kept_prefs": sum(base.route_row(r)["action"] != "drop" for r in prefs),
    }


def detection(rows, main_pairs, catalog) -> Dict[str, Any]:
    """Which context pairs the detector sends a turn for, and what it flags among the main set's 111
    self-contained messages (a flag there is a false detection)."""
    mg = _mg()
    flagged = [r for r in rows if r["cues"]]
    missed = [r for r in rows if not r["cues"]]
    main = [p for p in main_pairs if mg.unresolved_reference(p["message"], catalog)]
    main_cand = [p for p in main if mg.prefilter(p["message"]).candidate]
    return {"flagged": len(flagged), "missed": [r["pair"]["id"] for r in missed], "n": len(rows),
            "main_flagged": len(main), "main_n": len(main_pairs), "main_candidates": len(main_cand),
            "main_flagged_ids": [p["id"] for p in main_cand]}


def recommend(rows) -> Dict[str, Any]:
    """The v2 thresholds: #202's rules on v2's own answers, then the pin rule. A context variant's write
    thresholds are never looser than v1's evidence-backed ones while its own would-be write set (the writes
    the fit sees with the code rules off) is thinner than v1's (`V1_WOULD_BE_WRITES`): `TAU_HI_V2` is
    `max(fit, TAU_HI)` and `TAU_TARGET_V2` is `max(fit, TAU_TARGET)`. `TAU_LO` is fitted again below the pinned
    `TAU_HI_V2`. `raw` keeps the unpinned fit, `pinned` says whether the rule applied."""
    mg = _mg()
    rec = base.recommend(v2_rows(rows))
    raw = {"tau_hi": rec["tau_hi"], "tau_target": rec["tau_target"], "tau_lo": rec["tau_lo"]}
    pinned = rec["hi"]["autos"] < mg.V1_WOULD_BE_WRITES
    if pinned:
        rec["tau_hi"] = max(rec["tau_hi"], mg.TAU_HI)
        rec["tau_target"] = max(rec["tau_target"], mg.TAU_TARGET)
        rec["lo"] = base.fit_lo(v2_rows(rows), rec["tau_hi"])
        rec["tau_lo"] = rec["lo"]["tau_lo"]
    rec["raw"], rec["pinned"] = raw, pinned
    return rec


# ── rendering ────────────────────────────────────────────────────────────────

def _guess(r) -> str:
    g = r["guess"]
    return f"p {g.p:.2f}; {g.direction} {g.target_key or 'no_match'} (p {g.target_p:.2f}); s{g.strength}"


def _route(r) -> str:
    d = base.route_row(r)
    s = d["action"] if d["action"] != "host" else f"host ({d['reason']})"
    return s + (" WRONG" if base.write_is_wrong(r, d) else "")


def _disagreements(rows) -> List[Dict[str, Any]]:
    return base.disagreements(rows)


def render_report(rows, main_pairs, catalog, meta: Dict[str, Any]) -> str:
    mg = _mg()
    v1, v2, sh = v1_rows(rows), v2_rows(rows), shipped_rows(rows)
    rec = recommend(rows)
    cur = mg.thresholds(mg.VERSION_V2)
    fitted = (rec["tau_lo"], rec["tau_hi"], rec["tau_target"])
    tg, hi, lo = rec["target"], rec["hi"], rec["lo"]
    det = detection(rows, main_pairs, catalog)
    n = len(rows)
    prefs = [r for r in v2 if r["is_preference"]]
    new = [r for r in rows if r["pair"]["origin"] == "244"]
    old = [r for r in rows if r["pair"]["origin"] == "202"]

    o: List[str] = []
    o.append("# Memory gate v2: the previous assistant turn (issue #244)\n")
    o.append("Generated by `python eval/fit_memory_gate_context.py analyze` from `pairs.json`, `recordings.json` and "
             "#202's `../recordings.json`. **All 40 labels are user-confirmed**: the 12 messages with origin `202` "
             "in #202, the 28 with origin `244` from `REVIEW.md` (every proposal kept as is). "
             "Correct a label in `pairs.json` and re-run `analyze` to refit.\n")
    o.append(f"- Recorded {meta.get('recorded', 'n/a')} with model {', '.join(meta.get('models') or ['n/a'])}: "
             f"`{mg.VERSION}` (the message alone) and `{mg.VERSION_V2}` (with the previous turn) for {n} messages "
             f"({len(old)} from #202, {len(new)} new), replayed at {meta.get('hit_rate', 'n/a')} cache hit rate. Every "
             "message was asked of Jev, prefilter or not.")
    o.append(f"- {len(prefs)} labelled standing preferences, {n - len(prefs)} not. Catalog: `{meta.get('profile')}`'s "
             f"({meta.get('catalog')} items and `no_match`).")
    o.append(f"- v2 thresholds now in `memory_gate.py`: `TAU_LO_V2` {cur[0]}, `TAU_HI_V2` {cur[1]}, `TAU_TARGET_V2` "
             f"{cur[2]} ({'they match the fit below' if tuple(cur) == fitted else 'they differ from the fit below'}). "
             f"v1's stay `TAU_LO` {mg.TAU_LO}, `TAU_HI` {mg.TAU_HI}, `TAU_TARGET` {mg.TAU_TARGET}.\n")

    # v1 against v2
    o.append("## v1 against v2\n")
    o.append("The same messages, asked alone (`memory_gate@v1`) and with the previous assistant turn "
             "(`memory_gate@v2`). Standing is Jev's yes-probability at 0.5 against the label, over every message; "
             "direction, target and strength are over the labelled preferences. `as shipped` is v2 where the detector "
             "finds an unresolved reference and v1 elsewhere. Each is routed at its own version's thresholds.\n")
    cols = [("v1 alone", v1), ("v2 with the turn", v2), ("as shipped", sh)]
    stats = [accuracy(rs) for _, rs in cols]
    body = []

    def line(label, fn):
        body.append([label] + [fn(s) for s in stats])
    line("standing right (of all)", lambda s: f"{s['standing']}/{s['n']}")
    line("preferences called (recall)", lambda s: f"{s['recall']}/{s['prefs']}")
    line("non-preferences called preferences", lambda s: f"{s['false_yes']}/{s['n'] - s['prefs']}")
    line("direction right", lambda s: f"{s['direction']}/{s['prefs']}")
    line("target right (an item, or no_match when none)", lambda s: f"{s['target']}/{s['prefs']}")
    line("strength within one level", lambda s: f"{s['strength1']}/{s['prefs']}")
    line("true preferences kept (host or write)", lambda s: f"{s['kept_prefs']}/{s['prefs']}")
    line("**automatic writes**", lambda s: str(s["writes"]))
    line("**wrong automatic writes**", lambda s: str(s["wrong"]))
    line("handed to the host", lambda s: str(s["host"]))
    o.append(_table(["measure"] + [c for c, _ in cols], body))
    o.append("")
    s1, s2 = stats[0], stats[1]
    o.append(f"On #202's original 12 ({sum(r['pair']['is_preference'] for r in old)} preferences) the figures are "
             f"standing {accuracy([r['v1'] for r in old])['standing']}/12 alone and "
             f"{accuracy([r['v2'] for r in old])['standing']}/12 with the turn, direction "
             f"{accuracy([r['v1'] for r in old])['direction']} against {accuracy([r['v2'] for r in old])['direction']} "
             f"of {accuracy([r['v1'] for r in old])['prefs']}, target "
             f"{accuracy([r['v1'] for r in old])['target']} against {accuracy([r['v2'] for r in old])['target']}: "
             "#202 measured the same messages with the v1 questions over a different state.\n")
    o.append(f"Automatic writes: v1 {s1['writes']} ({', '.join('`'+i+'`' for i in s1['write_ids']) or 'none'}), "
             f"v2 {s2['writes']} ({', '.join('`'+i+'`' for i in s2['write_ids']) or 'none'}); wrong: v1 {s1['wrong']}, "
             f"v2 {s2['wrong']}. A write needs the message itself to name the item (the name check): \"never list "
             "that again\" never names it, so a message that depends on the turn for its target goes to the host "
             "with v2's guess of it, as it does under v1. **So `no wrong write` holds here almost by construction**: "
             f"without the code rules v2 would write {rec['hi']['autos']} ({rec['hi']['wrong']} wrong), and with them "
             "none, because the messages that name their item are all strength 5 (never written) and the rest leave "
             "the item to the turn. The set cannot show that v2 is safe to write from; the code rules do that. A "
             "set of messages that name an item and still lean on the turn at strength 4 or less would.\n")

    # by category
    o.append("## By category\n")
    body = []
    for c in CATEGORY_ORDER:
        rs = [r for r in rows if r["pair"]["category"] == c]
        if not rs:
            continue
        a, b = accuracy([r["v1"] for r in rs]), accuracy([r["v2"] for r in rs])
        body.append([c, len(rs), f"{a['standing']} / {b['standing']}",
                     f"{a['direction']} / {b['direction']} of {a['prefs']}",
                     f"{a['target']} / {b['target']} of {a['prefs']}",
                     f"{a['writes']} / {b['writes']}", f"{a['wrong']} / {b['wrong']}"])
    o.append(_table(["category", "n", "standing v1 / v2", "direction v1 / v2", "target v1 / v2",
                     "writes v1 / v2", "wrong writes v1 / v2"], body))
    o.append("")

    # detection
    o.append("## Detection: when the turn is sent\n")
    o.append("`unresolved_reference` (an anaphoric phrase, a bare pronoun when the message names no catalog item, "
             "or a short yes/no reply) decides whether the previous turn is looked for at all. It was written from the "
             "design's list and checked against the main set's 111 self-contained messages. The ordinal referents (\"the last "
             "one\") were added after the first recording showed three misses (`cx_mis_last`, `cx_mis_first`, "
             "`cx_mis_ranked`), so recall on this set is not an out-of-sample figure.\n")
    o.append(f"- The context set: detected for {det['flagged']} of {det['n']}"
             + (f"; **not detected** (so these run v1 in production): {', '.join('`'+i+'`' for i in det['missed'])}"
                if det["missed"] else "") + ".")
    o.append(f"- The main set (self-contained messages, so a flag is a false detection): {det['main_flagged']} of "
             f"{det['main_n']} flagged, {det['main_candidates']} of them candidates the gate would ask about"
             + (f" ({', '.join('`'+i+'`' for i in det['main_flagged_ids'])})" if det["main_flagged_ids"] else "")
             + ". A false detection only costs a v2 question in place of a v1 one, and only when the host has a "
             "transcript; the questions are the same.\n")

    # thresholds
    o.append("## The v2 thresholds\n")
    o.append(f"**TAU_LO_V2 = {rec['tau_lo']:.2f}, TAU_HI_V2 = {rec['tau_hi']:.2f}, TAU_TARGET_V2 = {rec['tau_target']:.2f}.** "
             "The rules are #202's: each over the grid, in priority order, each then the grid value closest to the middle "
             "of its gap (ties to the higher), **as if the code rules did not exist** (strength 5 and `HARD_MASS`, the "
             "section rule, the negation backstop), with a 0.5 floor on `TAU_HI`.\n")
    raw = rec["raw"]
    o.append("**The pin rule.** A write threshold of the context variant is never looser than v1's evidence-backed one while "
             "the variant's own would-be write set is thinner than v1's: `TAU_HI_V2 = max(fit, TAU_HI)` and `TAU_TARGET_V2 = "
             f"max(fit, TAU_TARGET)` unless the set holds at least {mg.V1_WOULD_BE_WRITES} would-be writes (v1's own, with the "
             f"code rules off). Here it holds {hi['autos']}, so "
             + (f"the rule applies: the fit alone gives `TAU_HI` {raw['tau_hi']:.2f} and `TAU_TARGET` {raw['tau_target']:.2f}, "
                f"which become {rec['tau_hi']:.2f} and {rec['tau_target']:.2f}. A threshold fitted on {hi['autos']} would-be "
                "writes sits where the rule leaves it (the floor, or below any evidence), which is not evidence that a looser "
                "cut is safe. `TAU_LO` is not a write threshold: it is fitted again below the pinned `TAU_HI_V2` "
                f"(the fit alone gave {raw['tau_lo']:.2f}).\n" if rec["pinned"] else "it does not apply.\n"))
    o.append(f"- **TAU_TARGET_V2 {rec['tau_target']:.2f}** (fit alone {tg['tau_target']:.2f}): v2 bound {tg['every']} true preferences to a catalog item; the "
             f"message names the item in {tg['binds']} of them ({tg['right']} right, {tg['wrong']} wrong"
             + (f": {', '.join('`'+i+'`' for i in tg['wrong_ids'])}, the highest scoring {tg['top_wrong']:.2f}" if tg["wrong"] else "")
             + f"). The name check removes {len(tg['unnamed_ids'])} bindings the message does not name (the referent is in "
             f"the previous turn, not the message), {len(tg['unnamed_wrong_ids'])} of them wrong"
             + (f" ({', '.join('`'+i+'`' for i in tg['unnamed_wrong_ids'])})" if tg["unnamed_wrong_ids"] else "")
             + f". Candidates {', '.join(f'{c:.2f}' for c in tg['candidates'])}"
             + (f"; the lowest right binding kept scores {tg['lowest_kept']:.2f}" if tg["lowest_kept"] is not None else "") + ".")
    o.append(f"- **TAU_HI_V2 {rec['tau_hi']:.2f}** (fit alone {hi['tau_hi']:.2f}): without the code rules there are {hi['autos']} "
             f"writes and {hi['wrong']} wrong at {hi['tau_hi']:.2f}; the wrong writes the rules would otherwise leave: "
             f"{', '.join('`'+i+'`' for i in hi['danger_ids']) or 'none'}"
             + (f" (the highest scoring one under the candidates `{hi['danger_id']}` at {hi['danger_max']:.2f})"
                if hi["danger_id"] else "")
             + (f"; the lowest-scoring right write {hi['kept_min']:.2f}" if hi["kept_min"] is not None else "; no right write at all")
             + f". Candidates with the fewest wrong and the most writes: {', '.join(f'{c:.2f}' for c in hi['candidates'])}.")
    reach = [r for r in v2 if base.reachable(r)]
    o.append(f"- **TAU_LO_V2 {lo['tau_lo']:.2f}**: of the {len([r for r in reach if r['is_preference']])} true preferences "
             f"v2 is asked about, {lo['dropped_prefs']} are dropped; {lo['handoffs']} non-preferences are handed to the host "
             "between TAU_LO and TAU_HI."
             + (f" The lowest-scoring true preference is `{lo['lowest_pref']['id']}` at {lo['lowest_pref']['guess'].p:.2f}."
                if lo["lowest_pref"] else ""))
    o.append("\nThe evidence is thin: the context set is small, and a write also needs the message to name the item, which "
             "a message that leans on the previous turn rarely does. The thresholds are cutoffs on this model's scores on "
             "this set, not probabilities. Refit when the model or a question changes, and with more messages.\n")
    o.append("TAU_HI grid (TAU_TARGET fixed): automatic writes and wrong ones without the code rules (what the fit chooses "
             "on) and with them (what the gate does):\n")
    on = {t: (sum(base.write_is_wrong(r, base.route_row(r, rec["tau_lo"], t, rec["tau_target"])) for r in v2),
              sum(base.route_row(r, rec["tau_lo"], t, rec["tau_target"])["action"] == "write" for r in v2))
          for t in hi["table"]}
    o.append(_table(["TAU_HI", "writes (rules off)", "wrong (rules off)", "writes (rules on)", "wrong (rules on)"],
                    [[f"{t:.2f}", a, w, on[t][1], on[t][0]] for t, (w, a) in hi["table"].items()]))
    o.append("")
    o.append("TAU_LO grid (TAU_HI fixed): true preferences dropped, non-preferences handed to the host:\n")
    o.append(_table(["TAU_LO", "preferences dropped", "non-preferences handed to the host"],
                    [[f"{t:.2f}", d, h] for t, (d, h) in lo["table"].items()]))
    o.append("")
    # v2 answers routed at v1's thresholds, to show whether a separate fit mattered
    at_v1 = [(r, base.route_row(r, mg.TAU_LO, mg.TAU_HI, mg.TAU_TARGET)) for r in v2]
    w1 = [(r, d) for r, d in at_v1 if d["action"] == "write"]
    o.append(f"For comparison, v2's answers at v1's thresholds ({mg.TAU_LO}, {mg.TAU_HI}, {mg.TAU_TARGET}) write {len(w1)} "
             f"({sum(base.write_is_wrong(r, d) for r, d in w1)} wrong) and drop "
             f"{sum(d['action'] == 'drop' for _, d in at_v1)} messages.\n")
    if rec["pinned"]:
        a_fit = {r["id"]: base.route_row(r, raw["tau_lo"], raw["tau_hi"], raw["tau_target"]) for r in v2}
        a_pin = {r["id"]: base.route_row(r, rec["tau_lo"], rec["tau_hi"], rec["tau_target"]) for r in v2}
        diff = [i for i in a_fit if a_fit[i]["action"] != a_pin[i]["action"]]
        why = [i for i in a_fit if a_fit[i]["reason"] != a_pin[i]["reason"]]
        o.append(f"The pin changes no routing on this set: at the fit alone ({raw['tau_lo']:.2f}, {raw['tau_hi']:.2f}, "
                 f"{raw['tau_target']:.2f}) and at the pinned values every message gets the same action (drop, host or write; "
                 f"{len(diff)} differ), and both write nothing. The reason a host hand-off carries differs for {len(why)} "
                 "messages scored between the two `TAU_HI` values (`uncertain` under the pin, a rule's name under the fit"
                 + (f": {', '.join('`'+i+'`' for i in why)}" if why else "") + ").\n")

    # disagreements
    dis2, dis1 = _disagreements(v2), _disagreements(v1)
    ids2 = {r["id"] for r in dis2}
    o.append("## Disagreements between the label and v2\n")
    o.append(f"{len(dis2)} of {n} messages differ on standing (p >= {base.JEV_YES}), direction, strength (more than one "
             f"level) or target: " + (", ".join(f"`{i}`" for i in sorted(ids2)) or "none") + ". "
             f"v1 alone differs on {len(dis1)}: " + (", ".join(f"`{r['id']}`" for r in dis1) or "none") + ".\n")
    if dis2:
        o.append(_table(["id", "category", "differs", "label", "v2", "v1", "route (v2)"],
                        [[f"`{r['id']}`", r["category"], ", ".join(r["differs"]), base._label(r), _guess(r),
                          _guess(next(x for x in v1 if x["id"] == r["id"])), _route(r)] for r in dis2]))
    o.append("")
    flipped = [(a, b) for a, b in zip(v1, v2) if (a["jev_pref"] != b["jev_pref"] or a["guess"].direction != b["guess"].direction
                                                  or a["guess"].target_key != b["guess"].target_key)]
    o.append("## Where the turn changed the answer\n")
    o.append(f"{len(flipped)} of {n} messages get a different standing call, direction or target with the turn. "
             "`ok` means that field agrees with the label.\n")

    def ok(row, field):
        if not row["is_preference"]:
            return "ok" if (not row["jev_pref"] if field == "standing" else True) else "wrong"
        return "ok" if (row["jev_pref"] if field == "standing" else
                        row["guess"].direction == row["direction"] if field == "direction" else
                        row["guess"].target_key == row["target"]) else "wrong"
    if flipped:
        o.append(_table(["id", "label", "v1 (standing / direction / target)", "v2"],
                        [[f"`{b['id']}`", base._label(b),
                          f"{a['guess'].p:.2f} {ok(a, 'standing')} / {a['guess'].direction} {ok(a, 'direction')} / "
                          f"{a['guess'].target_key or 'no_match'} {ok(a, 'target')}",
                          f"{b['guess'].p:.2f} {ok(b, 'standing')} / {b['guess'].direction} {ok(b, 'direction')} / "
                          f"{b['guess'].target_key or 'no_match'} {ok(b, 'target')}"] for a, b in flipped]))
    o.append("")

    o.append("## Every message\n")
    o.append(_table(["id", "category", "turn sent", "label", "v1", "v2", "route v1", "route v2"],
                    [[f"`{r['pair']['id']}`", r["pair"]["category"], "yes" if r["cues"] else "no", base._label(r["v2"]),
                      _guess(r["v1"]), _guess(r["v2"]), _route(r["v1"]), _route(r["v2"])] for r in rows]))
    return "\n".join(o)


def render_review(rows) -> str:
    v1 = {r["id"]: r for r in v1_rows(rows)}
    dis = _disagreements(v2_rows(rows))
    ids = {r["id"] for r in dis}
    by_id = {r["pair"]["id"]: r for r in rows}

    def block(i: int, r) -> str:
        p, a, b = r["pair"], r["v1"], r["v2"]
        lines = [f"**{i}. `{p['id']}`** ({p['category']}; label {'confirmed in #202' if p['origin'] == '202' else 'confirmed in review of this issue'}, user-confirmed)",
                 f"- Previous assistant turn: \"{p['previous']}\"",
                 f"- Message: \"{p['message']}\"",
                 f"- Label: {base._label(b)}. {p['rationale']}",
                 f"- v2 (with the turn): {_guess(b)}" + (f"; differs on {', '.join(b['differs'])}" if b.get("differs") else ""),
                 f"- v1 (alone): {_guess(a)}",
                 f"- The turn is sent in production: " + ("yes (" + ", ".join(r["cues"]) + ")" if r["cues"] else "no (no unresolved reference found)"),
                 f"- The gate with v2: {_route(b)}", ""]
        return "\n".join(lines)

    o = ["# Memory-gate context labels for review (issue #244)\n",
         "Each message is a synthetic user message with the synthetic assistant turn it answers, and its label: whether it "
         "states a **lasting preference** about the resume, its **direction** (`emphasize`, `suppress`, `format_rule`, none), "
         "its **strength** on #129's 1-5 scale (5 only for an absolute rule such as \"never\"), its **target** (a catalog item, or "
         "no item) and whether it is **job-scoped**. **The label assumes the previous turn is known.** The 12 messages from "
         "#202 keep the labels you confirmed there, and **you confirmed the 28 new ones as proposed (every proposal kept as is)**; "
         "all 40 are user-confirmed, kept for audit. To "
         "change a label, correct it in `pairs.json` and re-run `python eval/fit_memory_gate_context.py analyze`. "
         "Disagreements with v2 come first.\n",
         f"## Disagreements with v2 ({len(dis)})\n"]
    n = 0
    for r in sorted(dis, key=lambda r: (CATEGORY_ORDER.index(r["category"]), r["id"])):
        n += 1
        o.append(block(n, by_id[r["id"]] | {"v2": r}))
    o.append(f"## Agreements ({len(rows) - len(dis)})\n")
    for r in rows:
        if r["pair"]["id"] not in ids:
            n += 1
            o.append(block(n, r))
    return "\n".join(o)


# ── commands ─────────────────────────────────────────────────────────────────

def _meta(recorded: Dict[str, Any], parent: Dict[str, Any], hit_rate: Optional[float], catalog) -> Dict[str, Any]:
    decisions = (recorded.get("decisions") or []) + (parent.get("decisions") or [])
    return {"recorded": (recorded.get("exported_at") or "n/a")[:10],
            "models": sorted({d.get("resolved_model") for d in decisions if d.get("resolved_model")}),
            "hit_rate": "n/a" if hit_rate is None else f"{hit_rate:.0%}",
            "profile": load_doc()["profile"], "catalog": len(catalog)}


def cmd_record(args) -> int:
    doc = load_doc(Path(args.pairs))
    pairs = doc["pairs"]
    if args.only:
        pairs = [p for p in pairs if p["id"] in args.only]
    _pin_store(Path(args.data_dir))
    os.environ["ART_JEV_MODE"] = "auto"
    from harness.decisions import engine, memory_gate, recordings
    if engine.get_client() is None:
        print("no TYPESAFE_API_KEY in the environment or .env; nothing was recorded", file=sys.stderr)
        return 2
    parent = json.loads(base.RECORDINGS_PATH.read_text(encoding="utf-8"))
    recordings.import_recordings(parent)                    # the 12 messages already asked alone cost nothing
    parent_keys = {d["cache_key"] for d in parent["decisions"]}
    catalog = base.profile_catalog(doc["profile"])
    engine.reset_stats()
    failures = []
    jobs = [(p, None) for p in pairs] + [(p, p["previous"]) for p in pairs]
    for i, (pair, previous) in enumerate(jobs, 1):
        live = engine.stats().get(memory_gate.POINT, {}).get("jev", 0)
        if live >= args.max_questions:
            print(f"stopping: {live} live questions reached the budget of {args.max_questions}", file=sys.stderr)
            failures.append("budget")
            break
        answers = memory_gate.ask(pair["message"], catalog, previous)
        bad = next((a for a in answers if a.fell_back), None)
        if bad is not None:
            failures.append(f"{pair['id']}: {bad.reason}")
            print(f"[{i}/{len(jobs)}] FAILED {pair['id']}: {bad.reason}", flush=True)
            if len(failures) >= 3 and not args.keep_going:
                print("three failures; stopping (use --keep-going to continue)", file=sys.stderr)
                break
            continue
        version = memory_gate.version_for(previous)
        g = memory_gate.parse_answers(answers, catalog, pair["message"], version)
        print(f"[{i}/{len(jobs)}] {pair['id']}{' (v2)' if previous else ''}: p={g.p:.2f} {g.direction} "
              f"{g.target_key or 'no_match'} s{g.strength} via {g.source}", flush=True)
    st = engine.stats().get(memory_gate.POINT, {})
    print(json.dumps({"jobs": len(jobs), "failed": len(failures), "live_answers": st.get("jev", 0),
                      "cache_answers": st.get("cache", 0), "requests": st.get("requests", 0),
                      "input_tokens": round(st.get("input_tokens", 0)), "output_tokens": round(st.get("output_tokens", 0))}))
    if failures:
        print("re-run `record` to retry only the failed messages (answered ones are cached)", file=sys.stderr)
        return 1
    out = recordings.export_recordings()
    out["decisions"] = [d for d in out["decisions"] if d["cache_key"] not in parent_keys]
    Path(args.recordings).write_text(json.dumps(out, sort_keys=True, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {len(out['decisions'])} recordings to {args.recordings}")
    return 0


def cmd_analyze(args) -> int:
    doc = load_doc(Path(args.pairs))
    recorded = json.loads(Path(args.recordings).read_text(encoding="utf-8"))
    parent = json.loads(base.RECORDINGS_PATH.read_text(encoding="utf-8"))
    from harness.decisions.client import JevReplayMiss
    try:
        rows, catalog, st = replay_all(doc, recorded, parent)
    except JevReplayMiss as exc:
        print(f"replay missed: {exc}", file=sys.stderr)
        return 2
    if st["hit_rate"] != 1.0 or st["jev"] or st["fallback"]:
        print(f"replay was not a 100% cache hit: {st}", file=sys.stderr)
        return 2
    main_pairs = base.load_pairs()
    Path(args.report).write_text(
        render_report(rows, main_pairs, catalog, _meta(recorded, parent, st["hit_rate"], catalog)) + "\n", encoding="utf-8")
    Path(args.review).write_text(render_review(rows) + "\n", encoding="utf-8")
    rec = recommend(rows)
    print(f"replayed {len(rows)} context messages (v1 and v2), hit rate {st['hit_rate']:.0%}; recommended v2 "
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
            s.add_argument("--only", nargs="*", default=[], help="Only these pair ids (a smoke test).")
            s.add_argument("--max-questions", type=int, default=MAX_QUESTIONS,
                           help="Stop before asking more than this many live questions.")
            s.add_argument("--keep-going", action="store_true", help="Do not stop after three failures.")
        else:
            s.add_argument("--report", default=str(REPORT_PATH))
            s.add_argument("--review", default=str(REVIEW_PATH))
    args = p.parse_args(list(sys.argv[1:] if argv is None else argv))
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
