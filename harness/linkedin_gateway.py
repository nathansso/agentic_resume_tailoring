"""The LinkedIn gateway client (#355): one POST to the hosted gateway (#354, #396).

The gateway holds ART's Bright Data key, so the user never needs one. It identifies
the caller by their GitHub token from `artie connect` (#353, #400) and keys its
quotas on the GitHub user: two profiles a day, and a monthly spend cap that turns
the endpoint off (503) when it is reached.

- **Endpoint.** `POST {base}/v1/linkedin {"url": ...}` with `Authorization: Bearer
  <GitHub token>`. `base` is `DEFAULT_URL`, the production deploy; `ART_LINKEDIN_GATEWAY_URL`
  overrides it (the gateway's local dev server, `gateway/README.md`, or a test).
- **Timeout.** The gateway polls Bright Data's snapshot for up to 280 s, so one fetch
  can take minutes; `TIMEOUT_SECONDS` sits above that.
- **Errors** come back as `GatewayError(code, message)`, with the gateway's own `code`
  (`unauthorized`, `quota_exceeded`, `spend_cap_reached`, ...) or the client's
  (`timeout`, `unreachable`, `bad_response`). The message never holds the token or a
  response body; the gateway's own messages are short and fixed.
- **Nothing is logged** about the request: not the token, the URL or the profile.
- **Transport is injectable**, as the GitHub client's is: `(method, url, headers, body,
  timeout) -> (status, headers, text)`. Tests pass a fake gateway, so the suite never
  spends the owner's quota.
- **This is the fourth and last network client under `harness/`**, beside the Jev
  client, `harness/github.py` and `harness/toolchain.py`; `tests/test_harness_boundary.py`
  allows exactly those. Nothing here is a generative call.
"""

from __future__ import annotations

import json
import os
from typing import Any, Callable, Dict, Mapping, Optional, Tuple

DEFAULT_URL = "https://artie-gateway.vercel.app"
URL_ENV = "ART_LINKEDIN_GATEWAY_URL"
PATH = "/v1/linkedin"
TIMEOUT_SECONDS = 330.0         # the gateway's snapshot polling runs up to 280 s (#396)
USER_AGENT = "art-mcp"

Transport = Callable[[str, str, Dict[str, str], bytes, float], Tuple[int, Dict[str, str], str]]


class GatewayError(Exception):
    """A failed fetch. `code` is the gateway's error code or the client's own; `status`
    the HTTP status (None when no answer came); `retry_after` seconds, from a 429."""

    def __init__(self, code: str, message: str = "", *, status: Optional[int] = None,
                 retry_after: Optional[int] = None):
        super().__init__(f"{code}: {message}" if message else code)
        self.code, self.message, self.status, self.retry_after = code, message, status, retry_after


def base_url() -> str:
    return ((os.environ.get(URL_ENV) or "").strip() or DEFAULT_URL).rstrip("/")


def _requests_transport(method, url, headers, body, timeout):
    import requests

    try:
        resp = requests.request(method, url, headers=headers, data=body, timeout=timeout)
    except requests.Timeout as exc:
        raise GatewayError("timeout", "the gateway did not answer in time") from exc
    except requests.RequestException as exc:
        raise GatewayError("unreachable", type(exc).__name__) from exc
    return resp.status_code, {k.lower(): v for k, v in resp.headers.items()}, resp.text or ""


def _retry_after(headers: Mapping[str, str]) -> Optional[int]:
    try:
        return max(int(str(headers.get("retry-after", "")).strip()), 0)
    except ValueError:
        return None


def fetch_profile(url: str, token: str, *, transport: Optional[Transport] = None,
                  base: Optional[str] = None,
                  timeout: float = TIMEOUT_SECONDS) -> Dict[str, Any]:
    """Bright Data's record for one public profile URL, as the gateway returns it.
    Raises `GatewayError` for anything but a 200 carrying a JSON object."""
    if not token:
        raise GatewayError("not_logged_in", "no GitHub token")
    body = json.dumps({"url": url}).encode("utf-8")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json",
               "Accept": "application/json", "User-Agent": USER_AGENT}
    status, resp_headers, text = (transport or _requests_transport)(
        "POST", f"{(base or base_url()).rstrip('/')}{PATH}", headers, body, timeout)
    try:
        payload = json.loads(text) if text else None
    except ValueError:
        payload = None
    if status == 200:
        if isinstance(payload, list):           # a bare Bright Data list: take the first record
            payload = next((r for r in payload if isinstance(r, dict)), None)
        if not isinstance(payload, dict):
            raise GatewayError("bad_response", "the gateway returned no profile record",
                               status=status)
        return payload
    err = payload.get("error") if isinstance(payload, dict) else None
    code = str(err.get("code") or "") if isinstance(err, Mapping) else ""
    message = str(err.get("message") or "")[:200] if isinstance(err, Mapping) else ""
    raise GatewayError(code or f"http_{status}", message, status=status,
                       retry_after=_retry_after(resp_headers) if status == 429 else None)


__all__ = ["DEFAULT_URL", "URL_ENV", "TIMEOUT_SECONDS", "GatewayError", "base_url",
           "fetch_profile"]
