"""
LLM Factory — returns the appropriate ChatModel based on config.
Supports Ollama (local, free) and OpenAI (cloud, paid) backends.
"""
import logging
from langchain_core.language_models.chat_models import BaseChatModel
from config import LLM_PROVIDER, OLLAMA_MODEL, OLLAMA_BASE_URL, MODEL_NAME, OPENAI_API_KEY

logger = logging.getLogger(__name__)


def get_llm(temperature: float = 0.0) -> BaseChatModel:
    """
    Returns a LangChain ChatModel based on the configured LLM_PROVIDER.

    Args:
        temperature: Sampling temperature (0.0 = deterministic, higher = more creative)
    """
    if LLM_PROVIDER == "ollama":
        from langchain_ollama import ChatOllama
        logger.info(f"Using Ollama model: {OLLAMA_MODEL} at {OLLAMA_BASE_URL}")
        return ChatOllama(
            model=OLLAMA_MODEL,
            base_url=OLLAMA_BASE_URL,
            temperature=temperature,
        )
    elif LLM_PROVIDER == "openai":
        from langchain_openai import ChatOpenAI
        logger.info(f"Using OpenAI model: {MODEL_NAME}")
        return ChatOpenAI(
            model=MODEL_NAME,
            temperature=temperature,
            api_key=OPENAI_API_KEY,
        )
    else:
        raise ValueError(f"Unknown LLM_PROVIDER: {LLM_PROVIDER}. Use 'ollama' or 'openai'.")
