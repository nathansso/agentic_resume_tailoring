"""One exception type for every refusal the gateway can send.

The message is what the caller sees, so it is short, fixed text: never an
upstream response body, a token, a URL or anything from the request.
"""
from __future__ import annotations

from typing import Dict, Optional


class GatewayError(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        headers: Optional[Dict[str, str]] = None,
        reason: Optional[str] = None,
    ) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.message = message
        self.headers = headers or {}
        # Log-only diagnosis from a fixed vocabulary (e.g. `upstream_http_401`); never sent
        # to the caller and never built from request text or an upstream body.
        self.reason = reason


class ConfigError(Exception):
    """An environment variable holds a value that cannot be used."""
