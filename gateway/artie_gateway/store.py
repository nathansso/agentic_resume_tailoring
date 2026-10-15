"""Counter storage behind a small interface.

What the gateway keeps, in full: quota and rate counters (keyed by a hash or a
GitHub user id and a time bucket), a month's spend count, and a short cache
mapping a hashed token to a GitHub user id. Every key expires. No profile data
and no raw token ever reach the store.

`InMemoryStore` is for tests and local dev. `UpstashStore` speaks the Upstash
Redis REST API, which is what Vercel KV is.
"""
from __future__ import annotations

import json
import time
from typing import Callable, Dict, Optional, Protocol, Tuple

from .transport import Transport, TransportError, TransportTimeout


class StoreUnavailable(Exception):
    """The store could not be reached or answered something unusable."""


class Store(Protocol):
    def incr(self, key: str, ttl_seconds: int) -> int:
        """Add 1 (creating the key at 1), set its expiry, return the new count."""

    def decr(self, key: str) -> None:
        """Subtract 1, never below zero."""

    def get(self, key: str) -> Optional[str]: ...

    def set(self, key: str, value: str, ttl_seconds: int) -> None: ...


class InMemoryStore:
    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._data: Dict[str, Tuple[str, float]] = {}

    def _live(self, key: str) -> Optional[str]:
        entry = self._data.get(key)
        if entry is None:
            return None
        value, expires = entry
        if expires <= self._clock():
            del self._data[key]
            return None
        return value

    def incr(self, key: str, ttl_seconds: int) -> int:
        count = int(self._live(key) or 0) + 1
        self._data[key] = (str(count), self._clock() + ttl_seconds)
        return count

    def decr(self, key: str) -> None:
        current = self._live(key)
        if current is not None and int(current) > 0:
            self._data[key] = (str(int(current) - 1), self._data[key][1])

    def get(self, key: str) -> Optional[str]:
        return self._live(key)

    def set(self, key: str, value: str, ttl_seconds: int) -> None:
        self._data[key] = (value, self._clock() + ttl_seconds)

    def keys(self) -> list:
        return [k for k in list(self._data) if self._live(k) is not None]


class UpstashStore:
    def __init__(self, url: str, token: str, transport: Transport, timeout: float = 5.0) -> None:
        self._url = url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        self._transport = transport
        self._timeout = timeout

    def _post(self, path: str, payload) -> object:
        try:
            result = self._transport(
                "POST", self._url + path, self._headers, json.dumps(payload).encode(), self._timeout
            )
        except (TransportError, TransportTimeout):
            raise StoreUnavailable() from None
        if result.status != 200:
            raise StoreUnavailable()
        try:
            return json.loads(result.body)
        except ValueError:
            raise StoreUnavailable() from None

    def _command(self, *args) -> object:
        body = self._post("", list(args))
        if not isinstance(body, dict) or "error" in body:
            raise StoreUnavailable()
        return body.get("result")

    def incr(self, key: str, ttl_seconds: int) -> int:
        body = self._post("/pipeline", [["INCR", key], ["EXPIRE", key, ttl_seconds]])
        try:
            first = body[0]  # type: ignore[index]
            if "error" in first:
                raise StoreUnavailable()
            return int(first["result"])
        except (KeyError, IndexError, TypeError, ValueError):
            raise StoreUnavailable() from None

    def decr(self, key: str) -> None:
        # DECR on a missing key would create -1; only decrement a live counter.
        current = self.get(key)
        if current is not None and int(current) > 0:
            self._command("DECR", key)

    def get(self, key: str) -> Optional[str]:
        value = self._command("GET", key)
        return None if value is None else str(value)

    def set(self, key: str, value: str, ttl_seconds: int) -> None:
        self._command("SET", key, value, "EX", ttl_seconds)
