"""
Human-readable Markdown report for chat eval runs.

Sections:
  1. Summary table       — Total / Passed / Failed / Pass Rate
  2. Failure label breakdown — aggregated across all scenarios
  3. Routing accuracy    — route counts and %
  4. Scenario results table — one row per scenario
  5. Regression / Fixed  — diff against a prior run's summary.json
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional


def build_report(
    results: List[dict],
    run_id: str,
    prior_summary: Optional[dict] = None,
) -> str:
    """Build a Markdown report string from eval results.

    Args:
        results:       Result dicts from EvalRunner.run_scenario().
        run_id:        Run identifier, e.g. "20260518T121214Z".
        prior_summary: Optional dict from a previous run's summary.json;
                       used for regression/fixed detection.

    Returns:
        Markdown string.
    """
    lines: List[str] = []

    lines.append("# ART Chat Eval Report")
    lines.append(f"**Run:** `{run_id}`")
    lines.append("")

    # ── 1. Summary table ─────────────────────────────────────────────────────
    total = len(results)
    passed = sum(1 for r in results if r.get("score", {}).get("passed", False))
    failed = total - passed
    pass_rate = f"{passed / total * 100:.1f}%" if total else "0.0%"

    lines.append("## Summary")
    lines.append("| Total | Passed | Failed | Pass Rate |")
    lines.append("|---|---|---|---|")
    lines.append(f"| {total} | {passed} | {failed} | {pass_rate} |")
    lines.append("")

    # ── 2. Failure label breakdown ────────────────────────────────────────────
    label_counts: dict[str, list[str]] = {}  # label -> [scenario_id, ...]
    for r in results:
        sid = r.get("scenario_id", "?")
        for label in r.get("score", {}).get("failure_labels", []):
            label_counts.setdefault(label, []).append(sid)

    lines.append("## Failure Label Breakdown")
    if label_counts:
        lines.append("| Label | Count | Scenarios |")
        lines.append("|---|---|---|")
        for label, sids in sorted(label_counts.items(), key=lambda x: -len(x[1])):
            lines.append(f"| {label} | {len(sids)} | {', '.join(sids)} |")
    else:
        lines.append("_(no failures)_")
    lines.append("")

    # ── 3. Routing accuracy ───────────────────────────────────────────────────
    route_totals: dict[str, int] = {}
    for r in results:
        for trace in r.get("traces", []):
            kind = trace.get("route_kind", "unknown")
            route_totals[kind] = route_totals.get(kind, 0) + 1

    total_turns = sum(route_totals.values())

    lines.append("## Routing Accuracy")
    if route_totals:
        lines.append("| Route | Turns | % |")
        lines.append("|---|---|---|")
        for route, count in sorted(route_totals.items(), key=lambda x: -x[1]):
            pct = f"{count / total_turns * 100:.1f}%" if total_turns else "0.0%"
            lines.append(f"| {route} | {count} | {pct} |")
    else:
        lines.append("_(no traces)_")
    lines.append("")

    # ── 4. Scenario results table ─────────────────────────────────────────────
    lines.append("## Scenario Results")
    lines.append("| Scenario | Status | Failure Labels | Turns | Routes |")
    lines.append("|---|---|---|---|---|")
    for r in results:
        sid = r.get("scenario_id", "?")
        score = r.get("score", {})
        is_passed = score.get("passed", False)
        status = "✅ PASS" if is_passed else "❌ FAIL"
        labels_str = ", ".join(score.get("failure_labels", [])) or "—"
        traces = r.get("traces", [])
        n_turns = len(traces)
        rdist: dict[str, int] = {}
        for t in traces:
            k = t.get("route_kind", "unknown")
            rdist[k] = rdist.get(k, 0) + 1
        routes_str = (
            ", ".join(f"{k}×{v}" for k, v in sorted(rdist.items(), key=lambda x: -x[1]))
            or "—"
        )
        lines.append(f"| {sid} | {status} | {labels_str} | {n_turns} | {routes_str} |")
    lines.append("")

    # ── 5. Regression / Fixed ─────────────────────────────────────────────────
    if prior_summary:
        prior_by_id = {s["scenario_id"]: s for s in prior_summary.get("scenarios", [])}
        current_by_id = {r["scenario_id"]: r for r in results}

        regressions: list[tuple[str, list[str]]] = []  # (sid, failure_labels)
        fixed: list[str] = []

        for sid, current in current_by_id.items():
            if sid not in prior_by_id:
                continue
            prior = prior_by_id[sid]
            now_passed = current.get("score", {}).get("passed", False)
            was_passed = prior.get("passed", False)
            if was_passed and not now_passed:
                labels = current.get("score", {}).get("failure_labels", [])
                regressions.append((sid, labels))
            elif not was_passed and now_passed:
                fixed.append(sid)

        lines.append("## Regressions Since Last Run")
        if regressions:
            for sid, labels in regressions:
                label_str = (
                    ", ".join(f"`{lb}`" for lb in labels) if labels else "unknown"
                )
                lines.append(f"- ❗ `{sid}` — was PASS, now FAIL ({label_str})")
        else:
            lines.append("_(none)_")
        lines.append("")

        lines.append("## Fixed Since Last Run")
        if fixed:
            for sid in fixed:
                lines.append(f"- ✅ `{sid}` — was FAIL, now PASS")
        else:
            lines.append("_(none)_")
        lines.append("")

    return "\n".join(lines)


def write_report(
    run_dir: Path,
    results: List[dict],
    prior_summary: Optional[dict] = None,
) -> Path:
    """Write report.md into run_dir and return its Path."""
    content = build_report(results, run_dir.name, prior_summary)
    report_path = run_dir / "report.md"
    with open(report_path, "w", encoding="utf-8") as fh:
        fh.write(content)
    return report_path
