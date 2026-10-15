"""LinkedIn profile URL validation.

The gateway fetches public profiles only: `linkedin.com/in/<slug>`. The output
form matches `LinkedInIngestor._normalize_url` in `ingestion/linkedin.py`
(`https://www.linkedin.com/in/<handle>`); `tests/test_gateway_contract.py`
in the repo keeps the two in step. Unlike the ingestor, a bare handle is not
accepted: the gateway wants a URL, and everything else is a 400.
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

from .errors import GatewayError

_PATH = re.compile(r"/in/([\w%-]{2,100})/?")
_COUNTRY_HOST = re.compile(r"[a-z]{2,3}\.linkedin\.com")
_MAX_LENGTH = 300


def _bad() -> GatewayError:
    return GatewayError(
        400,
        "invalid_url",
        "Send a LinkedIn profile URL of the form https://www.linkedin.com/in/<name>.",
    )


def normalize_profile_url(raw: Any) -> str:
    """Return `https://www.linkedin.com/in/<slug>`, or raise a 400."""
    if not isinstance(raw, str):
        raise _bad()
    text = raw.strip()
    if not text or len(text) > _MAX_LENGTH or any(c.isspace() for c in text):
        raise _bad()
    if "://" not in text:
        text = "https://" + text
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        raise _bad() from None
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or parts.username or parts.password or port:
        raise _bad()
    if host not in ("linkedin.com", "www.linkedin.com") and not _COUNTRY_HOST.fullmatch(host):
        raise _bad()
    match = _PATH.fullmatch(parts.path)
    if not match:
        raise _bad()
    return f"https://www.linkedin.com/in/{match.group(1).lower()}"
