"""Bright Data's Web Scraper call for one LinkedIn profile.

A port of `LinkedInIngestor._scrape` in `ingestion/linkedin.py`: same endpoint,
same query, same body. `tests/test_gateway_contract.py` in the repo fails if
the two drift. Failures become short fixed messages; an upstream body is never
passed on.

The synchronous `/scrape` call answers within about a minute. A LinkedIn profile
often takes longer, and then Bright Data answers `202` with a `snapshot_id` and
keeps scraping. The client then polls `/progress/{snapshot_id}` until the snapshot
is `ready` (or `failed`) and downloads it from `/snapshot/{snapshot_id}`, all
inside one overall deadline that stays under the function's `maxDuration`.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any, Callable, Dict
from urllib.parse import quote, urlencode

from .errors import GatewayError
from .transport import HttpResult, Transport, TransportError, TransportTimeout

BRIGHTDATA_BASE = "https://api.brightdata.com/datasets/v3"
POLL_SECONDS = 5.0
# A record's `error_code` / `warning_code` reaches the log only if it looks like a code.
_CODE = re.compile(r"[a-z0-9_]{1,40}")
_SNAPSHOT_ID = re.compile(r"[A-Za-z0-9_-]{1,80}")


def _failed(reason: str) -> GatewayError:
    return GatewayError(
        502,
        "upstream_error",
        "The profile could not be fetched. Check the URL, or use your LinkedIn data export instead.",
        reason=reason,
    )


def _timed_out(reason: str) -> GatewayError:
    return GatewayError(
        504, "upstream_timeout", "The profile took too long to fetch. Try again later.", reason=reason
    )


def _code(value: Any) -> str:
    text = value.strip().lower() if isinstance(value, str) else ""
    return text if _CODE.fullmatch(text) else "unknown"


def _json(body: bytes) -> Any:
    try:
        return json.loads(body)
    except ValueError:
        return None


class BrightDataClient:
    def __init__(
        self,
        api_key: str,
        dataset_id: str,
        transport: Transport,
        timeout: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        poll_seconds: float = POLL_SECONDS,
    ) -> None:
        self._api_key = api_key
        self._dataset_id = dataset_id
        self._transport = transport
        self._timeout = timeout          # the whole fetch, scrape plus polling
        self._clock = clock
        self._sleep = sleep
        self._poll = poll_seconds

    def fetch_profile(self, profile_url: str) -> Dict[str, Any]:
        deadline = self._clock() + self._timeout
        query = urlencode({"dataset_id": self._dataset_id, "format": "json", "include_errors": "true"})
        body = json.dumps([{"url": profile_url}]).encode()
        result = self._call("POST", f"{BRIGHTDATA_BASE}/scrape?{query}", body, deadline)
        if result.status == 202:
            # The scrape outlived the synchronous window; Bright Data keeps going under a snapshot.
            return self._wait_for_snapshot(self._snapshot_id(result.body), deadline)
        return self._record(result)

    # -- the asynchronous path -------------------------------------------------

    def _snapshot_id(self, body: bytes) -> str:
        data = _json(body)
        sid = data.get("snapshot_id") if isinstance(data, dict) else None
        if not isinstance(sid, str) or not _SNAPSHOT_ID.fullmatch(sid):
            raise _timed_out("snapshot_no_id")
        return sid

    def _wait_for_snapshot(self, snapshot_id: str, deadline: float) -> Dict[str, Any]:
        sid = quote(snapshot_id, safe="")
        ready = False
        while True:
            remaining = deadline - self._clock()
            if remaining <= 0:
                raise _timed_out("snapshot_timeout")
            self._sleep(min(self._poll, remaining))
            if not ready:
                progress = self._call("GET", f"{BRIGHTDATA_BASE}/progress/{sid}", None, deadline)
                if progress.status != 200:
                    raise _failed(f"upstream_http_{int(progress.status)}")
                data = _json(progress.body)
                status = data.get("status") if isinstance(data, dict) else None
                if status == "failed":
                    raise _failed("snapshot_failed")
                if status != "ready":
                    continue                     # "running", "starting", or anything not final yet
                ready = True
            snapshot = self._call(
                "GET", f"{BRIGHTDATA_BASE}/snapshot/{sid}?{urlencode({'format': 'json'})}", None, deadline
            )
            if snapshot.status == 202:
                continue                         # "building": ready, but the file is not written yet
            return self._record(snapshot)

    # -- shared ----------------------------------------------------------------

    def _call(self, method: str, url: str, body, deadline: float) -> HttpResult:
        headers = {"Authorization": f"Bearer {self._api_key}"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        remaining = deadline - self._clock()
        if remaining <= 0:
            raise _timed_out("snapshot_timeout")
        try:
            return self._transport(method, url, headers, body, remaining)
        except TransportTimeout:
            raise _timed_out("upstream_timeout") from None
        except TransportError:
            raise GatewayError(
                502, "upstream_unreachable", "Could not reach the profile service.",
                reason="upstream_unreachable",
            ) from None

    def _record(self, result: HttpResult) -> Dict[str, Any]:
        if result.status != 200:
            # The status alone diagnoses most failures (401 key, 402 billing, 403 account or
            # permission, 400/404 dataset) without logging Bright Data's body.
            raise _failed(f"upstream_http_{int(result.status)}")
        data = _json(result.body)
        if data is None:
            raise _failed("upstream_not_json")
        if isinstance(data, list):
            records = [r for r in data if isinstance(r, dict)]
            if not records:
                raise _failed("upstream_empty")
            record = records[0]
        elif isinstance(data, dict):
            record = data
        else:
            raise _failed("upstream_shape")
        if not record:
            raise _failed("upstream_empty")
        if record.get("error"):
            raise _failed(f"record_error:{_code(record.get('error_code'))}")
        if record.get("warning"):
            raise _failed(f"record_warning:{_code(record.get('warning_code'))}")
        return record
