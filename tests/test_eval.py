"""PRD 06 — chat eval harness regression tests."""
import agents.chat as chat_module
import tui.services as services_module


def test_scenario_loading():
    """All scenario JSON files load without error and each has required keys."""
    from verification.chat_eval.scenario_loader import load_all_scenarios

    scenarios = load_all_scenarios()
    assert len(scenarios) >= 1, "Expected at least one scenario file on disk"
    required = {"scenario_id", "canonical_turns", "success_conditions"}
    for s in scenarios:
        missing = required - set(s.keys())
        assert not missing, f"Scenario {s.get('scenario_id', '?')} missing keys: {missing}"


def test_scenario_seed_setup(isolated_engine):
    """seed_scenario_db creates the expected profile in the isolated test DB."""
    from verification.chat_eval.scenario_loader import load_scenario, seed_scenario_db

    scenario = load_scenario("github_plain_english_existing_profile")
    user = seed_scenario_db(scenario)

    assert user is not None
    assert user.email == "eval@test.local"
    assert user.github_username == "testuser"


def test_stubbed_eval_execution(isolated_engine):
    """EvalRunner completes a scenario without network calls when stub=True."""
    from verification.chat_eval.runner import EvalRunner
    from verification.chat_eval.scenario_loader import seed_scenario_db

    scenario = {
        "scenario_id": "test_inline_help",
        "profile_fixture": {"name": "Eval User", "email": "eval@test.local"},
        "canonical_turns": [{"user": "help"}],
        "initial_chat_history": [],
        "must_call_tools": [],
        "forbidden_tools": [],
        "required_response_substrings": [],
        "success_conditions": {
            "route_must_be_any": ["fast_path", "llm", "tool_call"],
        },
    }

    seed_scenario_db(scenario)
    turns = [t["user"] for t in scenario["canonical_turns"]]

    with EvalRunner(stub=True) as runner:
        result = runner.run_scenario(scenario, turns)

    assert result["scenario_id"] == "test_inline_help"
    assert "score" in result
    assert "traces" in result
    assert len(result["traces"]) >= 1
    assert result["score"]["passed"] is True


def test_scoring_tool_mismatch():
    """score_scenario_result classifies a forbidden-tool call as 'wrong_tool'."""
    from verification.chat_eval.scorer import score_scenario_result

    scenario = {
        "scenario_id": "wrong_tool_check",
        "forbidden_tools": ["run_ingest_resume"],
        "must_call_tools": [],
        "required_response_substrings": [],
        "success_conditions": {"forbidden_tools_not_called": True},
    }
    traces = [
        {
            "route_kind": "tool_call",
            "tool_calls_executed": ["run_ingest_resume"],
            "response_text": "Resume ingested",
            "duration_ms": 10.0,
        }
    ]
    score = score_scenario_result(scenario, traces)
    assert score["passed"] is False
    assert "wrong_tool" in score["failure_labels"]


def test_handoff_generation():
    """build_handoff_markdown includes section headings, FAIL label, and scenario ID."""
    from verification.chat_eval.artifacts import build_handoff_markdown

    results = [
        {
            "scenario_id": "my_failing_scenario",
            "turns": ["ingest github"],
            "traces": [
                {
                    "user_message": "ingest github",
                    "response_text": "Something went wrong",
                    "route_kind": "error",
                    "tool_calls_executed": [],
                }
            ],
            "score": {"passed": False, "failure_labels": ["tool_wrapper_failure"]},
        }
    ]

    md = build_handoff_markdown(results)
    assert "Failing Scenarios" in md
    assert "my_failing_scenario" in md
    assert "FAIL" in md
    assert "tool_wrapper_failure" in md


def test_tui_logging_opt_in(isolated_engine, monkeypatch):
    """Trace sink is created only when ART_LOG_CHAT_EVAL=1; absent by default."""
    import os
    from verification.chat_eval import artifacts as eval_artifacts

    sink_created = []

    def fake_make_sink(output_dir=None):
        sink_created.append(True)
        return lambda trace: None

    monkeypatch.setattr(eval_artifacts, "make_live_session_sink", fake_make_sink)

    monkeypatch.delenv("ART_LOG_CHAT_EVAL", raising=False)
    trace_sink = None
    if os.environ.get("ART_LOG_CHAT_EVAL") == "1":
        trace_sink = eval_artifacts.make_live_session_sink()
    agent_no_flag = chat_module.ChatAgent(trace_sink=trace_sink)
    assert agent_no_flag._trace_sink is None
    assert len(sink_created) == 0

    monkeypatch.setenv("ART_LOG_CHAT_EVAL", "1")
    trace_sink = None
    if os.environ.get("ART_LOG_CHAT_EVAL") == "1":
        trace_sink = eval_artifacts.make_live_session_sink()
    agent_with_flag = chat_module.ChatAgent(trace_sink=trace_sink)
    assert agent_with_flag._trace_sink is not None
    assert len(sink_created) == 1
