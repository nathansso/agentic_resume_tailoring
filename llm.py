"""
LLM Factory — role-aware provider layer.
Each role (chat, extract, tailor) maps to its own model name from config.
Supports OpenAI (default) and Ollama (local fallback) via LLM_PROVIDER env var.
"""
import logging
from langchain_core.language_models.chat_models import BaseChatModel
from config import (
    LLM_PROVIDER,
    OLLAMA_MODEL, OLLAMA_BASE_URL,
    CHAT_MODEL, EXTRACT_MODEL, TAILOR_MODEL,
    OPENAI_API_KEY,
)

logger = logging.getLogger(__name__)


class ModelRole:
    CHAT = "chat"
    EXTRACT = "extract"
    TAILOR = "tailor"


_ROLE_MODELS = {
    ModelRole.CHAT: CHAT_MODEL,
    ModelRole.EXTRACT: EXTRACT_MODEL,
    ModelRole.TAILOR: TAILOR_MODEL,
}


def get_llm(role: str = ModelRole.CHAT, temperature: float = 0.0) -> BaseChatModel:
    """
    Return a LangChain ChatModel for the given role.

    Args:
        role: One of ModelRole.CHAT / EXTRACT / TAILOR (defaults to CHAT).
        temperature: Sampling temperature passed to the model.
    """
    if LLM_PROVIDER == "ollama":
        from langchain_ollama import ChatOllama
        logger.info(f"Using Ollama model: {OLLAMA_MODEL} at {OLLAMA_BASE_URL} (role={role})")
        return ChatOllama(
            model=OLLAMA_MODEL,
            base_url=OLLAMA_BASE_URL,
            temperature=temperature,
        )
    elif LLM_PROVIDER == "openai":
        from langchain_openai import ChatOpenAI
        model_name = _ROLE_MODELS.get(role, CHAT_MODEL)
        logger.info(f"Using OpenAI model: {model_name} (role={role})")
        return ChatOpenAI(
            model=model_name,
            temperature=temperature,
            api_key=OPENAI_API_KEY,
        )
    else:
        raise ValueError(f"Unknown LLM_PROVIDER: {LLM_PROVIDER!r}. Use 'openai' or 'ollama'.")
