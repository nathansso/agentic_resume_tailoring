"""Institution name canonicalization for cross-source dedup (issue #95).

Resume and LinkedIn record the same school under different name forms — 'UC San
Diego', 'UCSD', 'University of California, San Diego'. Fuzzy string matching
can't bridge an acronym like 'UC' to 'University of California', so those rows
never deduplicated. We instead resolve each institution name to a stable
canonical key via ROR (the Research Organization Registry) affiliation matcher
and dedup on that key.

The lookup is paid once per distinct name: results are memoized in-process and
persisted in the InstitutionCanonical table, so re-ingests and the O(n^2)
self-heal comparisons never re-hit the network. On any network failure the
normalized name is returned as a transient fallback and not cached, so a later
ingest retries. ROR is a research-org registry, so most employers won't match
and simply fall back to normalized-name comparison — no regression.
"""
import logging
import os
import re
from typing import Optional, Tuple

import requests
from sqlmodel import Session

import database.db as _db
from database.models import InstitutionCanonical

logger = logging.getLogger(__name__)

# ROR affiliation matcher: returns candidate orgs with a `chosen` flag marking
# the single confident match. https://ror.readme.io/docs/api-affiliation
_ROR_URL = "https://api.ror.org/organizations"
_ROR_TIMEOUT_SECONDS = 5
_USER_AGENT = "ART-resume-tailoring (github.com/nathansso/agentic_resume_tailoring)"

# In-process memo (normalized name -> canonical key), complementing the DB cache
# so repeated comparisons within one ingest/heal never touch the DB.
_MEMO: dict[str, str] = {}


def _ror_enabled() -> bool:
    """ROR lookups can be disabled (offline dev, tests) via ROR_LOOKUP_ENABLED=0;
    canonicalization then degrades to normalized-string matching — the pre-#95
    behavior."""
    return os.getenv("ROR_LOOKUP_ENABLED", "1").lower() not in ("0", "false", "no")


class _RORUnavailable(Exception):
    """The ROR service could not be reached — a transient failure that must not
    be cached, so a later ingest retries."""


def normalize_institution(name) -> str:
    """Lowercase, strip punctuation, and collapse whitespace — the lookup key
    and the fallback canonical form when ROR can't resolve a name."""
    n = re.sub(r"[^a-z0-9 ]+", " ", str(name or "").lower())
    return re.sub(r"\s+", " ", n).strip()


def _ror_display_name(org: dict) -> Optional[str]:
    for entry in org.get("names") or []:
        if "ror_display" in (entry.get("types") or []):
            return entry.get("value")
    return None


def _query_ror(name: str) -> Optional[Tuple[str, Optional[str]]]:
    """Return (ror_id, display_name) for a confident ROR match, or None when the
    service responds but finds no confident match. Raises _RORUnavailable on a
    network/parse failure (which must not be cached)."""
    try:
        resp = requests.get(
            _ROR_URL,
            params={"affiliation": name},
            headers={"User-Agent": _USER_AGENT},
            timeout=_ROR_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise _RORUnavailable(str(exc)) from exc
    for item in data.get("items") or []:
        if item.get("chosen"):
            org = item.get("organization") or {}
            rid = org.get("id")
            if rid:
                return rid, _ror_display_name(org)
    return None


def canonicalize_institution(name) -> str:
    """Resolve an institution name to a stable canonical key for dedup.

    Returns a ROR id when the name confidently matches a Research Organization
    Registry record (so 'UC San Diego' and 'University of California, San Diego'
    share one key), otherwise the normalized name. Resolved once per distinct
    name and cached in-process and in the DB; a transient network failure falls
    back to the normalized name without caching, so a later ingest retries.
    (issue #95)
    """
    norm = normalize_institution(name)
    if not norm:
        return ""
    if norm in _MEMO:
        return _MEMO[norm]
    if not _ror_enabled():
        # Offline mode: no network, no cache — behaves like pre-#95 normalized
        # matching. Deliberately not memoized so re-enabling ROR takes effect.
        return norm

    with Session(_db.engine) as session:
        cached = session.get(InstitutionCanonical, norm)
        if cached:
            _MEMO[norm] = cached.canonical_key
            return cached.canonical_key

        try:
            resolved = _query_ror(str(name).strip() or norm)
        except _RORUnavailable as exc:
            logger.warning(
                "ROR unavailable for %r; using normalized fallback: %s", name, exc)
            return norm  # transient — do not cache, retry on a later ingest

        canonical_key = resolved[0] if resolved else norm
        display_name = resolved[1] if resolved else None
        try:
            session.add(InstitutionCanonical(
                raw_norm=norm, canonical_key=canonical_key, display_name=display_name))
            session.commit()
        except Exception as exc:  # concurrent insert / write race — safe to skip
            session.rollback()
            logger.debug("Institution cache write skipped for %r: %s", norm, exc)
        _MEMO[norm] = canonical_key
        return canonical_key
