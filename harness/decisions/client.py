"""The TypeSafe Jev HTTP client (#193).

`POST https://api.typesafe.ai/v1/systemone` with `Authorization: Bearer <key>`
and `{state, model, questions}`; the response is `{model, answers, usage}`.

- **Transport is injectable**: a callable `(url, headers, payload, timeout) ->
  (status, body)`. Tests pass a fake, so nothing here touches the network in
  the suite.
- **Retries only what the API says to retry.** 429 and 529 back off
  exponentially, bounded (`max_retries`, `max_delay`); 401 and 422 are the
  caller's problem and raise at once. Nothing else is retried.
- **The model is pinned** (`jev-1.13.0`, override with `ART_JEV_MODEL`).
  `jev-latest` moves with releases, and a moving model would silently change
  every cached answer's meaning. The response reports the version that
  actually answered; the cache stores that.
- **The key** comes from `TYPESAFE_API_KEY`, else the OS keyring when the
  optional `keyring` package is installed. It is never logged, stored, put in
  an error, or shown by `repr`.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Mapping, Optional, Tuple

from harness.decisions.questions import Question, canonical, estimate_tokens

log = logging.getLogger(__name__)

DEFAULT_URL = "https://api.typesafe.ai/v1/systemone"
DEFAULT_MODEL = "jev-1.13.0"
KEY_ENV, MODEL_ENV = "TYPESAFE_API_KEY", "ART_JEV_MODEL"
KEYRING_SERVICE, KEYRING_USER = "art-mcp", "typesafe-api-key"

RETRY_STATUS = frozenset({429, 529})
# Documented limits: the state plus all questions, and the state plus the
# longest single question.
MAX_REQUEST_TOKENS = 64_000
MAX_QUESTION_TOKENS = 32_000

Transport = Callable[[str, Dict[str, str], Dict[str, Any], float], Tuple[int, Any]]


class JevError(Exception):
    """A failed call. `code` is the HTTP status, or `timeout`, `network`,
    `bad_response`, `request_too_large`."""

    def __init__(self, code, message: str = "", error_type: Optional[str] = None):
        super().__init__(f"jev {code}: {message}" if message else f"jev {code}")
        self.code, self.message, self.error_type = code, message, error_type


class JevReplayMiss(RuntimeError):
    """Replay mode was asked for a decision the cache does not hold. Never
    answered by a live call or by the fallback: a miss is a stale recording."""


def configured_model() -> str:
    return (os.environ.get(MODEL_ENV) or "").strip() or DEFAULT_MODEL


def resolve_api_key() -> Optional[str]:
    """`TYPESAFE_API_KEY`, else the keyring entry, else None. Never logged."""
    key = (os.environ.get(KEY_ENV) or "").strip()
    if key:
        return key
    try:
        import keyring  # optional
        return (keyring.get_password(KEYRING_SERVICE, KEYRING_USER) or "").strip() or None
    except Exception:  # not installed, no backend, locked: all mean "no key"
        return None


def _requests_transport(url, headers, payload, timeout):
    import requests

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
    except requests.Timeout as exc:
        raise JevError("timeout", "the request timed out") from exc
    except requests.RequestException as exc:
        raise JevError("network", type(exc).__name__) from exc
    try:
        body = resp.json()
    except ValueError:
        body = {"message": (resp.text or "")[:200]}
    return resp.status_code, body


@dataclass
class JevResponse:
    model: str
    answers: Dict[str, Any]
    usage: Dict[str, Any] = field(default_factory=dict)


class JevClient:
    def __init__(self, api_key: str, *, model: Optional[str] = None, url: str = DEFAULT_URL,
                 timeout: float = 30.0, max_retries: int = 3, backoff: float = 0.5,
                 max_delay: float = 8.0, transport: Optional[Transport] = None,
                 sleep: Callable[[float], None] = time.sleep):
        if not api_key:
            raise ValueError("JevClient needs an API key")
        self._key = api_key
        self.model = model or configured_model()
        self.url, self.timeout = url, timeout
        self.max_retries, self.backoff, self.max_delay = max_retries, backoff, max_delay
        self._transport = transport or _requests_transport
        self._sleep = sleep

    def __repr__(self) -> str:
        return f"JevClient(model={self.model!r}, url={self.url!r})"

    def ask(self, state: Any, questions: Mapping[str, Question]) -> JevResponse:
        """One batched request: `state` once, every question answered independently."""
        payload = {"state": state, "model": self.model,
                   "questions": {qid: q.wire() for qid, q in questions.items()}}
        size = estimate_tokens(canonical(payload))
        if size > MAX_REQUEST_TOKENS:
            raise JevError("request_too_large",
                           f"about {size} tokens; the limit is {MAX_REQUEST_TOKENS}")
        headers = {"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"}
        for attempt in range(self.max_retries + 1):
            status, body = self._transport(self.url, headers, payload, self.timeout)
            if status == 200:
                return self._parse(body)
            body = body if isinstance(body, Mapping) else {}
            if status in RETRY_STATUS and attempt < self.max_retries:
                delay = min(self.backoff * (2 ** attempt), self.max_delay)
                log.info("jev %s, retrying in %.1fs (%d/%d)", status, delay,
                         attempt + 1, self.max_retries)
                self._sleep(delay)
                continue
            raise JevError(status, str(body.get("message") or body.get("detail") or "")[:300],
                           body.get("error_type"))
        raise AssertionError("unreachable")  # pragma: no cover

    @staticmethod
    def _parse(body: Any) -> JevResponse:
        if not isinstance(body, Mapping) or not isinstance(body.get("answers"), Mapping):
            raise JevError("bad_response", "no answers in the response")
        return JevResponse(model=str(body.get("model") or ""), answers=dict(body["answers"]),
                           usage=dict(body.get("usage") or {}))
