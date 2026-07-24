"""LangSmith tracing scaffold — off-by-default guarantees (issue #142).

The scaffold must never emit traces unless the operator explicitly opts in via
env vars, and importing config must not force tracing on as a side effect.
"""
import importlib

import config


def test_langsmith_disabled_when_env_unset(monkeypatch):
    monkeypatch.delenv("LANGCHAIN_TRACING_V2", raising=False)
    assert config.langsmith_enabled() is False


def test_langsmith_disabled_for_falsey_values(monkeypatch):
    for val in ("", "0", "false", "no", "off"):
        monkeypatch.setenv("LANGCHAIN_TRACING_V2", val)
        assert config.langsmith_enabled() is False, val


def test_langsmith_enabled_only_when_explicitly_truthy(monkeypatch):
    for val in ("1", "true", "TRUE", "yes", "on", "'true'"):
        monkeypatch.setenv("LANGCHAIN_TRACING_V2", val)
        assert config.langsmith_enabled() is True, val


def test_importing_config_does_not_force_tracing_on(monkeypatch):
    """Re-importing config with no tracing env must leave LANGCHAIN_TRACING_V2
    unset — the scaffold reads env, it never writes it."""
    monkeypatch.delenv("LANGCHAIN_TRACING_V2", raising=False)
    importlib.reload(config)
    try:
        import os
        assert os.environ.get("LANGCHAIN_TRACING_V2") is None
        assert config.langsmith_enabled() is False
    finally:
        importlib.reload(config)  # restore module state for other tests
