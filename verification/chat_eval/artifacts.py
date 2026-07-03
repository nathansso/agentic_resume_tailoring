"""
Eval artifact writers: JSONL transcript, summary JSON, and Claude-ready handoff markdown.
Also provides make_live_session_sink() for opt-in TUI session logging.
"""
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Callable, List


def _new_run_dir(base: Path | None = None) -> Path:
    run_id = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    base = base or (Path.home() / ".art" / "evals")
    run_dir = base / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def append_turn(run_dir: Path, trace: dict, scenario_id: str) -> None:
    """Append one structured trace as a JSON line to transcript.jsonl."""
    record = {**trace, "scenario_id": scenario_id}
    with open(run_dir / "transcript.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


def write_summary(run_dir: Path, results: List[dict]) -> None:
    """Write summary.json with scenario-level pass/fail metrics."""
    passed = sum(1 for r in results if r.get("score", {}).get("passed", False))
    failed = len(results) - passed
    summary = {
        "run_id": run_dir.name,
        "total": len(results),
        "passed": passed,
        "failed": failed,
        "scenarios": [
            {
                "scenario_id": r["scenario_id"],
                "passed": r.get("score", {}).get("passed", False),
                "failure_labels": r.get("score", {}).get("failure_labels", []),
                "turns": len(r.get("traces", [])),
                "route_distribution": _route_distribution(r.get("traces", [])),
            }
            for r in results
        ],
    }
    with open(run_dir / "summary.json", "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)


def write_handoff(run_dir: Path, results: List[dict]) -> None:
    """Write claude_handoff.md — failing scenarios first with transcript and fix hints."""
    lines: List[str] = [
        "# ART Chat Eval — Claude Handoff Report",
        f"\nRun: `{run_dir.name}`  |  Generated: {datetime.utcnow().isoformat()}Z",
        "",
    ]

    failures = [r for r in results if not r.get("score", {}).get("passed", False)]
    passing = [r for r in results if r.get("score", {}).get("passed", False)]

    lines.append(f"**{len(failures)} failing / {len(passing)} passing**\n")

    if failures:
        lines.append("## Failing Scenarios\n")
        for r in failures:
            _append_scenario_block(lines, r, failed=True)

    if passing:
        lines.append("## Passing Scenarios\n")
        for r in passing:
            _append_scenario_block(lines, r, failed=False)

    lines.append("## Reproduction\n")
    lines.append("```bash")
    lines.append("python cli.py chat-eval --mode canonical --variants 1 --stubbed")
    for r in failures:
        sid = r["scenario_id"]
        lines.append(f"python cli.py chat-eval --scenario {sid} --mode canonical --stubbed")
    lines.append("```\n")

    with open(run_dir / "claude_handoff.md", "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


def _append_scenario_block(lines: List[str], result: dict, failed: bool) -> None:
    sid = result["scenario_id"]
    score = result.get("score", {})
    labels = score.get("failure_labels", [])
    status = "FAIL" if failed else "PASS"

    lines.append(f"### [{status}] `{sid}`\n")
    if labels:
        lines.append(f"**Failure labels:** {', '.join(labels)}\n")

    lines.append("**Transcript:**\n")
    lines.append("```")
    for trace in result.get("traces", []):
        lines.append(f"User:  {trace.get('user_message', '')}")
        lines.append(f"Agent: {trace.get('response_text', '')[:200]}")
        lines.append(f"Route: {trace.get('route_kind', '?')}  tools_executed={trace.get('tool_calls_executed', [])}")
        lines.append("")
    lines.append("```\n")

    if failed:
        lines.append("**Likely owning files:** `agents/chat.py`, `services.py`\n")


def _route_distribution(traces: List[dict]) -> dict:
    dist: dict = {}
    for t in traces:
        k = t.get("route_kind", "unknown")
        dist[k] = dist.get(k, 0) + 1
    return dist


def build_handoff_markdown(results: List[dict]) -> str:
    """Return the handoff markdown as a string (used by tests without writing to disk)."""
    import io
    buf: List[str] = []
    failures = [r for r in results if not r.get("score", {}).get("passed", False)]
    passing = [r for r in results if r.get("score", {}).get("passed", False)]

    buf.append("# ART Chat Eval — Claude Handoff Report\n")
    buf.append(f"**{len(failures)} failing / {len(passing)} passing**\n")

    if failures:
        buf.append("## Failing Scenarios\n")
        for r in failures:
            _append_scenario_block(buf, r, failed=True)

    if passing:
        buf.append("## Passing Scenarios\n")
        for r in passing:
            _append_scenario_block(buf, r, failed=False)

    return "\n".join(buf)


def load_latest_prior_summary(output_dir: Path) -> "dict | None":
    """Return the second-most-recent run's summary.json, or None if no prior run exists.

    Run directories are named %Y%m%dT%H%M%SZ so lexicographic sort == chronological.
    The most-recent directory is the *current* run being written; we want the one before it.
    """
    candidates = sorted(
        [d for d in output_dir.iterdir() if d.is_dir() and (d / "summary.json").exists()],
        key=lambda d: d.name,
    )
    # Need at least two dirs: [prior, current]
    if len(candidates) < 2:
        return None
    prior_dir = candidates[-2]
    try:
        with open(prior_dir / "summary.json", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def make_live_session_sink(output_dir: Path | None = None) -> Callable[[dict], None]:
    """Return a trace sink for opt-in TUI session logging (ART_LOG_CHAT_EVAL=1)."""
    run_dir = _new_run_dir(output_dir)
    transcript_path = run_dir / "transcript.jsonl"

    def _redact(text: str) -> str:
        text = re.sub(r'\b[A-Za-z0-9+/]{40,}={0,2}\b', "[REDACTED]", text)
        return text

    def sink(trace: dict) -> None:
        safe = {
            **trace,
            "user_message": _redact(str(trace.get("user_message", ""))),
            "response_text": _redact(str(trace.get("response_text", ""))),
        }
        with open(transcript_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(safe) + "\n")

    return sink
