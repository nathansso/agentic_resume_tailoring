"""
LLM Factory — role-aware provider layer.
Each role (chat, extract, tailor) maps to its own model name from config.
Supports Anthropic (default) and OpenAI via LLM_PROVIDER env var.
"""
import logging
from langchain_core.language_models.chat_models import BaseChatModel
from config import (
    LLM_PROVIDER,
    CHAT_MODEL, EXTRACT_MODEL, TAILOR_MODEL, EVAL_MODEL, REVIEW_MODEL,
    OPENAI_API_KEY, ANTHROPIC_API_KEY,
)

logger = logging.getLogger(__name__)


class ModelRole:
    CHAT = "chat"
    EXTRACT = "extract"
    TAILOR = "tailor"
    EVAL = "eval"
    REVIEW = "review"


_ROLE_MODELS = {
    ModelRole.CHAT: CHAT_MODEL,
    ModelRole.EXTRACT: EXTRACT_MODEL,
    ModelRole.TAILOR: TAILOR_MODEL,
    ModelRole.EVAL: EVAL_MODEL,
    ModelRole.REVIEW: REVIEW_MODEL,
}

# Models that have deprecated the temperature parameter entirely.
_ANTHROPIC_NO_TEMPERATURE = {"claude-opus-4-7"}


def get_llm(role: str = ModelRole.CHAT, temperature: float = 0.0) -> BaseChatModel:
    """
    Return a LangChain ChatModel for the given role.

    Reads LLM_PROVIDER and API keys from os.environ at call time so that keys
    saved via services.save_llm_config() take effect immediately without restart.
    Falls back to module-level config values when env vars are absent.

    Args:
        role: One of ModelRole.CHAT / EXTRACT / TAILOR (defaults to CHAT).
        temperature: Sampling temperature passed to the model.
    """
    import os
    from config import normalize_provider
    provider = normalize_provider(os.environ.get("LLM_PROVIDER")) or LLM_PROVIDER
    model_name = _ROLE_MODELS.get(role, CHAT_MODEL)

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        api_key = os.environ.get("ANTHROPIC_API_KEY") or ANTHROPIC_API_KEY
        logger.info(f"Using Anthropic model: {model_name} (role={role})")
        kwargs: dict = {"model": model_name, "api_key": api_key}
        if model_name not in _ANTHROPIC_NO_TEMPERATURE:
            kwargs["temperature"] = temperature
        return ChatAnthropic(**kwargs)
    elif provider == "openai":
        from langchain_openai import ChatOpenAI
        api_key = os.environ.get("OPENAI_API_KEY") or OPENAI_API_KEY
        logger.info(f"Using OpenAI model: {model_name} (role={role})")
        return ChatOpenAI(
            model=model_name,
            temperature=temperature,
            api_key=api_key,
        )
    else:
        raise ValueError(f"Unknown LLM_PROVIDER: {provider!r}. Use 'anthropic' or 'openai'.")


class StructuredExtractor:
    """Wrap a ``with_structured_output`` runnable with a bounded validation-retry.

    ``invoke`` returns a validated Pydantic model. On a validation/parse failure
    (or a transient provider error) it retries up to ``max_attempts`` times total,
    then re-raises the last exception — so every call site keeps its existing
    graceful-degrade (an extractor catches the error and returns ``[]`` rather
    than crashing ingestion). This is the single extraction seam #142 introduces;
    call sites no longer touch ``JsonOutputParser``.
    """

    def __init__(self, runnable, *, max_attempts: int = 2):
        self._runnable = runnable
        self._max_attempts = max(1, max_attempts)

    def invoke(self, messages):
        last_exc: Exception | None = None
        for attempt in range(self._max_attempts):
            try:
                result = self._runnable.invoke(messages)
                if result is None:
                    # with_structured_output can return None if the model emits no
                    # tool call; treat as a validation miss and retry.
                    raise ValueError("structured extractor returned no result")
                return result
            except Exception as exc:  # ValidationError, parse error, transient API
                last_exc = exc
                logger.warning(
                    "Structured extraction attempt %d/%d failed: %s",
                    attempt + 1, self._max_attempts, exc,
                )
        raise last_exc  # exhausted — caller degrades gracefully


def get_extractor(
    role: str = ModelRole.EXTRACT,
    schema=None,
    *,
    llm: BaseChatModel | None = None,
    temperature: float = 0.0,
    max_attempts: int = 2,
) -> StructuredExtractor:
    """Return a :class:`StructuredExtractor` for ``schema`` (a Pydantic model).

    Wraps ``get_llm(role).with_structured_output(schema)`` (LangChain-native,
    schema-validated output — chosen over Instructor so extraction stays on the
    LangChain path and is traced uniformly by the LangSmith scaffold). Pass an
    existing ``llm`` to reuse a model instance the caller already configured
    (e.g. an agent's ``self.llm``) instead of building a fresh one.
    """
    if schema is None:
        raise ValueError("get_extractor requires a Pydantic schema")
    base = llm if llm is not None else get_llm(role, temperature=temperature)
    return StructuredExtractor(
        base.with_structured_output(schema), max_attempts=max_attempts
    )
