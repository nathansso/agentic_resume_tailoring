"""Who is calling: verify a GitHub token and return the GitHub user id.

The id, not the login, is the key for every quota, because a login can be
renamed. Verification results are cached for a few minutes under a hash of the
token, so the raw token is never stored.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Optional

from .errors import GatewayError
from .store import Store
from .transport import Transport, TransportError, TransportTimeout

GITHUB_USER_URL = "https://api.github.com/user"
_BEARER = re.compile(r"Bearer ([A-Za-z0-9_\-]{10,255})", re.IGNORECASE)
_INVALID_TTL = 60  # a rejected token is remembered briefly, so retries do not reach GitHub
_INVALID = "invalid"


def _unauthorized() -> GatewayError:
    return GatewayError(
        401,
        "unauthorized",
        "Send a GitHub token from `artie github login` as 'Authorization: Bearer <token>'.",
        {"WWW-Authenticate": 'Bearer realm="artie-gateway"'},
    )


def parse_bearer(header: Optional[str]) -> str:
    match = _BEARER.fullmatch((header or "").strip())
    if not match:
        raise _unauthorized()
    return match.group(1)


class GitHubVerifier:
    def __init__(self, transport: Transport, store: Store, cache_seconds: int, timeout: float) -> None:
        self._transport = transport
        self._store = store
        self._cache_seconds = cache_seconds
        self._timeout = timeout

    def user_id(self, token: str) -> int:
        cache_key = "gh:" + hashlib.sha256(token.encode()).hexdigest()
        cached = self._store.get(cache_key)
        if cached == _INVALID:
            raise _unauthorized()
        if cached is not None:
            return int(cached)

        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "artie-gateway",
        }
        try:
            result = self._transport("GET", GITHUB_USER_URL, headers, None, self._timeout)
        except TransportTimeout:
            raise GatewayError(504, "github_timeout", "GitHub did not answer in time. Try again.") from None
        except TransportError:
            raise GatewayError(502, "github_unreachable", "Could not reach GitHub to check the token.") from None

        if result.status in (401, 403):
            self._store.set(cache_key, _INVALID, _INVALID_TTL)
            raise _unauthorized()
        if result.status != 200:
            raise GatewayError(502, "github_error", "GitHub could not check the token. Try again.")
        try:
            uid = json.loads(result.body)["id"]
        except (ValueError, KeyError, TypeError):
            raise GatewayError(502, "github_error", "GitHub could not check the token. Try again.") from None
        if not isinstance(uid, int) or isinstance(uid, bool) or uid <= 0:
            raise GatewayError(502, "github_error", "GitHub could not check the token. Try again.")
        self._store.set(cache_key, str(uid), self._cache_seconds)
        return uid
