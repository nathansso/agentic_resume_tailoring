"""
EvalRunner — executes scenario variants against ChatAgent and collects structured traces.

Usage:
    runner = EvalRunner(stub=True, output_dir=Path("/tmp/eval_out"))
    with runner:
        result = runner.run_scenario(scenario, turns)
    runner.write_artifacts([result])
"""
import contextlib
import logging
from pathlib import Path
from typing import List, Optional

from verification.chat_eval.scenario_loader import seed_scenario_db
from verification.chat_eval.scorer import score_scenario_result

logger = logging.getLogger(__name__)

# Services that get stubbed in --stubbed mode to avoid network/file I/O.
_STUB_MAP = {
    "tui.services": {
        "ingest_github": lambda username="": f"[STUBBED] GitHub ingested for {username}",
        "ingest_github_repo": lambda ref="": f"[STUBBED] Repo ingested: {ref}",
        "ingest_resume_file": lambda path="": f"[STUBBED] Resume ingested: {path}",
        "ingest_linkedin_pdf": lambda path="": f"[STUBBED] LinkedIn PDF ingested: {path}",
    },
    "agents.chat": {
        "run_tailor": lambda job="": f"[STUBBED] Tailoring complete for: {job}",
    },
}


class EvalRunner:
    """Runs scenario variants, collects traces, and writes artifacts."""

    def __init__(self, stub: bool = True, output_dir: Optional[Path] = None):
        self.stub = stub
        self.output_dir = output_dir or (Path.home() / ".art" / "evals")
        self._originals: list = []

    def __enter__(self):
        if self.stub:
            self._apply_stubs()
        return self

    def __exit__(self, *_):
        self._restore_stubs()

    def _apply_stubs(self) -> None:
        """Patch service modules with deterministic fakes."""
        import importlib
        for module_name, patches in _STUB_MAP.items():
            try:
                mod = importlib.import_module(module_name)
                for attr, stub_fn in patches.items():
                    original = getattr(mod, attr, None)
                    if original is not None:
                        self._originals.append((mod, attr, original))
                        setattr(mod, attr, stub_fn)
            except ImportError:
                logger.debug("Could not import %s for stubbing", module_name)

    def _restore_stubs(self) -> None:
        for mod, attr, original in self._originals:
            setattr(mod, attr, original)
        self._originals.clear()

    def run_scenario(self, scenario: dict, turns: List[str]) -> dict:
        """Run one variant of a scenario. Returns a result dict with traces and score."""
        traces: List[dict] = []

        def sink(trace: dict) -> None:
            traces.append(dict(trace))

        from agents.chat import ChatAgent
        agent = ChatAgent(trace_sink=sink, session_id=scenario["scenario_id"])

        # Inject initial history if specified (e.g. bot already asked a question).
        for msg in scenario.get("initial_chat_history", []):
            agent.history.append({"role": msg["role"], "content": msg["content"]})

        for turn in turns:
            try:
                agent.chat(turn)
            except Exception as exc:
                logger.error("Error during scenario %s turn %r: %s", scenario["scenario_id"], turn, exc)
                traces.append({
                    "session_id": agent._session_id,
                    "turn_index": agent._turn_index,
                    "user_message": turn,
                    "normalized_message": "",
                    "route_kind": "error",
                    "matched_fast_path": None,
                    "tool_calls_requested": [],
                    "tool_calls_executed": [],
                    "response_text": f"Error: {exc}",
                    "duration_ms": 0.0,
                    "llm_provider": "unknown",
                    "llm_role": "chat",
                    "error": str(exc),
                })

        score = score_scenario_result(scenario, traces)

        return {
            "scenario_id": scenario["scenario_id"],
            "turns": turns,
            "traces": traces,
            "score": score,
        }

    def write_artifacts(self, results: List[dict], run_id: Optional[str] = None) -> Path:
        """Write transcript.jsonl, summary.json, and claude_handoff.md to the output directory."""
        from datetime import datetime
        from verification.chat_eval.artifacts import append_turn, write_summary, write_handoff

        ts = run_id or datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        run_dir = self.output_dir / ts
        run_dir.mkdir(parents=True, exist_ok=True)

        for result in results:
            for trace in result.get("traces", []):
                append_turn(run_dir, trace, result["scenario_id"])

        write_summary(run_dir, results)
        write_handoff(run_dir, results)

        return run_dir
