"""LLM factory tests."""


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
