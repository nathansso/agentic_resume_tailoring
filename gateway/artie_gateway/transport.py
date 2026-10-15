"""The one place the gateway touches the network.

GitHub, Bright Data and the KV store all go through a `Transport`, so tests
swap in a fake and no test opens a socket. Exceptions carry no URL, header or
body: they end up near logs.
"""
from __future__ import annotations

import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Mapping, Optional


class TransportTimeout(Exception):
    """The peer did not answer in time."""


class TransportError(Exception):
    """The peer could not be reached."""


@dataclass(frozen=True)
class HttpResult:
    status: int
    body: bytes


# (method, url, headers, body, timeout seconds) -> HttpResult
Transport = Callable[[str, str, Mapping[str, str], Optional[bytes], float], HttpResult]


def urllib_transport(
    method: str,
    url: str,
    headers: Mapping[str, str],
    body: Optional[bytes],
    timeout: float,
) -> HttpResult:
    if not url.startswith("https://"):
        raise TransportError()
    request = urllib.request.Request(url, data=body, method=method, headers=dict(headers))
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            return HttpResult(response.status, response.read())
    except urllib.error.HTTPError as exc:
        try:
            return HttpResult(exc.code, exc.read())
        finally:
            exc.close()
    except (socket.timeout, TimeoutError):
        raise TransportTimeout() from None
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, (socket.timeout, TimeoutError)):
            raise TransportTimeout() from None
        raise TransportError() from None
    except OSError:
        raise TransportError() from None
