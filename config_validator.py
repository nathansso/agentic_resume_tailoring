"""Startup configuration validator for ART.

validate_config() returns a list of human-readable error strings.
An empty list means all checks passed.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_VALID_PROVIDERS = {"openai", "anthropic", "ollama"}


def validate_config() -> list[str]:
    """Check runtime configuration and return a list of error strings (empty = OK)."""
    import config as _cfg

    errors: list[str] = []

    # 1. Provider must be a known value.
    provider = _cfg.LLM_PROVIDER
    if provider not in _VALID_PROVIDERS:
        errors.append(
            f"LLM_PROVIDER must be one of {sorted(_VALID_PROVIDERS)}, got: {provider!r}"
        )

    # 2. API key must be present for the chosen cloud provider.
    if provider == "openai" and not _cfg.OPENAI_API_KEY:
        errors.append("OPENAI_API_KEY is not set (required when LLM_PROVIDER=openai)")

    if provider == "anthropic" and not _cfg.ANTHROPIC_API_KEY:
        errors.append("ANTHROPIC_API_KEY is not set (required when LLM_PROVIDER=anthropic)")

    # 3. Ollama base URL must be reachable.
    if provider == "ollama":
        try:
            import requests
            requests.get(_cfg.OLLAMA_BASE_URL, timeout=2)
        except Exception:
            errors.append(f"Ollama is not reachable at {_cfg.OLLAMA_BASE_URL!r}")

    # 4. APP_DATA_DIR must be writable.
    try:
        _cfg.APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
        _probe = _cfg.APP_DATA_DIR / ".art_write_probe"
        _probe.write_text("x")
        _probe.unlink()
    except Exception as exc:
        errors.append(f"APP_DATA_DIR is not writable ({_cfg.APP_DATA_DIR}): {exc}")

    return errors
