"""Model-free readers over a compiled JD profile payload (issue #197).

`agents/jd_profile.py` imports the extraction stack (`langchain_core`, `llm`)
at module level, so anything that only needs to *read* a stored profile —
arbitration, keyword weights, the harness executor — imports these from here
instead and stays inside the harness import boundary
(`tests/test_harness_boundary.py`). `jd_profile` re-exports both names, so
existing callers are unchanged.
"""
import hashlib
from typing import Dict, List, Optional


def text_digest(description: str, version: int) -> str:
    """Digest of the JD text + a schema version, whitespace-normalized.

    An equal key means a stored artifact already describes this exact posting.
    Whitespace is normalized so a reflowed paste is not treated as a new
    posting.
    """
    normalized = " ".join((description or "").split())
    raw = f"{version}\x00{normalized}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def iter_requirements(
    payload: Optional[Dict], types: Optional[List[str]] = None,
) -> List[Dict]:
    """Requirements in source order, optionally filtered by type.

    The read path downstream consumers use. Returns a list rather than a
    generator so callers can count without exhausting it, and always in source
    order — filtering never reorders.
    """
    rows = [
        r for r in ((payload or {}).get("requirements") or [])
        if isinstance(r, dict)
    ]
    if types is not None:
        wanted = set(types)
        rows = [r for r in rows if r.get("type") in wanted]
    return rows
