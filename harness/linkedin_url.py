"""The LinkedIn profile URL rule `artie connect` applies (#400).

The hosted gateway accepts public profiles only: `linkedin.com/in/<slug>`
(`urls.py` in the gateway's own package). The gateway deploys on its own and the harness
must not import it, so this is the same rule written out again;
`tests/test_connect.py` runs both over one corpus and fails if they part. The output
form is `https://www.linkedin.com/in/<slug>`, lower-case, with no trailing slash.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

_PATH = re.compile(r"/in/([\w%-]{2,100})/?")
_COUNTRY_HOST = re.compile(r"[a-z]{2,3}\.linkedin\.com")
_MAX_LENGTH = 300

MESSAGE = ("Give a LinkedIn profile URL of the form https://www.linkedin.com/in/<name> "
           "(the address of your public profile).")


class InvalidLinkedInURL(ValueError):
    """Not a `linkedin.com/in/<slug>` URL."""


def normalize_profile_url(raw: Any) -> str:
    """`https://www.linkedin.com/in/<slug>`, or raise `InvalidLinkedInURL`."""
    if not isinstance(raw, str):
        raise InvalidLinkedInURL(MESSAGE)
    text = raw.strip()
    if not text or len(text) > _MAX_LENGTH or any(c.isspace() for c in text):
        raise InvalidLinkedInURL(MESSAGE)
    if "://" not in text:
        text = "https://" + text
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        raise InvalidLinkedInURL(MESSAGE) from None
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or parts.username or parts.password or port:
        raise InvalidLinkedInURL(MESSAGE)
    if host not in ("linkedin.com", "www.linkedin.com") and not _COUNTRY_HOST.fullmatch(host):
        raise InvalidLinkedInURL(MESSAGE)
    match = _PATH.fullmatch(parts.path)
    if not match:
        raise InvalidLinkedInURL(MESSAGE)
    return f"https://www.linkedin.com/in/{match.group(1).lower()}"
