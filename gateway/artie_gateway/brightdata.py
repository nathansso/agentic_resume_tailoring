"""Bright Data's synchronous Web Scraper call for one LinkedIn profile.

A port of `LinkedInIngestor._scrape` in `ingestion/linkedin.py`: same endpoint,
same query, same body. `tests/test_gateway_contract.py` in the repo fails if
the two drift. Failures become short fixed messages; an upstream body is never
passed on.
"""
from __future__ import annotations

import json
from typing import Any, Dict
from urllib.parse import urlencode

from .errors import GatewayError
from .transport import Transport, TransportError, TransportTimeout

BRIGHTDATA_BASE = "https://api.brightdata.com/datasets/v3"


def _failed() -> GatewayError:
    return GatewayError(
        502,
        "upstream_error",
        "The profile could not be fetched. Check the URL, or use your LinkedIn data export instead.",
    )


class BrightDataClient:
    def __init__(self, api_key: str, dataset_id: str, transport: Transport, timeout: float) -> None:
        self._api_key = api_key
        self._dataset_id = dataset_id
        self._transport = transport
        self._timeout = timeout

    def fetch_profile(self, profile_url: str) -> Dict[str, Any]:
        query = urlencode({"dataset_id": self._dataset_id, "format": "json", "include_errors": "true"})
        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
        body = json.dumps([{"url": profile_url}]).encode()
        try:
            result = self._transport(
                "POST", f"{BRIGHTDATA_BASE}/scrape?{query}", headers, body, self._timeout
            )
        except TransportTimeout:
            raise GatewayError(
                504, "upstream_timeout", "The profile took too long to fetch. Try again later."
            ) from None
        except TransportError:
            raise GatewayError(502, "upstream_unreachable", "Could not reach the profile service.") from None

        if result.status == 202:
            # Bright Data hands back a snapshot id when a scrape outlives its sync window.
            raise GatewayError(
                504, "upstream_timeout", "The profile took too long to fetch. Try again later."
            )
        if result.status != 200:
            raise _failed()
        try:
            data = json.loads(result.body)
        except ValueError:
            raise _failed() from None

        if isinstance(data, list):
            records = [r for r in data if isinstance(r, dict)]
            if not records:
                raise _failed()
            record = records[0]
        elif isinstance(data, dict):
            record = data
        else:
            raise _failed()
        if not record or record.get("error") or record.get("warning"):
            raise _failed()
        return record
