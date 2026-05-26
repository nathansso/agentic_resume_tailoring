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


def test_report_per_scenario_table():
    """build_report() output contains each scenario_id and PASS/FAIL symbols."""
    from verification.chat_eval.report import build_report

    results = [
        {
            "scenario_id": "scenario_alpha",
            "traces": [{"route_kind": "fast_path"}],
            "score": {"passed": True, "failure_labels": []},
        },
        {
            "scenario_id": "scenario_beta",
            "traces": [{"route_kind": "llm"}],
            "score": {"passed": False, "failure_labels": ["response_clarity_failure"]},
        },
    ]
    report = build_report(results, "20260101T000000Z")

    assert "scenario_alpha" in report
    assert "scenario_beta" in report
    assert "✅ PASS" in report
    assert "❌ FAIL" in report


def test_report_routing_accuracy():
    """Traces with known route kinds produce correct counts and % in the report."""
    from verification.chat_eval.report import build_report

    results = [
        {
            "scenario_id": "s1",
            "traces": [
                {"route_kind": "fast_path"},
                {"route_kind": "fast_path"},
                {"route_kind": "tool_call"},
            ],
            "score": {"passed": True, "failure_labels": []},
        }
    ]
    report = build_report(results, "run_x")

    assert "fast_path" in report
    assert "tool_call" in report
    # 2 fast_path out of 3 = 66.7%
    assert "66.7%" in report
    # 1 tool_call out of 3 = 33.3%
    assert "33.3%" in report


def test_report_failure_label_aggregation():
    """Two scenarios with the same label → count=2 and both IDs listed."""
    from verification.chat_eval.report import build_report

    results = [
        {
            "scenario_id": "s1",
            "traces": [],
            "score": {"passed": False, "failure_labels": ["response_clarity_failure"]},
        },
        {
            "scenario_id": "s2",
            "traces": [],
            "score": {"passed": False, "failure_labels": ["response_clarity_failure"]},
        },
    ]
    report = build_report(results, "run_y")

    assert "response_clarity_failure" in report
    # count = 2 appears in the table
    assert "| 2 |" in report
    assert "s1" in report
    assert "s2" in report


def test_report_regression_detection():
    """Scenario that was PASS in prior run and is now FAIL appears in Regressions."""
    from verification.chat_eval.report import build_report

    prior_summary = {
        "scenarios": [
            {"scenario_id": "flaky_scenario", "passed": True, "failure_labels": []},
        ]
    }
    results = [
        {
            "scenario_id": "flaky_scenario",
            "traces": [],
            "score": {"passed": False, "failure_labels": ["llm_prompt_gap"]},
        }
    ]
    report = build_report(results, "run_z", prior_summary=prior_summary)

    assert "Regressions" in report
    assert "flaky_scenario" in report
    assert "was PASS, now FAIL" in report
    assert "llm_prompt_gap" in report


def test_load_latest_prior_summary(tmp_path):
    """load_latest_prior_summary returns the second-most-recent run, not the current one."""
    import json as _json
    from verification.chat_eval.artifacts import load_latest_prior_summary

    # Write two fake run dirs — names must sort chronologically.
    older = tmp_path / "20260101T000000Z"
    newer = tmp_path / "20260102T000000Z"
    for d in (older, newer):
        d.mkdir()
        payload = {"run_id": d.name, "total": 1, "passed": 1, "failed": 0, "scenarios": []}
        with open(d / "summary.json", "w") as fh:
            _json.dump(payload, fh)

    result = load_latest_prior_summary(tmp_path)
    assert result is not None
    assert result["run_id"] == "20260101T000000Z"


def test_load_latest_prior_summary_no_prior(tmp_path):
    """Returns None when there is only one (current) run dir."""
    import json as _json
    from verification.chat_eval.artifacts import load_latest_prior_summary

    only_dir = tmp_path / "20260101T000000Z"
    only_dir.mkdir()
    with open(only_dir / "summary.json", "w") as fh:
        _json.dump({"run_id": only_dir.name, "scenarios": []}, fh)

    assert load_latest_prior_summary(tmp_path) is None


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
