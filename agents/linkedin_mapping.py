"""The deterministic Bright Data LinkedIn record → KG entities mapping (#73, #96, #355).

Bright Data returns a LinkedIn profile as structured JSON, so its experiences,
education, projects and honors map straight onto ART's entities with no model in
the loop. Both ingest paths use this one mapping:

- the legacy parser (`agents/parser.py::ResumeParserAgent._save_linkedin_structured`),
  which saves the entities with its own merge rules;
- the harness tool `ingest_linkedin` (`harness/linkedin.py`), which stores them
  through `upsert_items`, the path setup uses.

Model-free by rule: the harness imports this module, so it may import nothing that
reaches a generative client (`tests/test_harness_boundary.py`).

The public scrape is often sparse (#396): `experience` and `position` can be null,
`about` truncated, an education entry missing its school. The mapping keeps only
entries that carry their identity (a school, a project name, an award title, a
role's title or company), lists the rest under `skipped` with the reason, and
never fills a field the record does not have. Posts (`activity`), courses and bio
links are returned as `candidates`: evidence for the agent to confirm with the
user, never stored as facts.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

# Sections a full profile carries; any that come back empty are reported as missing.
SECTIONS = ("experience", "education", "projects", "honors_and_awards")
ACTIVITY_MAX = 20            # posts handed back as candidates
POST_MAX_CHARS = 600         # a post's text, enough to tell what it is about


def coerce_records(data: Any, str_key: Optional[str] = None) -> List[Dict]:
    """A section's records as a list of dicts.

    Upstream JSON can arrive wrapped in an object (`{"skills": [...]}`) or as bare
    strings; both crash downstream `.get()` calls if passed through untouched.
    `str_key`: when set, a bare string becomes `{str_key: item}`; otherwise it is dropped.
    """
    if isinstance(data, dict):
        wrapped = next((v for v in data.values() if isinstance(v, list)), None)
        data = wrapped if wrapped is not None else [data]
    if not isinstance(data, list):
        return []
    records: List[Dict] = []
    for item in data:
        if isinstance(item, dict):
            records.append(item)
        elif isinstance(item, str) and str_key and item.strip():
            records.append({str_key: item.strip()})
    return records


def flatten_experiences(experiences: List[Dict]) -> List[Dict]:
    """One dict per role (#96).

    Several roles at one employer arrive nested under `positions`, with the company
    on the parent record and the role's own title, dates and description on each
    item. A multi-role employer yields one entry per role; a single-role employer
    passes through unchanged; the parent company backfills a role that omits its own.
    """
    flat: List[Dict] = []
    for rec in experiences:
        if not isinstance(rec, dict):
            continue
        positions = rec.get("positions")
        if isinstance(positions, list) and positions:
            company = _text(rec.get("company") or rec.get("company_name"))
            for pos in positions:
                if not isinstance(pos, dict):
                    continue
                role = dict(pos)
                if not _text(role.get("company")) and company:
                    role["company"] = company
                flat.append(role)
        else:
            flat.append(rec)
    return flat


def experience_bullets(item: Dict) -> List[str]:
    """Bullets for one role (#96).

    An explicit `bullets` list wins; otherwise a multi-line `description` is split
    into lines (leading glyphs stripped), so a role described as a bulleted blob is
    not reduced to an empty stub. A single-line description yields no bullets: it
    stays the description.
    """
    raw = item.get("bullets")
    if isinstance(raw, list):
        out = [str(b).strip() for b in raw if str(b or "").strip()]
        if out:
            return out
    desc = str(item.get("description") or "")
    lines = [re.sub(r"^[\s•·\-\*]+", "", ln).strip() for ln in desc.splitlines()]
    lines = [ln for ln in lines if ln]
    return lines if len(lines) >= 2 else []


def _text(value: Any) -> str:
    if value is None or isinstance(value, (dict, list, bool)):
        return ""
    return str(value).strip()


def _some(**fields: Any) -> Dict[str, Any]:
    """Only the fields the record has: nothing empty is passed on as a value."""
    return {k: v for k, v in fields.items() if v not in (None, "", [])}


def _label(*parts: Any) -> str:
    return " — ".join(p for p in (_text(x) for x in parts) if p) or "(no detail)"


def map_experiences(record: Dict[str, Any]) -> tuple:
    out, skipped = [], []
    for item in flatten_experiences(coerce_records(record.get("experience"), str_key="title")):
        title, company = _text(item.get("title")), _text(item.get("company"))
        if not title and not company:
            skipped.append({"kind": "experience", "label": _label(item.get("description")),
                            "reason": "no title or company"})
            continue
        out.append(_some(title=title, company=company,
                         start_date=_text(item.get("start_date")),
                         end_date=_text(item.get("end_date")),
                         description=_text(item.get("description")),
                         bullets=experience_bullets(item)))
    return out, skipped


def map_education(record: Dict[str, Any]) -> tuple:
    """Bright Data gives `title` (the school), `degree`, `field` and sometimes
    `start_year` / `end_year`. The degree is "degree, field" (#73)."""
    out, skipped = [], []
    for item in coerce_records(record.get("education"), str_key="title"):
        institution = _text(item.get("title") or item.get("institution"))
        degree = ", ".join(p for p in (_text(item.get("degree")), _text(item.get("field"))) if p)
        if not institution:
            skipped.append({"kind": "education", "label": _label(degree, item.get("end_year")),
                            "reason": "no school"})
            continue
        out.append(_some(institution=institution, degree=degree,
                         start_date=_text(item.get("start_year") or item.get("start_date")),
                         end_date=_text(item.get("end_year") or item.get("end_date"))))
    return out, skipped


def map_projects(record: Dict[str, Any]) -> tuple:
    out, skipped = [], []
    for item in coerce_records(record.get("projects"), str_key="title"):
        name = _text(item.get("title") or item.get("name"))
        if not name:
            skipped.append({"kind": "project", "label": _label(item.get("description")),
                            "reason": "no name"})
            continue
        out.append(_some(name=name, description=_text(item.get("description")),
                         start_date=_text(item.get("start_date")),
                         end_date=_text(item.get("end_date"))))
    return out, skipped


def map_achievements(record: Dict[str, Any]) -> tuple:
    """`honors_and_awards`: `title`, `publication` (the issuer), `date`, `description`."""
    out, skipped = [], []
    for item in coerce_records(record.get("honors_and_awards"), str_key="title"):
        title = _text(item.get("title"))
        if not title:
            skipped.append({"kind": "achievement", "label": _label(item.get("description")),
                            "reason": "no title"})
            continue
        out.append(_some(title=title, description=_text(item.get("description")),
                         issuer=_text(item.get("issuer") or item.get("publication")),
                         date=_text(item.get("date"))))
    return out, skipped


def _clip(text: str, limit: int) -> tuple:
    text = " ".join(text.split())
    return (text[:limit].rstrip(), True) if len(text) > limit else (text, False)


def candidates(record: Dict[str, Any]) -> Dict[str, Any]:
    """What the profile shows but ART does not store as fact: the agent confirms it with
    the user first (a post about a hackathon win is a claim to check, not an award)."""
    posts = []
    for item in coerce_records(record.get("activity"), str_key="title") + \
            coerce_records(record.get("posts"), str_key="title"):
        body = _text(item.get("title") or item.get("text") or item.get("headline"))
        if not body:
            continue
        text, clipped = _clip(body, POST_MAX_CHARS)
        posts.append(_some(text=text, truncated=clipped or None,
                           link=_text(item.get("link") or item.get("url")),
                           interaction=_text(item.get("interaction") or item.get("attribution")),
                           date=_text(item.get("created_at") or item.get("date"))))
        if len(posts) >= ACTIVITY_MAX:
            break
    courses = [_some(title=_text(c.get("title") or c.get("name")),
                     code=_text(c.get("subtitle") or c.get("number")))
               for c in coerce_records(record.get("courses"), str_key="title")]
    links = [_some(title=_text(b.get("title")), url=_text(b.get("link") or b.get("url")))
             for b in coerce_records(record.get("bio_links"))]
    about = _text(record.get("about") or record.get("summary"))
    return {"activity": posts,
            "courses": [c for c in courses if c.get("title")],
            "bio_links": [b for b in links if b.get("url")],
            "about": about or None}


def map_record(record: Dict[str, Any]) -> Dict[str, Any]:
    """The whole record: entities in `ingest_schema`'s field names, what was skipped and
    why, the sections that came back empty, and the candidates."""
    record = record if isinstance(record, dict) else {}
    experiences, s1 = map_experiences(record)
    education, s2 = map_education(record)
    projects, s3 = map_projects(record)
    achievements, s4 = map_achievements(record)
    return {
        "experiences": experiences, "education": education, "projects": projects,
        "achievements": achievements,
        "skipped": s1 + s2 + s3 + s4,
        "missing": [s for s in SECTIONS if not coerce_records(record.get(s), str_key="title")],
        "candidates": candidates(record),
        "name": _text(record.get("name") or record.get("full_name")) or None,
        "headline": _text(record.get("position") or record.get("headline")) or None,
    }


__all__ = ["coerce_records", "flatten_experiences", "experience_bullets", "map_record",
           "map_experiences", "map_education", "map_projects", "map_achievements",
           "candidates", "SECTIONS"]
