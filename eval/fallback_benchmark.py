"""Score every Jev decision point's fallback on the labelled set its threshold was fitted on (issue #350).

Jev is optional (epic #349): with no `TYPESAFE_API_KEY`, `ART_JEV_MODE=off`, or an API error, each of
the six shipped decision points (`docs/harness.md` § 4) runs on its fallback. This script runs those
fallbacks, through the same production functions the harness calls, on `eval/<point>_labels/`, and
reports the same error breakdown as that point's Jev `REPORT.md`: the harmful error first (a
fabrication or a pinned topic that reaches the page, a poor-fit pick, a wrong write), then the misses
and the counts. `eval/FALLBACKS.md` sets the result beside Jev's figures, **read from those REPORT.md
files** (never retyped), and proposes the bar each fallback has to clear:

    python eval/fallback_benchmark.py write    # regenerate the tables in eval/FALLBACKS.md
    python eval/fallback_benchmark.py check    # exit 1 when the committed tables are stale
    python eval/fallback_benchmark.py json     # the measured numbers, as JSON

Nothing here calls Jev. `measure` forces `ART_JEV_MODE=off` for its duration (and drops
`TYPESAFE_API_KEY`), asks the engine for no recordings and opens no network connection, so it needs
neither the recordings nor a key. A fallback that is absent (coverage's semantic target) is reported as
exactly that: no requirement is credited. No number stands in for a missing check. The support fallback
was "unchecked" when this baseline was taken (#350: 0 of 54 should-block pairs blocked) and is the
deterministic check since #351; its row reports what that check does, beside the #123 gate.

Rules for what is counted (the epic's, #349):

- Harmful errors first. A fallback that blocks a supported pair is reported even though it blocks
  nothing Jev would not: "a fallback never blocks more than Jev would on the same labelled set
  without saying so". The #123 numbers-and-names gate, which runs beside the unchecked support
  fallback in production, is scored on its own line for that reason.
- No threshold is fitted here: the fallbacks have none. A bar for a future fallback is
  proposed, never tuned on this set, and any threshold a fix introduces is fitted the way
  `docs/architecture.md` § 8.8 says (harmful errors first, never on a model's reported confidence).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import math
import os
import re
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from eval.fit_support_threshold import _pct, _pin_store, _table  # noqa: E402  the shared helpers

EVAL = Path(__file__).resolve().parent
DOC_PATH = EVAL / "FALLBACKS.md"
SUPPORT_REPORT = EVAL / "support_labels" / "REPORT.md"
SUPPORT_PAIRS = EVAL / "support_labels" / "pairs.json"
PIN_REPORT = EVAL / "negative_pin_labels" / "REPORT.md"
PIN_PAIRS = EVAL / "negative_pin_labels" / "pairs.json"
COVERAGE_REPORT = EVAL / "coverage_labels" / "REPORT.md"
COVERAGE_PAIRS = EVAL / "coverage_labels" / "pairs.json"
GATE_REPORT = EVAL / "memory_gate_labels" / "REPORT.md"
GATE_CONTEXT_REPORT = EVAL / "memory_gate_labels" / "context" / "REPORT.md"
GATE_PAIRS = EVAL / "memory_gate_labels" / "pairs.json"
GATE_CONTEXT_PAIRS = EVAL / "memory_gate_labels" / "context" / "pairs.json"
LIBRARY_REPORT = EVAL / "library_labels" / "REPORT.md"

POINTS = ("support", "negative_pin", "coverage", "memory_gate", "variant_choice", "track_baseline")
BLOCK_PREFIX = "fallback-benchmark"


# ── running with Jev off ─────────────────────────────────────────────────────

@contextlib.contextmanager
def jev_off() -> Iterator[None]:
    """`ART_JEV_MODE=off` and no API key for the block, restored after. In `off` mode the engine
    returns the fallback before it touches the cache, the key or the network."""
    saved = {k: os.environ.get(k) for k in ("ART_JEV_MODE", "TYPESAFE_API_KEY")}
    os.environ["ART_JEV_MODE"] = "off"
    os.environ.pop("TYPESAFE_API_KEY", None)
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _load(path: Path) -> Dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _frac(n: int, d: int) -> str:
    return f"{n}/{d} ({_pct(n / d if d else None)})" if d else f"{n}/{d}"


# ── 1. support (#193, #237) ──────────────────────────────────────────────────

def _support_page(pair: Dict[str, Any]):
    """One pair as the page the gate sees: an experience whose one bullet cites the evidence."""
    from agents.checks import exp_key

    item = {"title": "x", "company": "y", "bullets": [pair["bullet"]]}
    key = exp_key(item)
    item["cites"] = {pair["bullet"]: [f"{key}#b{i}" for i in range(len(pair["evidence"]))]}
    source = {key: list(pair["evidence"])}
    base = ({"experiences": [{"title": "x", "company": "y", "bullets": [pair["original"]]}]}
            if pair.get("original") else None)
    return {"experiences": [item]}, source, base


def measure_support(pairs: Optional[Sequence[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """The support fallback on `eval/support_labels/pairs.json`, through `make_support_checker`.

    With Jev off every finding is the deterministic check's reading (#351, `status: "heuristic"`,
    `source: "fallback"`), so `violations` and `reviews` are the fallback's own block and review.
    `consistency` is the #123 numbers-and-names gate on the same pair: it runs beside the support
    check in production, so what stands between a reworded claim and the page is the two together.
    The pair carries only its cited text as evidence, where a real item adds its other source
    bullets and header, so both checks' catches here are upper bounds."""
    from agents.checks import consistency_check
    from harness.decisions import support

    pairs = list(pairs if pairs is not None else _load(SUPPORT_PAIRS)["pairs"])
    rows = []
    with jev_off():
        for pair in pairs:
            content, source, base = _support_page(pair)
            findings = support.make_support_checker(source, base)(content)
            if len(findings) != 1 or findings[0]["status"] != "heuristic" or findings[0]["source"] != "fallback":
                raise RuntimeError(f"{pair['id']}: expected one fallback finding, got {findings}")
            rows.append({
                "id": pair["id"], "category": pair["category"], "label": pair["label"],
                "blocked": bool(support.violations(findings)),
                "reviewed": bool(support.reviews(findings)),
                "reason": findings[0]["reason"], "score": findings[0]["score"],
                "g123": bool(consistency_check(content, source)),
            })
    return _support_summary(rows)


def _support_summary(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """`blocked` / `reviewed` / `silent` are the #351 check alone; `with123` counts a pair blocked by
    either the check or the #123 gate (what the page is actually held to), and `silent` there is a pair
    neither blocks nor the check surfaces for review."""
    should = [r for r in rows if r["label"] != "supported"]
    supported = [r for r in rows if r["label"] == "supported"]
    for r in rows:
        r["either"] = r["blocked"] or r["g123"]

    def by_label(sel, key):
        return {l: [sum(1 for r in sel if r["label"] == l and r[key]), sum(1 for r in sel if r["label"] == l)]
                for l in ("contradicts", "adds_unsupported")}

    cats = {}
    for c in sorted({r["category"] for r in rows}):
        rs = [r for r in rows if r["category"] == c]
        sb = [r for r in rs if r["label"] != "supported"]
        cats[c] = {"n": len(rs), "should": len(sb), "blocked": sum(r["blocked"] for r in sb),
                   "reviewed": sum(r["reviewed"] and not r["blocked"] for r in sb),
                   "g123": sum(r["g123"] for r in sb), "either": sum(r["either"] for r in sb),
                   "g123_false": sum(r["g123"] for r in rs if r["label"] == "supported")}
    return {
        "n": len(rows), "should": len(should), "supported": len(supported),
        "blocked": sum(r["blocked"] for r in should), "reviewed": sum(r["reviewed"] for r in should),
        "silent": sum(not (r["blocked"] or r["reviewed"]) for r in should),
        "by_label": by_label(should, "blocked"),
        "false_blocks": sum(r["blocked"] for r in supported),
        "false_reviews": sum(r["reviewed"] for r in supported),
        "false_ids": [r["id"] for r in supported if r["blocked"] or r["reviewed"]],
        "silent_ids": [r["id"] for r in should if not (r["either"] or r["reviewed"])],
        "reasons": sorted({r["reason"] for r in rows}),
        "g123": {"blocked": sum(r["g123"] for r in should), "by_label": by_label(should, "g123"),
                 "false_blocks": sum(r["g123"] for r in supported),
                 "false_ids": [r["id"] for r in supported if r["g123"]]},
        "with123": {"blocked": sum(r["either"] for r in should), "by_label": by_label(should, "either"),
                    "reviewed": sum(r["reviewed"] and not r["either"] for r in should),
                    "silent": sum(not (r["either"] or r["reviewed"]) for r in should),
                    "false_blocks": sum(r["either"] for r in supported),
                    "false_ids": [r["id"] for r in supported if r["either"]]},
        "categories": cats,
    }


# ── 2. negative pin (#232) ───────────────────────────────────────────────────

def _pin_page(pair: Dict[str, Any]) -> Dict[str, Any]:
    """One (text, pin) pair as the page the gate sees: the text in the field it came from."""
    kind, text = pair.get("kind", "bullet"), pair["text"]
    if kind == "name":
        return {"projects": [{"name": text, "bullets": []}]}
    if kind == "company":
        return {"experiences": [{"title": "x", "company": text, "bullets": []}]}
    if kind == "bullet":
        return {"experiences": [{"title": "x", "company": "y", "bullets": [text]}]}
    return {"experiences": [{"title": "x", "company": "y", kind: text, "bullets": []}]}


def measure_negative_pin(pairs: Optional[Sequence[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """The negative-pin fallback on `eval/negative_pin_labels/pairs.json`: the whole-word term match
    (`acceptance.negative_pin_violations`), which always runs, plus `make_pin_checker`, whose
    findings are all `unchecked` with Jev off and so add no violation."""
    from harness import acceptance
    from harness.decisions import negative_pins as neg

    pairs = list(pairs if pairs is not None else _load(PIN_PAIRS)["pairs"])
    rows = []
    with jev_off():
        for pair in pairs:
            content, term = _pin_page(pair), pair["pin"]["term"]
            skill = [term] if pair["pin"].get("skill") else []
            by_term = bool(acceptance.negative_pin_violations(content, [term], skill))
            findings = neg.make_pin_checker([{**pair["pin"], "term": term}], None)(content)
            if any(f["status"] != "unchecked" for f in findings):
                raise RuntimeError(f"{pair['id']}: a pin finding was answered with Jev off")
            rows.append({"id": pair["id"], "category": pair["category"], "label": pair["label"],
                         "blocked": by_term or bool(neg.violations(findings)),
                         "reviewed": bool(neg.reviews(findings))})
    mentions = [r for r in rows if r["label"] == "mentions"]
    other = [r for r in rows if r["label"] != "mentions"]
    cats = {}
    for c in dict.fromkeys(r["category"] for r in rows):
        rs = [r for r in rows if r["category"] == c]
        cats[c] = {"n": len(rs), "mentions": sum(r["label"] == "mentions" for r in rs),
                   "blocked": sum(r["blocked"] for r in rs),
                   "blocked_mentions": sum(r["blocked"] for r in rs if r["label"] == "mentions")}
    return {"n": len(rows), "mentions": len(mentions), "other": len(other),
            "blocked": sum(r["blocked"] for r in mentions),
            "reviewed": sum(r["reviewed"] for r in mentions),
            "silent": sum(not (r["blocked"] or r["reviewed"]) for r in mentions),
            "false_blocks": sum(r["blocked"] for r in other),
            "false_ids": [r["id"] for r in other if r["blocked"]],
            "categories": cats}


# ── 3. requirement coverage (#126) ───────────────────────────────────────────

def _coverage_page(pair: Dict[str, Any]) -> Dict[str, Any]:
    if pair.get("kind") == "education":
        return {"education": [{"degree": pair["text"]}]}
    return {"experiences": [{"title": "x", "company": "y", "bullets": [pair["text"]]}]}


def measure_coverage(pairs: Optional[Sequence[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """The coverage fallback on `eval/coverage_labels/pairs.json`: the semantic target is absent.
    Every pair goes through `make_coverage_checker`, which with Jev off reports `unchecked` and so
    leaves `semantic_coverage` out of the metric vector. What is left is the literal `coverage`, which
    needs the posting's keyword terms; the pairs carry none, so it is not scored here."""
    from harness.decisions import coverage

    pairs = list(pairs if pairs is not None else _load(COVERAGE_PAIRS)["pairs"])
    rows = []
    with jev_off():
        for pair in pairs:
            check = coverage.make_coverage_checker([{**pair["requirement"], "type": "required"}])
            result = check(_coverage_page(pair))
            if result["status"] != "unchecked":
                raise RuntimeError(f"{pair['id']}: coverage was {result['status']} with Jev off")
            rows.append({"id": pair["id"], "category": pair["category"], "label": pair["label"],
                         "covered": bool(result["covered"]), "answered": result["score"] is not None})
    covered = [r for r in rows if r["label"] == "covered"]
    cats = {}
    for c in dict.fromkeys(r["category"] for r in rows):
        rs = [r for r in rows if r["category"] == c]
        cats[c] = {"n": len(rs), "should": sum(r["label"] == "covered" for r in rs),
                   "credited": sum(r["covered"] for r in rs)}
    return {"n": len(rows), "should": len(covered), "other": len(rows) - len(covered),
            "credited": sum(r["covered"] for r in covered),
            "false_covers": sum(r["covered"] for r in rows if r["label"] != "covered"),
            "answered": sum(r["answered"] for r in rows), "categories": cats}


# ── 4. memory gate (#202, #244) ──────────────────────────────────────────────

def _route_without_jev(message: str) -> Dict[str, Any]:
    """The decision `memory.observe` takes with no Jev answer: the prefilter settles a non-candidate,
    a message that states several things and a role-scoped one; any other candidate goes to the host."""
    from harness.decisions import memory_gate as mg

    return mg.route(message, mg.prefilter(message), None, job_known=True, write_ok=True,
                    unavailable="mode=off")


def measure_memory_gate(doc: Optional[Dict[str, Any]] = None,
                        context: Optional[Sequence[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """The memory-gate fallback on `eval/memory_gate_labels/pairs.json` (111 messages) and on the
    40 messages of `context/pairs.json` (#244, 12 of them also in the main set): the prefilter alone."""
    doc = doc or _load(GATE_PAIRS)
    context = list(context if context is not None else _load(GATE_CONTEXT_PAIRS)["pairs"])

    def run(pairs):
        out = []
        for pair in pairs:
            d = _route_without_jev(pair["message"])
            out.append({"id": pair["id"], "category": pair["category"], "pref": bool(pair["is_preference"]),
                        "action": d["action"], "reason": d["reason"]})
        return out

    def summarize(rows):
        prefs = [r for r in rows if r["pref"]]
        other = [r for r in rows if not r["pref"]]
        kept = lambda rs: sum(r["action"] != "drop" for r in rs)          # noqa: E731
        return {"n": len(rows), "prefs": len(prefs), "other": len(other),
                "writes": sum(r["action"] == "write" for r in rows),
                "host": sum(r["action"] == "host" for r in rows),
                "drop": sum(r["action"] == "drop" for r in rows),
                "prefs_kept": kept(prefs), "prefs_dropped": len(prefs) - kept(prefs),
                "dropped_ids": [r["id"] for r in prefs if r["action"] == "drop"],
                "other_to_host": kept(other),
                "reasons": dict(sorted(Counter(r["reason"] for r in rows if r["action"] != "drop").items()))}

    with jev_off():
        main, ctx = run(doc["pairs"]), run(context)
    cats = {}
    for c in dict.fromkeys(r["category"] for r in main):
        rs = [r for r in main if r["category"] == c]
        cats[c] = {"n": len(rs), "prefs": sum(r["pref"] for r in rs), "host": sum(r["action"] == "host" for r in rs)}
    return {"main": summarize(main), "context": summarize(ctx), "categories": cats}


# ── 5 and 6. variant choice and track baseline (#199) ────────────────────────

def measure_library() -> Dict[str, Any]:
    """The variant and baseline fallbacks on `eval/library_labels/`, through the production functions
    (`harness.library.best_variant`, `pick_track`; #352) as `fit_library_thresholds` runs them.
    Also the same cases answered by a constant (always `no_match`, always `none`), the floor any
    fallback has to beat to be worth its code. The variant pick is given the job's title and
    requirements, with uniform keyword weights for its tie-break, as in the fit."""
    from eval import fit_library_thresholds as fit

    vdoc, bdoc = fit.load_variants(), fit.load_baselines()
    with jev_off():
        vrows = [{**c, "pick": fit.fallback_variant(vdoc, c)} for c in vdoc["cases"]]
        brows = [{**c, "pick": fit.fallback_baseline(bdoc, c)} for c in bdoc["cases"]]

    def block(rows, outcome, order):
        pick = lambda r: r["pick"]                                           # noqa: E731
        t = fit.tally(rows, outcome, pick)
        const = fit.tally(rows, outcome, lambda r: None)
        cats = {c: fit.tally(rs, outcome, pick) for c, rs in fit.groups(rows, order)}
        return {"fallback": t, "constant": const, "categories": cats,
                "wrong_ids": [r["id"] for r in rows if outcome(r, r["pick"]) in ("poor", "wrong")]}

    return {"variant_choice": block(vrows, fit.variant_outcome, fit.VARIANT_CATEGORIES),
            "track_baseline": block(brows, fit.baseline_outcome, fit.BASELINE_CATEGORIES)}


def measure() -> Dict[str, Any]:
    """Every fallback, on its labelled set, with Jev off. Pure given the committed labels."""
    lib = measure_library()
    return {"support": measure_support(), "negative_pin": measure_negative_pin(),
            "coverage": measure_coverage(), "memory_gate": measure_memory_gate(),
            "variant_choice": lib["variant_choice"], "track_baseline": lib["track_baseline"]}


# ── Jev's figures, read from the REPORT.md files ─────────────────────────────

def _cells(line: str) -> List[str]:
    return [c.strip().replace("*", "").replace("`", "").strip() for c in line.strip().strip("|").split("|")]


def _section(text: str, heading: str) -> str:
    i = text.index(heading)
    return text[i:]


def _table_after(text: str, heading: str) -> List[List[str]]:
    """The first markdown table after `heading`, header row first."""
    rows, started = [], False
    for ln in _section(text, heading).splitlines()[1:]:
        if ln.startswith("|"):
            started = True
            if set(ln.replace("|", "").strip()) <= set("-: "):
                continue
            rows.append(_cells(ln))
        elif started:
            break
    return rows


def _row(rows: List[List[str]], first: str) -> List[str]:
    for r in rows[1:]:
        if r[0] == first or r[0].startswith(first):
            return r
    raise KeyError(first)


def _ints(text: str) -> List[int]:
    return [int(x) for x in re.findall(r"\d+", text)]


def _text(path: Path) -> str:
    return Path(path).read_text(encoding="utf-8")


def jev_support() -> Dict[str, Any]:
    t = _text(SUPPORT_REPORT)
    row = _row(_table_after(t, "### At the recommended thresholds"), "in support.py (sum score)")
    band = _ints(row[8])                              # "4 (3 should / 1 supported)"
    grid = _table_after(t, "### Blocked / should-block, by group")
    col = grid[0].index(row[1])                       # the column of the shipped tau_block, e.g. "0.85"
    return {"tau_block": row[1], "tau_review": row[2], "blocked": row[3], "contradicts": row[4],
            "adds": row[5], "false_blocks": row[7], "review_total": band[0], "review_should": band[1],
            "review_supported": band[2], "silent": int(row[9]),
            "blocked_n": _ints(row[3])[0], "should_n": _ints(row[3])[1],
            "by_group": {r[0]: int(r[col]) for r in grid[1:]}, "group_should": {r[0]: int(r[1]) for r in grid[1:]}}


def jev_negative_pin() -> Dict[str, Any]:
    from harness.decisions import negative_pins as neg

    t = _text(PIN_REPORT)
    grid = _table_after(t, "## tau_block grid, whole set")
    row = _row(grid, f"{neg.TAU_BLOCK:.2f}")
    h = {name: row[i] for i, name in enumerate(grid[0])}
    band = _table_after(t, "## Review band")
    cell = _row(band, f"{neg.TAU_REVIEW:.2f}")[band[0].index(f"{neg.TAU_BLOCK:.2f}")]
    total, ment, _ = _ints(cell)
    return {"tau_block": row[0], "tau_review": f"{neg.TAU_REVIEW:.2f}", "blocked": h["TP/should"],
            "false_blocks": h["false blocks"], "direct": h["direct blocked"],
            "paraphrase": h["paraphrase blocked"], "indirect": h["indirect blocked"],
            "near_miss": h["near_miss blocked"], "unrelated": h["unrelated blocked"],
            "negation": h["negation blocked"], "blocked_n": _ints(h["TP/should"])[0],
            "review_total": total, "review_mentions": ment}


def jev_coverage() -> Dict[str, Any]:
    t = _text(COVERAGE_REPORT)
    m = re.search(r"Pooled: (\d+)/(\d+) covered pairs covered, (\d+)/(\d+) false covers\.", t)
    cats = {r[0]: r[3] for r in _table_after(t, "### By category at the recommended threshold")[1:]}
    return {"covered": int(m.group(1)), "should": int(m.group(2)), "false_covers": int(m.group(3)),
            "other": int(m.group(4)), "called_covered": cats}


def jev_memory_gate() -> Dict[str, Any]:
    t = _text(GATE_REPORT)
    rows = _table_after(t, "## Gate precision and recall")
    heur, kept, wrote = _row(rows, "heuristics alone"), _row(rows, "gate kept"), _row(rows, "gate wrote")
    m = re.search(r"the gate writes (\d+) of (\d+) messages \((\d+) true preferences\)\. Wrong writes: (\d+)",
                  t.replace("**", ""))
    ctx = _table_after(_text(GATE_CONTEXT_REPORT), "## v1 against v2")
    return {"heuristic": {"tp": int(heur[1]), "fp": int(heur[2]), "fn": int(heur[3])},
            "kept": {"tp": int(kept[1]), "fp": int(kept[2]), "fn": int(kept[3])},
            "writes": int(m.group(1)), "messages": int(m.group(2)), "wrong_writes": int(m.group(4)),
            "context": {"kept_v1": _row(ctx, "true preferences kept")[1],
                        "kept_v2": _row(ctx, "true preferences kept")[2],
                        "handed_v1": _row(ctx, "handed to the host")[1],
                        "handed_v2": _row(ctx, "handed to the host")[2],
                        "writes_v1": _row(ctx, "automatic writes")[1],
                        "wrong_v1": _row(ctx, "wrong automatic writes")[1]}}


def jev_library() -> Dict[str, Any]:
    t = _text(LIBRARY_REPORT)
    out: Dict[str, Any] = {}
    for key, heading, fb, bad in (("variant_choice", "## Variant choice", "The Jev-free fallback (concept fit)", "poor"),
                                  ("track_baseline", "## Track baseline", "The Jev-free fallback (duties fit)", "wrong")):
        sec = _section(t, heading)
        end = sec.find("\n## ", 5)
        sec = sec[:end] if end > 0 else sec
        rows = _table_after(sec, heading)
        jev, fall = _row(rows, "Jev at the threshold"), _row(rows, fb)
        cats = _table_after(sec, "### By category")
        head = {name: i for i, name in enumerate(cats[0])}
        poor_col = "Jev@τ poor" if bad == "poor" else "Jev@τ wrong"
        fb_bad = "fallback poor" if bad == "poor" else "fallback wrong"
        out[key] = {"jev": {"correct": jev[1], "picks": int(jev[2]), "right": int(jev[3]), "bad": int(jev[4]),
                            "lost": int(jev[5])},
                    "report_fallback": {"correct": fall[1], "picks": int(fall[2]), "right": int(fall[3]),
                                        "bad": int(fall[4]), "lost": int(fall[5])},
                    "categories": {r[0]: {"cases": int(r[1]), "jev_correct": r[head["Jev@τ correct"]],
                                          "jev_bad": int(r[head[poor_col]]),
                                          "fb_correct": r[head["fallback correct"]],
                                          "fb_bad": int(r[head[fb_bad]])} for r in cats[1:]}}
    return out


def jev_figures() -> Dict[str, Any]:
    return {"support": jev_support(), "negative_pin": jev_negative_pin(), "coverage": jev_coverage(),
            "memory_gate": jev_memory_gate(), **jev_library()}


# ── the proposed targets ─────────────────────────────────────────────────────

def half_gap(floor: int, jev: int) -> int:
    """The proposed bar: close at least half the distance from what runs today to Jev's figure,
    rounded up. A fixed rule, so the bar follows the measurement and is not tuned to a fix."""
    return math.ceil((floor + jev) / 2)


# What ran for the two library points before #352 (#229's word overlap and title lookup), on the
# same labelled sets. The bars of the library points are half the gap from *these* figures to Jev's:
# they were set at #350 and stay put when a fix lands, so a verdict can say the bar was met.
BEFORE_352 = {"variant_choice": {"correct": 18, "picks": 18, "right": 7, "poor": 7, "wrong": 4, "lost": 19},
              "track_baseline": {"correct": 27, "picks": 23, "right": 15, "poor": 0, "wrong": 8, "lost": 1}}


def targets(m: Dict[str, Any], j: Dict[str, Any]) -> Dict[str, Any]:
    """Per point: the hard bars (harmful errors, never worse than Jev's), the soft bar (half the gap
    on the main figure), and the verdict. Points #349 does not schedule keep their baseline."""
    s, js = m["support"], j["support"]
    g = s["g123"]
    sup_total = half_gap(g["blocked"], js["blocked_n"])
    sup_con = half_gap(g["by_label"]["contradicts"][0], _ints(js["contradicts"])[0])
    sup_add = half_gap(g["by_label"]["adds_unsupported"][0], _ints(js["adds"])[0])
    v, jv = m["variant_choice"]["fallback"], j["variant_choice"]["jev"]
    b, jb = m["track_baseline"]["fallback"], j["track_baseline"]["jev"]
    v0, b0 = BEFORE_352["variant_choice"], BEFORE_352["track_baseline"]
    v_bar = half_gap(v0["correct"], _ints(jv["correct"])[0])
    b_bar = half_gap(b0["correct"], _ints(jb["correct"])[0])
    jv_other = jv["picks"] - jv["right"] - jv["bad"]
    v_const, b_const = m["variant_choice"]["constant"]["correct"], m["track_baseline"]["constant"]["correct"]
    v_met = v["poor"] <= jv["bad"] and v["wrong"] <= jv_other and v["correct"] >= v_bar and v["correct"] > v_const
    b_met = b["wrong"] <= jb["bad"] and b["correct"] >= b_bar and b["correct"] > b_const
    w = s["with123"]
    review_cap = math.floor(0.10 * s["supported"])
    met = (w["blocked"] >= sup_total and s["false_blocks"] == 0 and s["false_reviews"] <= review_cap
           and w["by_label"]["contradicts"][0] >= sup_con and w["by_label"]["adds_unsupported"][0] >= sup_add)
    return {
        "support": {"verdict": "met: #351" if met else "work: #351",
                    "hard": f"0/{s['supported']} supported pairs blocked by the new check (#123's own {g['false_blocks']} is reported, "
                            f"not counted against it); any review tier surfaces at most "
                            f"{review_cap} of the {s['supported']} supported pairs (the fits' 10% cap)",
                    "bar": f"{sup_total}/{s['should']} should-block pairs blocked with #123 counted "
                           f"(contradicts {sup_con}/{s['by_label']['contradicts'][1]}, "
                           f"adds_unsupported {sup_add}/{s['by_label']['adds_unsupported'][1]})",
                    "reason": f"Half the way from the {g['blocked']} that #123 blocks alone to Jev's {js['blocked_n']}; "
                              f"a check that blocks an honest edit is worse than none, so the hard bar is Jev's 0. "
                              f"Measured with the #351 check: {w['blocked']}/{s['should']} blocked with #123 counted "
                              f"(contradicts {w['by_label']['contradicts'][0]}/{w['by_label']['contradicts'][1]}, adds_unsupported "
                              f"{w['by_label']['adds_unsupported'][0]}/{w['by_label']['adds_unsupported'][1]}), "
                              f"{s['false_blocks']}/{s['supported']} supported blocked and {s['false_reviews']}/{s['supported']} "
                              f"surfaced for review by the new check."},
        "negative_pin": {"verdict": "leave",
                         "hard": f"0/{m['negative_pin']['other']} not-a-mention pairs blocked (hold)",
                         "bar": f"hold {m['negative_pin']['blocked']}/{m['negative_pin']['mentions']} (the direct mentions)",
                         "reason": "The term match is exact and never wrongly blocks; the "
                                   f"{m['negative_pin']['silent']} misses are paraphrases and products that no word list reads, and the host "
                                   "is handed every pin verbatim."},
        "coverage": {"verdict": "good enough",
                     "hard": "none possible: an absent target blocks and writes nothing",
                     "bar": "hold: the target stays absent without Jev",
                     "reason": "`semantic_coverage` is a target, never a gate; the literal `coverage` and `relevance_density` still steer."},
        "memory_gate": {"verdict": "good enough",
                        "hard": "0 automatic writes without Jev (hold)",
                        "bar": f"hold {m['memory_gate']['main']['prefs_kept']}/{m['memory_gate']['main']['prefs']} preferences kept",
                        "reason": f"Nothing is written, so no write can be wrong; the {m['memory_gate']['main']['prefs_dropped']} misses are "
                                  "the prefilter's, which Jev sits behind and shares."},
        "variant_choice": {"verdict": "done: #352" if v_met else "work: #352",
                           "hard": f"0/{v['n']} poor-fit picks, and at most {jv_other} other wrong pick",
                           "bar": f"{v_bar}/{v['n']} correct, and above the constant 'always no_match' "
                                  f"({v_const}/{v['n']})",
                           "reason": f"Half the way from {v0['correct']} (#229's word overlap, before #352) to Jev's "
                                     f"{_ints(jv['correct'])[0]}; that fallback was only {v0['correct'] - v_const} cases "
                                     "better than a constant."},
        "track_baseline": {"verdict": "done: #352" if b_met else "work: #352",
                           "hard": f"0/{b['n']} wrong tracks",
                           "bar": f"{b_bar}/{b['n']} correct, and above the constant 'always none' "
                                  f"({b_const}/{b['n']})",
                           "reason": f"Half the way from {b0['correct']} (#229's title lookup, before #352) to Jev's "
                                     f"{_ints(jb['correct'])[0]}; the lookup read the title alone and was right on none of "
                                     "the jobs whose title and duties disagree."},
    }


# ── rendering ────────────────────────────────────────────────────────────────

def block_summary(m, j, t) -> str:
    s, js = m["support"], j["support"]
    p, jp = m["negative_pin"], j["negative_pin"]
    c, jc = m["coverage"], j["coverage"]
    g, jg = m["memory_gate"]["main"], j["memory_gate"]
    v, jv = m["variant_choice"]["fallback"], j["variant_choice"]["jev"]
    b, jb = m["track_baseline"]["fallback"], j["track_baseline"]["jev"]
    head = ["point (labelled set)", "harmful error: fallback", "harmful error: Jev", "main figure: fallback",
            "main figure: Jev", "proposed bar", "verdict"]
    body = [
        ["support (89 pairs)", f"{s['with123']['silent']}/{s['should']} should-block pairs kept silently by the check and #123 "
                               f"({s['with123']['reviewed']} surfaced for review); {s['false_blocks']}/{s['supported']} supported "
                               f"blocked by the check, {s['g123']['false_blocks']} by #123",
         f"{js['silent']}/{s['should']} kept silently ({js['review_should']} surfaced for review); {js['false_blocks']} supported blocked",
         f"blocked {s['blocked']}/{s['should']}; with #123 {s['with123']['blocked']}/{s['should']}",
         f"blocked {js['blocked']}", t["support"]["bar"], t["support"]["verdict"]],
        ["negative pin (74 pairs)", f"{p['silent']}/{p['mentions']} pinned mentions render", f"0/{p['mentions']} render "
         f"({jp['review_mentions']} surfaced for review)", f"blocked {_frac(p['blocked'], p['mentions'])}",
         f"blocked {jp['blocked']}", t["negative_pin"]["bar"], t["negative_pin"]["verdict"]],
        ["coverage (131 pairs)", "none possible", "0 false covers", f"credited {c['credited']}/{c['should']} (target absent)",
         f"credited {jc['covered']}/{jc['should']}", t["coverage"]["bar"], t["coverage"]["verdict"]],
        ["memory gate (111 messages)", f"{g['writes']} wrong writes (it writes nothing)", f"{jg['wrong_writes']} wrong of {jg['writes']} writes",
         f"{g['prefs_kept']}/{g['prefs']} preferences kept; {g['other_to_host']} non-preferences sent to the host",
         f"{jg['kept']['tp']}/{g['prefs']} kept; {jg['kept']['fp']} non-preferences sent", t["memory_gate"]["bar"],
         t["memory_gate"]["verdict"]],
        ["variant choice (48 cases)", f"{v['poor']} poor-fit picks, {v['wrong']} other wrong", f"{jv['bad']} poor-fit, "
         f"{jv['picks'] - jv['right'] - jv['bad']} other wrong", f"{_frac(v['correct'], v['n'])} correct", jv["correct"],
         t["variant_choice"]["bar"], t["variant_choice"]["verdict"]],
        ["track baseline (36 cases)", f"{b['wrong']} wrong tracks", f"{jb['bad']} wrong", f"{_frac(b['correct'], b['n'])} correct",
         jb["correct"], t["track_baseline"]["bar"], t["track_baseline"]["verdict"]],
    ]
    return _table(head, body)


def block_targets(m, j, t) -> str:
    body = [[name.replace("_", " "), t[name]["verdict"], t[name]["hard"], t[name]["bar"], t[name]["reason"]]
            for name in POINTS]
    return _table(["point", "verdict", "hard bar (harmful errors)", "bar for the main figure", "why"], body)


def block_support(m, j, t) -> str:
    s, js = m["support"], j["support"]
    g, w = s["g123"], s["with123"]
    c, cw = s["by_label"], w["by_label"]
    o = [f"{s['n']} pairs: {s['should']} should block (contradicts {c['contradicts'][1]}, "
         f"adds_unsupported {c['adds_unsupported'][1]}), {s['supported']} supported. "
         f"With Jev off every cited bullet's finding is the deterministic check's (`harness/decisions/support_fallback.py`, "
         f"#351, model `fallback-support-v1`, `source`: fallback, `reason`: {', '.join(s['reasons'])}); `violations` and `reviews` "
         "are its block and review. The #123 numbers-and-names gate runs beside it in production, so the middle column counts a "
         "pair blocked by either. Jev figures: `support_labels/REPORT.md`, \"in support.py\" row.\n"]
    o.append(_table(["", "fallback: the #351 check", "fallback: the #351 check + #123",
                     f"Jev (tau_block {js['tau_block']}, tau_review {js['tau_review']})"], [
        ["should-block pairs blocked", _frac(s["blocked"], s["should"]), _frac(w["blocked"], s["should"]), js["blocked"]],
        ["  contradicts", f"{c['contradicts'][0]}/{c['contradicts'][1]}", f"{cw['contradicts'][0]}/{cw['contradicts'][1]}",
         js["contradicts"]],
        ["  adds_unsupported", f"{c['adds_unsupported'][0]}/{c['adds_unsupported'][1]}",
         f"{cw['adds_unsupported'][0]}/{cw['adds_unsupported'][1]}", js["adds"]],
        ["should-block pairs surfaced for review", s["reviewed"], w["reviewed"], js["review_should"]],
        ["**should-block pairs kept silently (the harmful error)**", f"**{s['silent']}**", f"**{w['silent']}**",
         f"**{js['silent']}**"],
        ["supported pairs blocked (false blocks)", f"{s['false_blocks']}/{s['supported']}", f"{w['false_blocks']}/{s['supported']}",
         js["false_blocks"]],
        ["supported pairs surfaced for review", s["false_reviews"], s["false_reviews"], js["review_supported"]],
    ]))
    o.append("")
    o.append(f"**What stands beside it.** The #123 numbers-and-names gate (`agents.checks.consistency_check`) runs beside the "
             f"check, in production and here. On these pairs it alone blocks {g['blocked']}/{s['should']} "
             f"(contradicts {g['by_label']['contradicts'][0]}/{g['by_label']['contradicts'][1]}, adds_unsupported "
             f"{g['by_label']['adds_unsupported'][0]}/{g['by_label']['adds_unsupported'][1]}), the baseline #350 measured, and "
             f"{g['false_blocks']}/{s['supported']} supported pair"
             + (f" ({', '.join('`' + i + '`' for i in g['false_ids'])}), which Jev does not block" if g["false_ids"] else "")
             + ". The pair carries only its cited text as evidence, where a real item adds its other source bullets and header, "
               "so both checks' figures are upper bounds. Lexical drift (`faithfulness_drift`) is an item-level mean over the whole "
               "item's bullets that fires on wholesale rewrites; it has no per-bullet reading, so it is not scored here.\n")
    cats = [[f"`{c_}`", v["should"] or "-", v["g123"] if v["should"] else "-", v["blocked"] if v["should"] else "-",
             v["reviewed"] if v["should"] else "-", v["either"] if v["should"] else "-",
             js["by_group"].get(c_, "-") if v["should"] else "-"]
            for c_, v in s["categories"].items() if v["should"]]
    o.append(_table(["category", "should block", "#123 blocks", "#351 check blocks", "#351 check reviews (not blocked)",
                     "either blocks", f"Jev blocks (tau_block {js['tau_block']})"], cats))
    resid = {c_: v["should"] - v["g123"] for c_, v in s["categories"].items() if v["should"] - v["g123"]}
    o.append("")
    o.append(f"The residual the check worked on, the should-block pairs #123 does not block: {sum(resid.values())} "
             f"({', '.join(f'`{c_}` {n}' for c_, n in resid.items())}). The check blocks "
             f"{w['blocked'] - g['blocked']} of them and surfaces {w['reviewed']} more for review; "
             f"{w['silent']} should-block pairs pass both unflagged"
             + (f" ({', '.join('`' + i + '`' for i in s['silent_ids'])})." if s["silent_ids"] else "."))
    return "\n".join(o)


def block_negative_pin(m, j, t) -> str:
    p, jp = m["negative_pin"], j["negative_pin"]
    o = [f"{p['n']} (text, pin) pairs: {p['mentions']} mentions, {p['other']} not. The fallback is the whole-word term match "
         "(`acceptance.negative_pin_violations`), which always runs; with Jev off the pin checker's findings are all `unchecked`. "
         "Jev figures: `negative_pin_labels/REPORT.md`, \"tau_block grid, whole set\" and \"Review band\".\n"]
    o.append(_table(["", "fallback (term match)", f"Jev (tau_block {jp['tau_block']}, tau_review {jp['tau_review']})"], [
        ["mentions blocked", _frac(p["blocked"], p["mentions"]), jp["blocked"]],
        ["mentions surfaced for review", p["reviewed"], jp["review_mentions"]],
        ["**mentions that render (the harmful error)**", f"**{p['silent']}**", f"**{p['mentions'] - jp['blocked_n'] - jp['review_mentions']}**"],
        ["not-a-mention pairs blocked (false blocks)", f"{p['false_blocks']}/{p['other']}", jp["false_blocks"]],
    ]))
    o.append("")
    cats = p["categories"]
    o.append(_table(["category (blocked / pairs in it)", "fallback", "Jev"], [
        [f"`{c}`" + ("" if v["mentions"] in (0, v["n"]) else f" ({v['mentions']} mentions)"), f"{v['blocked']}/{v['n']}", jp[c]]
        for c, v in cats.items() if c in jp]))
    return "\n".join(o)


def block_coverage(m, j, t) -> str:
    c, jc = m["coverage"], j["coverage"]
    o = [f"{c['n']} (bullet or education entry, requirement) pairs: {c['should']} covered, {c['other']} not. With Jev off "
         f"`make_coverage_checker` reports `unchecked` for every pair, so `semantic_coverage` is left out of the metric vector "
         f"({c['answered']} of {c['n']} pairs get an answer). Jev figures: `coverage_labels/REPORT.md`, \"By category\" and the pooled line.\n"]
    o.append(_table(["", "fallback (target absent)", "Jev (tau_cover 0.65)"], [
        ["covered pairs credited", _frac(c["credited"], c["should"]), f"{jc['covered']}/{jc['should']}"],
        ["not-covered pairs called covered (false covers)", f"{c['false_covers']}/{c['other']} (cannot occur)", f"{jc['false_covers']}/{jc['other']}"],
    ]))
    o.append("")
    rows = []
    for name, v in c["categories"].items():
        rows.append([f"`{name}`", v["n"], v["should"], v["credited"], jc["called_covered"].get(name, "-")])
    o.append(_table(["category", "pairs", "covered", "fallback credits", "Jev calls covered"], rows))
    o.append("")
    o.append("The literal `coverage` target stays: by construction of the labelled set the `literal` pairs share the requirement's own "
             "words with the bullet (the literal target sees them) and the `semantic` pairs share none (it cannot). The pairs carry no "
             "posting `terms`, so the literal target is not scored here and no number stands in for it.")
    return "\n".join(o)


def block_memory_gate(m, j, t) -> str:
    g, ctx, jg = m["memory_gate"]["main"], m["memory_gate"]["context"], j["memory_gate"]
    jc = jg["context"]
    o = [f"{g['n']} messages ({g['prefs']} standing preferences, {g['other']} not) and {ctx['n']} context messages. With Jev off the "
         "prefilter alone decides: a non-candidate is dropped, every candidate goes to the host with the gate's cue words, "
         f"and nothing is written (routes: {', '.join(f'{k} {v}' for k, v in g['reasons'].items())}). "
         "Jev figures: `memory_gate_labels/REPORT.md`, \"Gate precision and recall\" and \"Automatic writes\".\n"]
    o.append(_table(["", "fallback (prefilter)", "Jev (gate at TAU_LO/TAU_HI)"], [
        ["**wrong automatic writes (the harmful error)**", f"**{g['writes']}** (it writes nothing)", f"**{jg['wrong_writes']}** of {jg['writes']} writes"],
        ["automatic writes", g["writes"], jg["writes"]],
        ["standing preferences kept (host or store)", _frac(g["prefs_kept"], g["prefs"]), _frac(jg["kept"]["tp"], g["prefs"])],
        ["standing preferences dropped (never reach the host)", g["prefs_dropped"], jg["kept"]["fn"]],
        ["non-preferences sent to the host", g["other_to_host"], jg["kept"]["fp"]],
        ["messages the host has to confirm", g["host"], f"{jg['kept']['tp'] + jg['kept']['fp'] - jg['writes']} "
                                                       f"({jg['kept']['tp'] + jg['kept']['fp']} kept - {jg['writes']} written)"],
    ]))
    o.append("")
    o.append(f"The {g['prefs_dropped']} dropped preferences are the prefilter's, which Jev sits behind: "
             + ", ".join(f"`{i}`" for i in g["dropped_ids"]) + ". Jev does not recover them. "
             f"On the {ctx['n']} context messages (a reference to the previous assistant turn, which the fallback never reads) the "
             f"prefilter keeps {_frac(ctx['prefs_kept'], ctx['prefs'])} preferences, drops none below a threshold, and sends "
             f"{ctx['other_to_host']} of {ctx['other']} non-preferences to the host ({ctx['host']} messages in all); Jev "
             f"(`context/REPORT.md`) keeps {jc['kept_v1']} preferences asked alone (`memory_gate@v1`) and {jc['kept_v2']} with the turn "
             f"(`@v2`) and hands the host {jc['handed_v1']} and {jc['handed_v2']}, with {jc['writes_v1']} automatic writes either way.")
    return "\n".join(o)


def _lib_rows(m_key: str, m, j, bad_name: str) -> str:
    mm, jj = m[m_key], j[m_key]
    f, k = mm["fallback"], mm["constant"]
    bad = "poor" if m_key == "variant_choice" else "wrong"
    other = lambda t: t["wrong"]                  # noqa: E731  a poor-fit pick is counted apart from the other wrong ones
    head = ["", "correct", "picks made", "right picks", f"{bad_name} picks"] + (["other wrong picks"] if bad == "poor" else []) + ["lost"]

    def line(name, t):
        return [name, _frac(t["correct"], t["n"]), t["picks"], t["right_picks"], t[bad]] + ([other(t)] if bad == "poor" else []) + [t["lost"]]
    jr = jj["jev"]
    jline = ["Jev at the threshold 0.50", jr["correct"], jr["picks"], jr["right"], jr["bad"]] \
        + ([jr["picks"] - jr["right"] - jr["bad"]] if bad == "poor" else []) + [jr["lost"]]
    const_name = "constant: always no_match" if bad == "poor" else "constant: always none"
    z = BEFORE_352[m_key]
    before = {"n": f["n"], "correct": z["correct"], "picks": z["picks"], "right_picks": z["right"], "poor": z["poor"],
              "wrong": z["wrong"], "lost": z["lost"]}
    before_name = "before #352 (#229 word overlap)" if bad == "poor" else "before #352 (#229 title lookup)"
    return _table(head, [line("**the Jev-free fallback (#352)**", f), line(before_name, before), jline, line(const_name, k)])


def block_variant(m, j, t) -> str:
    mm, jj = m["variant_choice"], j["variant_choice"]
    o = [f"{mm['fallback']['n']} cases. The fallback is the concept-fit pick of #352 (`harness.library.best_variant` over `harness/concepts.py`, "
         "floor `library.variant_match_floor`), run exactly as `fit_library_thresholds.py` runs it; #229's word overlap (floor 0.10) ran before. "
         "The harmful error is a poor-fit pick (the label names the variants that do not fit). "
         "Jev figures: `library_labels/REPORT.md`, \"Variant choice\". Its vocabulary was written with these labels in view: "
         "`library_labels/FALLBACK_FIT.md` sets leave-one-out and an author-written holdout beside this fit.\n",
         _lib_rows("variant_choice", m, j, "poor-fit"), ""]
    rep, f = jj["report_fallback"], mm["fallback"]
    o.append(f"The fallback row above equals the one in `library_labels/REPORT.md` ({rep['correct']}, {rep['picks']} picks, "
             f"{rep['right']} right, {rep['bad']} poor-fit, {rep['lost']} lost); the test pins it.\n")
    rows = [[f"`{c}`", v["n"], f"{v['correct']}/{v['n']}", v["poor"], jj["categories"][c]["jev_correct"], jj["categories"][c]["jev_bad"]]
            for c, v in mm["categories"].items()]
    o.append(_table(["category", "cases", "fallback correct", "fallback poor-fit", "Jev correct", "Jev poor-fit"], rows))
    return "\n".join(o)


def block_baseline(m, j, t) -> str:
    mm, jj = m["track_baseline"], j["track_baseline"]
    o = [f"{mm['fallback']['n']} cases. The fallback is the duties-based pick of #352 (`harness.library.pick_track`): the saved track whose "
         "role family best fits the job's top requirements and title, when it clears the fitted floor and leads every other family "
         "by the fitted margin, else none; #229's title lookup (`agents.job_card.role_family_from_title`) ran before and still runs for a job "
         "with no requirements. A host-stated `role_family` wins before either is asked and is not scored here. The harmful error is a "
         "wrong track. Jev figures: `library_labels/REPORT.md`, \"Track baseline\". The vocabulary was written with these labels in view: "
         "`library_labels/FALLBACK_FIT.md` sets leave-one-out and an author-written holdout beside this fit.\n",
         _lib_rows("track_baseline", m, j, "wrong"), ""]
    rep = jj["report_fallback"]
    o.append(f"The fallback row above equals the one in `library_labels/REPORT.md` ({rep['correct']}, {rep['picks']} picks, "
             f"{rep['right']} right, {rep['bad']} wrong, {rep['lost']} lost); the test pins it.\n")
    rows = [[f"`{c}`", v["n"], f"{v['correct']}/{v['n']}", v["wrong"], jj["categories"][c]["jev_correct"], jj["categories"][c]["jev_bad"]]
            for c, v in mm["categories"].items()]
    o.append(_table(["category", "cases", "fallback correct", "fallback wrong", "Jev correct", "Jev wrong"], rows))
    return "\n".join(o)


BLOCKS = {"summary": block_summary, "targets": block_targets, "support": block_support,
          "negative_pin": block_negative_pin, "coverage": block_coverage, "memory_gate": block_memory_gate,
          "variant_choice": block_variant, "track_baseline": block_baseline}


def render_blocks(m: Optional[Dict[str, Any]] = None, j: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    m = m or measure()
    j = j or jev_figures()
    t = targets(m, j)
    return {name: fn(m, j, t) for name, fn in BLOCKS.items()}


def _marks(name: str):
    return f"<!-- {BLOCK_PREFIX}:begin {name} -->", f"<!-- {BLOCK_PREFIX}:end {name} -->"


def committed_blocks(text: str) -> Dict[str, str]:
    """`{name: body}` for every generated block in `text`."""
    out = {}
    for name in BLOCKS:
        begin, end = _marks(name)
        if begin in text and end in text:
            out[name] = text.split(begin, 1)[1].split(end, 1)[0].strip("\n")
    return out


def splice(text: str, blocks: Dict[str, str]) -> str:
    for name, body in blocks.items():
        begin, end = _marks(name)
        if begin not in text or end not in text:
            raise SystemExit(f"{DOC_PATH.name}: missing the {name} block markers ({begin})")
        head, rest = text.split(begin, 1)
        _old, tail = rest.split(end, 1)
        text = f"{head}{begin}\n{body}\n{end}{tail}"
    return text


# ── commands ─────────────────────────────────────────────────────────────────

def _prepare() -> None:
    _pin_store(Path(tempfile.mkdtemp(prefix="art_fallback_benchmark_")))


def cmd_write(args) -> int:
    _prepare()
    path = Path(args.doc)
    path.write_text(splice(path.read_text(encoding="utf-8"), render_blocks()), encoding="utf-8")
    print(f"wrote the generated tables in {path}")
    return 0


def cmd_check(args) -> int:
    _prepare()
    path = Path(args.doc)
    text = path.read_text(encoding="utf-8")
    fresh, have = render_blocks(), committed_blocks(text)
    stale = [n for n in fresh if have.get(n) != fresh[n]]
    if stale:
        print(f"{path.name} is stale: {', '.join(stale)}. Run `python eval/fallback_benchmark.py write`.", file=sys.stderr)
        return 1
    print(f"{path.name} is current ({len(fresh)} generated blocks)")
    return 0


def cmd_json(args) -> int:
    _prepare()
    print(json.dumps(measure(), indent=2, sort_keys=True))
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, fn in (("write", cmd_write), ("check", cmd_check), ("json", cmd_json)):
        s = sub.add_parser(name)
        s.add_argument("--doc", default=str(DOC_PATH))
        s.set_defaults(fn=fn)
    args = p.parse_args(list(sys.argv[1:] if argv is None else argv))
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
