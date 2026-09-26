"""`art ui` local mode (issue #204): the web app as a single-user local editor.

The launcher (`python -m web.local_ui`) sets `ART_LOCAL_UI=1` and binds the
local user in `ART_LOCAL_UI_USER_ID` before it imports `web.app`. Nothing else
sets them, and with the flag off every path here is inert, so the hosted
deploy is unchanged: a cookieless request still gets a 401.

A server with no login needs two guards a hosted one gets from its cookie:

- **Host allow-list.** Only `127.0.0.1` / `localhost` Host headers are served,
  which stops DNS rebinding (an attacker's hostname resolving to 127.0.0.1).
- **Same-origin writes.** A non-GET request carrying an `Origin` that is not
  this server's own is refused, which stops another browser tab from POSTing
  to the editor. Requests without `Origin` (curl, the host's own tools) pass.
"""

from __future__ import annotations

import json
import os
from typing import Optional
from uuid import UUID

LOCAL_UI_ENV = "ART_LOCAL_UI"
LOCAL_USER_ENV = "ART_LOCAL_UI_USER_ID"
READ_ONLY_ENV = "ART_LOCAL_UI_READ_ONLY"
LOCAL_HOSTS = ("127.0.0.1", "localhost")
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def enabled() -> bool:
    return os.environ.get(LOCAL_UI_ENV) == "1"


def read_only() -> bool:
    return enabled() and os.environ.get(READ_ONLY_ENV) == "1"


def local_user_id() -> Optional[UUID]:
    raw = os.environ.get(LOCAL_USER_ENV)
    try:
        return UUID(raw) if raw else None
    except ValueError:
        return None


def local_user():
    """The bound local user, or None. Reads through `database.db.engine` so a
    test's patched engine is honoured."""
    uid = local_user_id()
    if uid is None:
        return None
    from sqlmodel import Session

    import database.db as _db
    from database.models import User

    with Session(_db.engine) as session:
        return session.get(User, uid)


def origin_allowed(method: str, origin: Optional[str], host: Optional[str]) -> bool:
    """Pure: may this request proceed? Safe methods always may; a write may when
    it carries no Origin or its Origin is this server's own (`http://<Host>`)."""
    if method.upper() in _SAFE_METHODS or origin is None:
        return True
    return bool(host) and origin.rstrip("/").lower() == f"http://{host}".lower()


class SameOriginWriteGuard:
    """Pure-ASGI middleware (not BaseHTTPMiddleware, which buffers SSE streams)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = {k.decode("latin-1").lower(): v.decode("latin-1")
                       for k, v in scope.get("headers") or []}
            if not origin_allowed(scope.get("method", "GET"), headers.get("origin"),
                                  headers.get("host")):
                body = json.dumps({"detail": "Cross-origin write refused"}).encode()
                await send({"type": "http.response.start", "status": 403,
                            "headers": [(b"content-type", b"application/json"),
                                        (b"content-length", str(len(body)).encode())]})
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)


def install(app) -> None:
    """Add the local-mode guards. Called by `create_app` only when `enabled()`."""
    from starlette.middleware.trustedhost import TrustedHostMiddleware

    # Added last = runs first: reject a foreign Host before anything else.
    app.add_middleware(SameOriginWriteGuard)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(LOCAL_HOSTS))
