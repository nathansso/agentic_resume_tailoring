"""
Scenario scorer — objective, deterministic scoring based on traces and scenario contracts.
No LLM calls; all checks are derived from trace metadata.
"""
from typing import List


FAILURE_LABELS = {
    "missing_fast_path": "Expected a fast-path route but got LLM.",
    "wrong_tool": "A forbidden or incorrect tool was called.",
    "argument_parse_failure": "Tool was called with a missing or invalid argument.",
    "llm_prompt_gap": "LLM path returned unhelpful response; may be a prompt gap.",
    "response_clarity_failure": "Required substring not found in response.",
    "tool_wrapper_failure": "Tool wrapper raised an error during execution.",
}


def score_scenario_result(scenario: dict, traces: List[dict]) -> dict:
    """Score a scenario run. Returns a result dict with passed flag and failure labels."""
    failure_labels: List[str] = []
    conditions = scenario.get("success_conditions", {})
    forbidden_tools: List[str] = scenario.get("forbidden_tools", [])
    must_call_tools: List[str] = scenario.get("must_call_tools", [])
    required_substrings: List[str] = scenario.get("required_response_substrings", [])

    if not traces:
        return {"passed": False, "failure_labels": ["no_traces"], "metrics": {}}

    all_routes = [t.get("route_kind", "") for t in traces]
    all_executed: List[str] = []
    for t in traces:
        all_executed.extend(t.get("tool_calls_executed", []))

    all_responses = " ".join(t.get("response_text", "") for t in traces).lower()

    # Check route constraint
    route_must_be = conditions.get("route_must_be_any", [])
    if route_must_be and not any(r in route_must_be for r in all_routes):
        failure_labels.append("missing_fast_path")

    # Check forbidden tools
    if conditions.get("forbidden_tools_not_called", False) or forbidden_tools:
        for tool in forbidden_tools:
            if tool in all_executed:
                failure_labels.append("wrong_tool")
                break

    # Check must-call tools
    for tool in must_call_tools:
        if tool not in all_executed:
            failure_labels.append("wrong_tool")

    # Check required substrings
    response_must_contain_any = conditions.get("response_must_contain_any", [])
    if response_must_contain_any and not any(
        s.lower() in all_responses for s in response_must_contain_any
    ):
        failure_labels.append("response_clarity_failure")

    if required_substrings:
        for s in required_substrings:
            if s.lower() not in all_responses:
                failure_labels.append("response_clarity_failure")
                break

    # Check for tool_wrapper_failure in traces
    for t in traces:
        if t.get("route_kind") == "error" or (t.get("error") is not None):
            failure_labels.append("tool_wrapper_failure")
            break

    # Dedupe labels
    failure_labels = list(dict.fromkeys(failure_labels))

    metrics = {
        "turns": len(traces),
        "route_distribution": _route_distribution(traces),
        "tools_executed": all_executed,
        "duration_ms_total": sum(t.get("duration_ms", 0) for t in traces),
    }

    return {
        "passed": len(failure_labels) == 0,
        "failure_labels": failure_labels,
        "metrics": metrics,
    }


def _route_distribution(traces: List[dict]) -> dict:
    dist: dict = {}
    for t in traces:
        k = t.get("route_kind", "unknown")
        dist[k] = dist.get(k, 0) + 1
    return dist
