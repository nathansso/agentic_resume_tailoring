"""LLM-as-judge scoring layer for the ART chat eval harness."""
import json
from pathlib import Path

_PROMPT = """\
You are an impartial evaluator. Score the AI assistant response below on three \
dimensions (1=poor, 3=excellent):
- helpfulness: Does the response address the user's intent?
- correctness: Is the content accurate and free of hallucination?
- conciseness: Is the response appropriately brief without losing necessary detail?

User message: {user_message}
Route kind: {route_kind}
AI response: {response_text}

Respond ONLY with a JSON object, no surrounding text:
{{"helpfulness": <1-3>, "correctness": <1-3>, "conciseness": <1-3>, "rationale": "<brief explanation>"}}"""

_SKIPPED = {"helpfulness": -1, "correctness": -1, "conciseness": -1, "rationale": "skipped"}


def judge_turn(user_message: str, response_text: str, route_kind: str) -> dict:
    """Score one turn on helpfulness, correctness, conciseness (1–3). Never raises."""
    if route_kind == "fast_path" or not response_text:
        return dict(_SKIPPED)
    try:
        import llm as _llm
        model = _llm.get_llm(role="eval", temperature=0.0)
        prompt = _PROMPT.format(
            user_message=user_message,
            route_kind=route_kind,
            response_text=response_text,
        )
        content = model.invoke(prompt).content.strip()
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
            content = content.strip()
        return json.loads(content)
    except Exception as exc:
        return {
            "helpfulness": 0,
            "correctness": 0,
            "conciseness": 0,
            "rationale": "judge_error",
            "error": str(exc),
        }


def score_transcript(transcript_path: Path, limit: int = 50) -> list[dict]:
    """Read transcript.jsonl and judge each turn offline. Never raises."""
    results = []
    try:
        with open(transcript_path, encoding="utf-8") as fh:
            for i, line in enumerate(fh):
                if i >= limit:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except Exception:
                    continue
                response_text = record.get("response_text", "")
                route_kind = record.get("route_kind", "")
                scores = judge_turn(
                    user_message=record.get("user_message", ""),
                    response_text=response_text,
                    route_kind=route_kind,
                )
                results.append({
                    "scenario_id": record.get("scenario_id", ""),
                    "turn_index": record.get("turn_index", i),
                    "user_message": record.get("user_message", ""),
                    "route_kind": route_kind,
                    "response_text": response_text,
                    "judge_scores": scores,
                })
    except Exception:
        pass
    return results
