"""Fakes for GitHub, Bright Data and the KV store. No test opens a socket."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import List, Optional

from artie_gateway.config import Config
from artie_gateway.service import Request
from artie_gateway.transport import HttpResult, TransportError, TransportTimeout

TOKEN = "gho_FAKETOKENsecretvalue1234567890"
SLUG = "jane-example-4242"
PROFILE_URL = f"https://www.linkedin.com/in/{SLUG}"
SECRET_KEY = "bd-secret-key-do-not-log"
RECORD = {
    "name": "Jane Example",
    "position": "Data Scientist",
    "about": "SENTINEL-ABOUT-TEXT private summary",
    "experience": [{"title": "Analyst", "company": "Acme"}],
}


@dataclass
class FakeNetwork:
    """A `Transport` that answers for GitHub and Bright Data, and records every call."""

    github_users: dict = field(default_factory=lambda: {TOKEN: 1001})
    github_failure: Optional[str] = None            # "timeout" | "unreachable" | "500"
    brightdata_response: Optional[HttpResult] = None
    brightdata_failure: Optional[str] = None        # "timeout" | "unreachable"
    calls: List[tuple] = field(default_factory=list)

    def __call__(self, method, url, headers, body, timeout) -> HttpResult:
        self.calls.append((method, url, dict(headers), body, timeout))
        if url.startswith("https://api.github.com/user"):
            if self.github_failure == "timeout":
                raise TransportTimeout()
            if self.github_failure == "unreachable":
                raise TransportError()
            if self.github_failure == "500":
                return HttpResult(500, b"github internal detail UPSTREAM-LEAK")
            bearer = headers.get("Authorization", "")[len("Bearer "):]
            if bearer in self.github_users:
                return HttpResult(200, json.dumps({"id": self.github_users[bearer], "login": "jane"}).encode())
            return HttpResult(401, b'{"message": "Bad credentials"}')
        if url.startswith("https://api.brightdata.com/datasets/v3/scrape"):
            if self.brightdata_failure == "timeout":
                raise TransportTimeout()
            if self.brightdata_failure == "unreachable":
                raise TransportError()
            if self.brightdata_response is not None:
                return self.brightdata_response
            return HttpResult(200, json.dumps([RECORD]).encode())
        raise AssertionError(f"unexpected network call: {method} {url}")

    def calls_to(self, host: str) -> list:
        return [c for c in self.calls if c[1].startswith(f"https://{host}")]


class Clock:
    def __init__(self, now: float = 1_760_000_000.0) -> None:  # 2025-10-09 08:53 UTC
        self.now = now

    def __call__(self) -> float:
        return self.now


def make_config(**overrides) -> Config:
    env = {"BRIGHTDATA_API_KEY": SECRET_KEY, "ART_GATEWAY_ALLOW_MEMORY_STORE": "1"}
    env.update(overrides)
    return Config.from_env(env)


def post(body=None, token: Optional[str] = TOKEN, ip: str = "203.0.113.7", raw: Optional[bytes] = None) -> Request:
    headers = {"content-type": "application/json"}
    if token is not None:
        headers["authorization"] = f"Bearer {token}"
    payload = raw if raw is not None else json.dumps(body if body is not None else {"url": PROFILE_URL}).encode()
    return Request("POST", headers, payload, ip)
