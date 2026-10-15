"""The HTTP adapter: Vercel's Python runtime calls a `BaseHTTPRequestHandler`
subclass named `handler`, and the local dev server mounts the same class.

It reads the body, hands a `Request` to `Gateway`, and writes the JSON back.
It does not log anything: `BaseHTTPRequestHandler` would otherwise write each
request line to stderr.
"""
from __future__ import annotations

import json
import logging
import os
from http.server import BaseHTTPRequestHandler
from typing import Callable, Optional

from .config import Config
from .errors import ConfigError
from .service import MAX_BODY_BYTES, Gateway, Request, Response
from .store import InMemoryStore, UpstashStore
from .transport import urllib_transport

logger = logging.getLogger("artie_gateway")


def gateway_from_env(env=None) -> Gateway:
    """Build the production gateway. Anything unusable in the environment yields a
    gateway that answers 503 `not_configured`, rather than a crash."""
    env = os.environ if env is None else env
    try:
        config = Config.from_env(env)
    except ConfigError as exc:
        logger.error("gateway configuration is invalid: %s", exc)
        return _unconfigured()
    store = None
    if config.kv_url and config.kv_token:
        store = UpstashStore(config.kv_url, config.kv_token, urllib_transport)
    elif config.allow_memory_store:
        store = InMemoryStore()  # local development only: counters reset with the process
    else:
        logger.error("no KV store configured")
    return Gateway(config, store, urllib_transport)


def _unconfigured() -> Gateway:
    return Gateway(Config.from_env({}), None, urllib_transport)


_cached: Optional[Gateway] = None


def default_gateway() -> Gateway:
    global _cached
    if _cached is None:
        _cached = gateway_from_env()
    return _cached


class GatewayHandler(BaseHTTPRequestHandler):
    """Subclass and set `get_gateway`, or leave the default to read the environment."""

    get_gateway: Callable[[], Gateway] = staticmethod(default_gateway)  # type: ignore[assignment]
    protocol_version = "HTTP/1.0"  # one request per connection: an unread oversized body cannot stall the next

    def log_message(self, format, *args):  # noqa: A002 - stdlib signature
        pass

    def _client_ip(self) -> str:
        for name in ("x-vercel-forwarded-for", "x-real-ip", "x-forwarded-for"):
            value = self.headers.get(name)
            if value:
                return value.split(",")[0].strip()
        return self.client_address[0]

    def _send(self, response: Response) -> None:
        payload = json.dumps(response.body).encode()
        self.send_response(response.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        for name, value in response.headers.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(payload)

    def _serve(self, method: str) -> None:
        try:
            length = int(self.headers.get("content-length") or 0)
        except ValueError:
            length = 0
        # Read at most one byte past the limit: the gateway then answers 413.
        body = self.rfile.read(min(max(length, 0), MAX_BODY_BYTES + 1)) if length else b""
        headers = {k.lower(): v for k, v in self.headers.items()}
        request = Request(method, headers, body, self._client_ip())
        self._send(type(self).get_gateway().handle(request))

    def do_POST(self):  # noqa: N802
        self._serve("POST")

    def do_GET(self):  # noqa: N802
        self._serve("GET")

    do_PUT = do_DELETE = do_PATCH = do_GET
