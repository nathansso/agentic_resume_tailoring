"""`ingest_linkedin(url)` (#355): the user's LinkedIn profile into the knowledge graph.

The agent calls it with the user's own profile URL, or with none to use the URL
`artie connect` saved (#400). It:

1. reads the GitHub token from the keychain (`harness.github.resolve_token`, #353),
   which identifies the user to the hosted gateway;
2. fetches the profile through the gateway (`harness.linkedin_gateway`, #354, #396);
3. maps Bright Data's record with the shared, model-free mapping
   (`agents/linkedin_mapping.py`, the legacy parser's too);
4. stores the entities through `upsert_items`, the path setup uses, so dedup,
   tombstones and `manually_edited` protection apply; and reports what was added.

The public scrape is often sparse (#396): it may carry no experience at all. What is
present is stored; incomplete entries are skipped, never filled in; empty sections are
reported as `missing`. Posts, courses, bio links and the about text come back as
`candidates`, for the agent to confirm with the user before storing anything from them.
Every failure (not signed in, refused, over quota, the gateway down, a timeout) says so
plainly and points to the LinkedIn data export, which stays the richer path.

Nothing here logs the token or the profile.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from uuid import UUID

from agents import linkedin_mapping
from harness import github, linkedin_gateway
from harness.ingest import upsert_items
from harness.linkedin_url import InvalidLinkedInURL, normalize_profile_url
from harness.tools import get_profile

EXPORT_HINT = (
    "The LinkedIn data export works instead, and carries more (full experience): in LinkedIn, "
    "Settings > Data privacy > Get a copy of your data. Give the agent the archive's CSV files "
    "(Positions, Education, Projects, Honors) and it stores what it reads with upsert_items, "
    "source linkedin.")

_KIND = {"experiences": "experience", "education": "education", "projects": "project",
         "achievements": "achievement"}


def _error(code: str, message: str) -> Dict[str, Any]:
    return {"error": {"code": code, "message": message, "suggestions": []}}


def _hours(seconds: Optional[int]) -> str:
    if not seconds:
        return "tomorrow (the quota resets at midnight UTC)"
    hours = max(1, round(seconds / 3600))
    return f"in about {hours} hour{'s' if hours != 1 else ''}"


def explain(exc: linkedin_gateway.GatewayError) -> Dict[str, Any]:
    """A failed fetch in plain words, ending with the export path."""
    code, status = exc.code, exc.status
    if code == "not_logged_in":
        text = ("Not signed in to GitHub, which the LinkedIn lookup uses to know who is asking. "
                f"Have the user run this in their own terminal: {github.login_hint()}, then call "
                "this again.")
    elif status == 401 or code == "unauthorized":
        text = ("The LinkedIn lookup refused the GitHub sign-in (expired or revoked). Have the "
                "user sign in again in their own terminal: artie connect --reauth (or, if "
                "ART_GITHUB_TOKEN is set, replace or unset it), then call this again.")
    elif code == "quota_exceeded":
        text = ("The LinkedIn lookup's daily quota (two profiles a day) is used up. Try again "
                f"{_hours(exc.retry_after)}.")
    elif status == 429:
        wait = f"{exc.retry_after} seconds" if exc.retry_after else "a minute"
        text = f"Too many LinkedIn lookups from this machine; try again in {wait}."
    elif code == "spend_cap_reached":
        text = "The LinkedIn lookup is switched off for the rest of the month (its spend cap is reached)."
    elif status == 503:
        text = "The LinkedIn lookup is down right now (the gateway is unavailable)."
    elif code == "invalid_url" or status == 400:
        text = f"The gateway refused the URL. {exc.message}".strip()
    elif code == "timeout" or status == 504:
        text = ("The LinkedIn lookup timed out: a profile can take a few minutes, and this one "
                "took longer. Try again later.")
    elif code == "unreachable":
        text = "Could not reach the LinkedIn lookup service (offline, or a network block)."
    elif status == 502:
        text = "LinkedIn's data provider failed for this profile. Try again later."
    else:
        text = f"The LinkedIn lookup failed ({code})."
    return _error(code if code != f"http_{status}" else "gateway_error", f"{text} {EXPORT_HINT}")


def _profile_url(user_id: UUID) -> Optional[str]:
    return (get_profile(user_id) or {}).get("linkedin_url")


def _records(mapped: Dict[str, Any], skipped: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """`upsert_items` records. An experience needs its company there (the identity it is
    deduplicated on), so a role with a title alone is skipped rather than given one."""
    records = []
    for section, kind in _KIND.items():
        for data in mapped[section]:
            if kind == "experience" and not data.get("company"):
                skipped.append({"kind": kind, "label": data.get("title") or "(no detail)",
                                "reason": "no company"})
                continue
            records.append({"kind": kind, "data": data})
    return records


def ingest_linkedin(user_id: UUID, url: Optional[str] = None, *,
                    transport: Optional[linkedin_gateway.Transport] = None) -> Dict[str, Any]:
    """The tool body. Errors come back in the contract's `error` shape, never raised; the
    token is never part of the result."""
    raw = (url or "").strip() or _profile_url(user_id)
    if not raw:
        return _error("no_url", "No LinkedIn URL given, and none is saved on the profile. Ask "
                                "the user for their own profile URL (linkedin.com/in/<name>) "
                                "and pass it as url. " + EXPORT_HINT)
    try:
        profile_url = normalize_profile_url(raw)
    except InvalidLinkedInURL as exc:
        return _error("invalid_url", str(exc))

    token = github.resolve_token()
    try:
        if not token:
            raise linkedin_gateway.GatewayError("not_logged_in")
        record = linkedin_gateway.fetch_profile(profile_url, token, transport=transport)
    except linkedin_gateway.GatewayError as exc:
        return explain(exc)

    mapped = linkedin_mapping.map_record(record)
    skipped = list(mapped["skipped"])
    records = _records(mapped, skipped)
    source = f"linkedin:{profile_url}"
    if records:
        stored = upsert_items(user_id, records, source)
    else:
        stored = {"results": [], "counts": {}, "relinked": []}

    added: Dict[str, int] = {}
    for r in stored["results"]:
        if r["status"] == "created":
            added[r["kind"]] = added.get(r["kind"], 0) + 1
    notes = []
    if not records:
        notes.append("Nothing in the profile could be stored.")
    if "experience" in mapped["missing"]:
        notes.append("The public profile shows no experience, which is common for a public "
                     "scrape. " + EXPORT_HINT)
    if any(mapped["candidates"][k] for k in ("activity", "courses", "bio_links")):
        notes.append("candidates are not stored: confirm each with the user first (a post "
                     "about a win is a claim, not an award) and store what they confirm with "
                     "upsert_items.")
    return {"url": profile_url, "source": source, "name": mapped["name"],
            "headline": mapped["headline"], "added": added,
            "results": stored["results"], "counts": stored["counts"],
            "relinked": stored.get("relinked") or [], "skipped": skipped,
            "missing": mapped["missing"], "candidates": mapped["candidates"],
            "note": " ".join(notes) or None}


__all__ = ["ingest_linkedin", "explain", "EXPORT_HINT"]
