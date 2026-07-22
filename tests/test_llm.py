"""LLM factory tests."""

import pytest


def test_get_llm_roles(monkeypatch):
    """get_llm returns a BaseChatModel for each role without error (anthropic + openai)."""
    import llm as llm_module
    from langchain_core.language_models.chat_models import BaseChatModel

    class FakeModel(BaseChatModel):
        def _generate(self, *a, **kw): pass
        @property
        def _llm_type(self): return "fake"

    def fake_model(**kwargs):
        return FakeModel()

    import langchain_anthropic, langchain_openai
    monkeypatch.setattr(langchain_anthropic, "ChatAnthropic", fake_model)
    monkeypatch.setattr(langchain_openai, "ChatOpenAI", fake_model)

    for provider in ("anthropic", "openai"):
        monkeypatch.setattr(llm_module, "LLM_PROVIDER", provider)
        for role in ("chat", "extract", "tailor"):
            model = llm_module.get_llm(role=role)
            assert isinstance(model, BaseChatModel), (
                f"Expected BaseChatModel for provider={provider} role={role}"
            )


class TestProviderNormalization:
    """Regression cover for the `Unknown LLM_PROVIDER: "'anthropic'"` failure.

    A quoted value reaching os.environ (shell export, hosted secret store, or a
    .env written by dotenv's set_key and then re-exported) used to take down
    every LLM call. Normalisation happens at the read sites now.
    """

    def test_strips_surrounding_quotes(self):
        from config import normalize_provider
        assert normalize_provider("'anthropic'") == "anthropic"
        assert normalize_provider('"openai"') == "openai"

    def test_trims_whitespace_and_lowercases(self):
        from config import normalize_provider
        assert normalize_provider("  Anthropic \n") == "anthropic"
        assert normalize_provider(" 'OpenAI' ") == "openai"

    def test_blank_and_missing_fall_back_to_default(self):
        from config import normalize_provider
        assert normalize_provider(None) == "anthropic"
        assert normalize_provider("") == "anthropic"
        assert normalize_provider("''") == "anthropic"

    def test_get_llm_accepts_a_quoted_env_value(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "'anthropic'")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
        import llm
        # Previously raised ValueError("Unknown LLM_PROVIDER: \"'anthropic'\"").
        assert llm.get_llm() is not None

    def test_genuinely_unknown_provider_still_raises(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "gemini")
        import llm
        with pytest.raises(ValueError, match="Unknown LLM_PROVIDER"):
            llm.get_llm()
