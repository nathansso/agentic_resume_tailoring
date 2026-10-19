"""The gateway's request pipeline, with no HTTP framework in it.

Order of checks, cheapest first:

1. method, then the service's own configuration (503 if the Bright Data key
   or the KV store is missing)
2. per-IP rate limit (429), before anything reaches GitHub
3. Authorization header shape (401), then the body and the profile URL (400)
4. GitHub token check (401, or 502/504 when GitHub is down)
5. per-user daily quota (429)
6. global monthly spend cap (503)
7. the Bright Data call; a failure there refunds the user's quota but not the
   spend count, so the cap stays on the safe side

Nothing from the request body, the token or the profile record is logged. The
single log line per request carries an outcome code, a status and a duration.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Dict, Mapping, Optional

from .brightdata import BrightDataClient
from .config import Config
from .errors import GatewayError
from .github import GitHubVerifier, parse_bearer
from .store import Store, StoreUnavailable
from .transport import Transport
from .urls import normalize_profile_url

logger = logging.getLogger("artie_gateway")

MAX_BODY_BYTES = 4096


@dataclass
class Request:
    method: str
    headers: Mapping[str, str]  # lower-case names
    body: bytes
    client_ip: str


@dataclass
class Response:
    status: int
    body: Dict[str, object]
    headers: Dict[str, str] = field(default_factory=dict)


def _error(err: GatewayError) -> Response:
    return Response(
        err.status, {"error": {"code": err.code, "message": err.message}}, dict(err.headers)
    )


class Gateway:
    def __init__(
        self,
        config: Config,
        store: Optional[Store],
        transport: Transport,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self.store = store
        self._clock = clock
        self._github = None
        self._brightdata = None
        if store is not None:
            self._github = GitHubVerifier(
                transport, store, config.auth_cache_seconds, config.github_timeout
            )
        if config.brightdata_api_key:
            self._brightdata = BrightDataClient(
                config.brightdata_api_key,
                config.brightdata_dataset_id,
                transport,
                config.upstream_timeout,
                clock=clock,
                sleep=sleep,
            )

    # -- entry point ---------------------------------------------------------

    def handle(self, request: Request) -> Response:
        started = self._clock()
        reason = "-"
        try:
            response = self._handle(request)
            outcome = "ok" if response.status == 200 else str(response.body["error"]["code"])  # type: ignore[index]
        except GatewayError as err:
            response, outcome = _error(err), err.code
            reason = err.reason or "-"
        except StoreUnavailable:
            err = GatewayError(503, "unavailable", "The service is temporarily unavailable. Try again later.")
            response, outcome = _error(err), err.code
        except Exception as exc:  # noqa: BLE001 - last resort; log the type only, never the message
            err = GatewayError(500, "internal", "Something went wrong on our side.")
            response, outcome = _error(err), f"internal:{type(exc).__name__}"
        # Fixed fields only: no URL, token, user id, record or request text. `reason` comes
        # from a fixed vocabulary (an upstream HTTP status or a sanitised record code).
        logger.info(
            "linkedin request outcome=%s status=%d ms=%d reason=%s",
            outcome,
            response.status,
            int((self._clock() - started) * 1000),
            reason,
        )
        return response

    # -- pipeline ------------------------------------------------------------

    def _handle(self, request: Request) -> Response:
        if request.method != "POST":
            raise GatewayError(405, "method_not_allowed", "Use POST.", {"Allow": "POST"})
        if self.store is None or self._github is None or self._brightdata is None:
            raise GatewayError(503, "not_configured", "The service is not set up yet.")

        now = self._clock()
        self._limit_ip(request.client_ip, now)

        token = parse_bearer(request.headers.get("authorization"))
        profile_url = self._profile_url(request)
        user_id = self._github.user_id(token)

        quota_key = self._reserve_quota(user_id, now)
        try:
            self._reserve_spend(now)
        except GatewayError:
            self.store.decr(quota_key)
            raise
        try:
            record = self._brightdata.fetch_profile(profile_url)
        except GatewayError:
            self.store.decr(quota_key)
            raise
        return Response(200, record, {"Cache-Control": "no-store"})

    def _limit_ip(self, ip: str, now: float) -> None:
        assert self.store is not None
        digest = hashlib.sha256(ip.encode()).hexdigest()[:32]  # the raw address is never stored
        minute = int(now // 60)
        count = self.store.incr(f"ip:{digest}:{minute}", 120)
        if count > self.config.ip_per_minute:
            raise GatewayError(
                429,
                "rate_limited",
                "Too many requests. Wait a minute and try again.",
                {"Retry-After": str(int(60 - now % 60) or 1)},
            )

    @staticmethod
    def _profile_url(request: Request) -> str:
        bad = GatewayError(400, "invalid_request", 'Send JSON of the form {"url": "<LinkedIn profile URL>"}.')
        if len(request.body) > MAX_BODY_BYTES:
            raise GatewayError(413, "too_large", "The request body is too large.")
        try:
            payload = json.loads(request.body)
        except ValueError:
            raise bad from None
        if not isinstance(payload, dict) or "url" not in payload:
            raise bad
        return normalize_profile_url(payload["url"])

    def _reserve_quota(self, user_id: int, now: float) -> str:
        assert self.store is not None
        moment = datetime.fromtimestamp(now, timezone.utc)
        key = f"quota:{user_id}:{moment:%Y-%m-%d}"
        used = self.store.incr(key, 2 * 86400)
        if used > self.config.daily_quota:
            self.store.decr(key)
            tomorrow = (int(now // 86400) + 1) * 86400
            raise GatewayError(
                429,
                "quota_exceeded",
                f"Daily limit of {self.config.daily_quota} profiles reached. "
                "Try again tomorrow (UTC), or use your LinkedIn data export.",
                {"Retry-After": str(max(1, int(tomorrow - now)))},
            )
        return key

    def _reserve_spend(self, now: float) -> None:
        assert self.store is not None
        moment = datetime.fromtimestamp(now, timezone.utc)
        key = f"spend:{moment:%Y-%m}"
        calls = self.store.incr(key, 45 * 86400)
        if calls > self.config.max_calls_per_month:
            self.store.decr(key)
            raise GatewayError(
                503,
                "spend_cap_reached",
                "The monthly budget for this service is used up. Use your LinkedIn data export instead.",
            )
