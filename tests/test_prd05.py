"""PRD 05 — app-data directory, config validation, and secrets-safety tests."""
import logging


def test_validate_config_catches_missing_api_key(monkeypatch):
    """validate_config() returns an error when the active provider's API key is absent."""
    import config as cfg
    import config_validator

    monkeypatch.setattr(cfg, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(cfg, "OPENAI_API_KEY", "")
    # Clear os.environ so the validator doesn't pick up a real key from the shell.
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    errors = config_validator.validate_config()
    assert any("OPENAI_API_KEY" in e for e in errors), (
        f"Expected OPENAI_API_KEY error, got: {errors}"
    )


def test_validate_config_catches_missing_anthropic_key(monkeypatch):
    """validate_config() returns an error when ANTHROPIC_API_KEY is absent for anthropic provider."""
    import config as cfg
    import config_validator

    monkeypatch.setattr(cfg, "LLM_PROVIDER", "anthropic")
    monkeypatch.setattr(cfg, "ANTHROPIC_API_KEY", "")
    # Clear os.environ so the validator doesn't pick up a real key from the shell.
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    errors = config_validator.validate_config()
    assert any("ANTHROPIC_API_KEY" in e for e in errors), (
        f"Expected ANTHROPIC_API_KEY error, got: {errors}"
    )


def test_validate_config_rejects_unknown_provider(monkeypatch):
    """validate_config() returns an error for an unrecognised LLM_PROVIDER value."""
    import config as cfg
    import config_validator

    monkeypatch.setattr(cfg, "LLM_PROVIDER", "gpt-banana")
    # Override os.environ so the validator sees the bad provider, not the real one.
    monkeypatch.setenv("LLM_PROVIDER", "gpt-banana")

    errors = config_validator.validate_config()
    assert any("LLM_PROVIDER" in e for e in errors)


def test_ensure_app_dirs_creates_directories(tmp_path):
    """ensure_app_dirs(base_dir) creates exports/, uploads/, and logs/ subdirectories."""
    from config import ensure_app_dirs

    ensure_app_dirs(base_dir=tmp_path)

    assert (tmp_path / "exports").is_dir()
    assert (tmp_path / "uploads").is_dir()
    assert (tmp_path / "logs").is_dir()


def test_no_secrets_in_logs(monkeypatch, caplog):
    """API key values must not appear in log output during LLM initialisation."""
    import langchain_anthropic
    import config as cfg
    import llm as llm_module

    fake_key = "sk-ant-TESTSECRET99887766"
    monkeypatch.setattr(cfg, "LLM_PROVIDER", "anthropic")
    monkeypatch.setattr(cfg, "ANTHROPIC_API_KEY", fake_key)

    class _FakeModel:
        pass

    monkeypatch.setattr(langchain_anthropic, "ChatAnthropic", lambda **kw: _FakeModel())

    with caplog.at_level(logging.DEBUG):
        llm_module.get_llm(role="chat")

    assert fake_key not in caplog.text, (
        "API key value leaked into log output"
    )
