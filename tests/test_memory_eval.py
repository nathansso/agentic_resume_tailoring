"""
Memory quality evaluation harness for the chat agent context window and
compression pipeline.

Each YAML fixture defines a synthetic conversation, optional filler generation,
and a probe. The harness verifies:
  - factual_recall: key fact must appear in the context sent to the probe LLM
  - negative_recall: fact outside the token budget must not appear in the window

Integration (LLM-as-judge coherence) tests are gated with @pytest.mark.integration.

Results are written to tests/memory_evals/results/ after the session.
"""
import datetime
import json
import pytest
import yaml
from pathlib import Path

import agents.chat as chat_module
from conftest import _seed_user_and_skill

FIXTURE_DIR = Path(__file__).parent / "memory_evals"
RESULTS_DIR = FIXTURE_DIR / "results"

STRATEGIES = [
    pytest.param({"name": "default", "compress_at": 30, "keep": 8}, id="default"),
    pytest.param({"name": "aggressive", "compress_at": 10, "keep": 4}, id="aggressive"),
    pytest.param({"name": "no_compression", "compress_at": 9999, "keep": 8}, id="no_compression"),
]

FACTUAL_FIXTURES = [
    "skill_recall_across_compression.yaml",
    "job_title_recall.yaml",
    "user_preference_recall.yaml",
    "double_compression_recall.yaml",
]


# ── Helpers ────────────────────────────────────────────────────────────────────


def _load_fixture(name: str) -> dict:
    return yaml.safe_load((FIXTURE_DIR / name).read_text())


def _build_turns(fixture: dict) -> list[dict]:
    """Expand fixture turns + filler spec into a flat list of {role, content} dicts."""
    turns = list(fixture.get("turns", []))
    spec = fixture.get("filler", {})
    if spec:
        pairs = spec.get("pairs", 0)
        pad = "x" * spec.get("filler_chars", 0)
        user_msg = spec.get("user_content", "Please continue reviewing.") + pad
        asst_msg = spec.get("assistant_content", "Understood, continuing.") + pad
        for _ in range(pairs):
            turns.append({"role": "user", "content": user_msg})
            turns.append({"role": "assistant", "content": asst_msg})
    return turns


def _make_smart_llm(key_fact: str):
    """Return (llm_instance, captured_messages_list).

    Compression calls (single-message prompt asking to 'Summarize this conversation')
    return a canned summary containing key_fact. All other invoke calls (routing,
    probe) append their messages to captured and return a stub RESPONSE envelope.
    """
    captured: list[dict] = []

    class _Resp:
        def __init__(self, text: str):
            self.content = text

    class SmartLLM:
        def invoke(self, messages, **kw):
            first = messages[0] if messages else {}
            first_content = (
                first.get("content", "")
                if isinstance(first, dict)
                else getattr(first, "content", "")
            )
            if "Summarize this conversation" in first_content:
                return _Resp(f"Prior context: {key_fact}")
            for m in messages:
                if isinstance(m, dict):
                    captured.append({"role": m.get("role", ""), "content": m.get("content", "")})
                else:
                    captured.append({"role": getattr(m, "type", ""), "content": getattr(m, "content", "")})
            return _Resp("RESPONSE: captured")

    return SmartLLM(), captured


def _inject_and_compress(agent, turns: list[dict]) -> None:
    """Mirror chat()'s behavior: append each turn and trigger compression check after."""
    for turn in turns:
        agent.history.append({"role": turn["role"], "content": turn["content"]})
        agent._maybe_compress_history()


# ── Session-scoped results accumulator ────────────────────────────────────────


@pytest.fixture(scope="session")
def results_log():
    """Accumulates per-test results and writes a JSON report at session end."""
    results: list[dict] = []
    yield results
    if results:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
        report = {"timestamp": stamp, "results": results}
        (RESULTS_DIR / f"run_{stamp}.json").write_text(json.dumps(report, indent=2))


# ── Factual recall tests ───────────────────────────────────────────────────────


@pytest.mark.parametrize("strategy", STRATEGIES)
@pytest.mark.parametrize("fixture_name", FACTUAL_FIXTURES)
def test_factual_recall(isolated_engine, monkeypatch, results_log, strategy, fixture_name):
    """Key fact must appear somewhere in the messages sent to the probe LLM call."""
    fixture = _load_fixture(fixture_name)
    assert fixture["probe"]["metric"] == "factual_recall"

    monkeypatch.setattr(chat_module, "_COMPRESS_AT", strategy["compress_at"])
    monkeypatch.setattr(chat_module, "_COMPRESS_KEEP", strategy["keep"])

    smart_llm, captured = _make_smart_llm(fixture["key_fact"])
    monkeypatch.setattr(chat_module, "get_llm", lambda **kw: smart_llm)

    _seed_user_and_skill(isolated_engine)
    agent = chat_module.ChatAgent()

    _inject_and_compress(agent, _build_turns(fixture))
    agent.chat(fixture["probe"]["content"])

    context_text = " ".join(m["content"] for m in captured)
    expected = fixture["probe"]["expected_contains"]
    passed = expected in context_text

    results_log.append({
        "scenario": fixture["scenario"],
        "strategy": strategy["name"],
        "metric": "factual_recall",
        "passed": passed,
        "expected_contains": expected,
    })

    assert passed, (
        f"[{strategy['name']}] '{expected}' not found in probe context "
        f"for scenario '{fixture['scenario']}'.\n"
        f"Agent summary: {agent._job_summaries.get(None, '(none)')!r}\n"
        f"History length after setup: {len(agent.history)}\n"
        f"Captured context (first 400 chars): {context_text[:400]!r}"
    )


# ── Negative recall test ───────────────────────────────────────────────────────


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_negative_recall(isolated_engine, monkeypatch, results_log, strategy):
    """Fact placed before long filler messages must not appear in a constrained context window."""
    fixture = _load_fixture("budget_boundary_negative.yaml")
    assert fixture["probe"]["metric"] == "negative_recall"

    monkeypatch.setattr(chat_module, "_COMPRESS_AT", strategy["compress_at"])
    monkeypatch.setattr(chat_module, "_COMPRESS_KEEP", strategy["keep"])

    smart_llm, _ = _make_smart_llm(fixture["key_fact"])
    monkeypatch.setattr(chat_module, "get_llm", lambda **kw: smart_llm)

    _seed_user_and_skill(isolated_engine)
    agent = chat_module.ChatAgent()

    _inject_and_compress(agent, _build_turns(fixture))

    budget = fixture.get("budget_tokens", 800)
    window = agent._build_context_window(budget_tokens=budget)
    window_text = " ".join(m.get("content", "") for m in window)

    expected = fixture["probe"]["expected_contains"]
    passed = expected not in window_text

    results_log.append({
        "scenario": fixture["scenario"],
        "strategy": strategy["name"],
        "metric": "negative_recall",
        "passed": passed,
        "expected_not_contains": expected,
    })

    assert passed, (
        f"[{strategy['name']}] '{expected}' unexpectedly found in context window "
        f"(budget_tokens={budget}).\n"
        f"Window contents: {[m.get('content', '')[:80] for m in window]}"
    )
