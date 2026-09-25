"""Read-only harness tools over the knowledge graph (issue #189, the H0 spike).

Three tools, and the whole of what a host agent can see in this spike:

- `art_briefing` **pushes** what must not be missed: strength-5 preferences
  verbatim (suppressions included — they are the negative pins until #202),
  the scoped preference set, the compiled persona, and prior JobCards with
  their user rejections. Push, not search, because the dominant preference
  signal is negation and similarity search cannot represent "not this"
  (#109, docs/harness.md § 10).
- `kg_search` **pulls** items by a deterministic lexical match. Only `Skill`
  and `JobDescription` carry embeddings, so semantic search over experiences
  and projects does not exist yet; that is #194's FTS/embedding work.
- `get_item` resolves one stable key to its full record.

Every function takes `user_id` explicitly, reads through `services` (whose
`engine` the test fixture isolates), and never writes. Keys are the
planner's own (`agents.checks.exp_key`/`proj_key`) so a host can hand them straight
to the executor later (#197).
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence
from uuid import UUID

from sqlmodel import Session, select

import services
from agents.checks import exp_key, proj_key  # the planner's own keys (#190)
from agents.job_card import render_cards, select_cards
from agents.preferences import preferences_in_scope
from database.models import ProjectBlurb, User

KINDS = ("skill", "experience", "project", "education", "achievement")
_PREFIX = {"skill": "skill", "experience": "exp", "project": "proj",
           "education": "edu", "achievement": "ach"}
_KIND_OF_PREFIX = {v: k for k, v in _PREFIX.items()}
PIN_STRENGTH = 5

_TOKEN = re.compile(r"[a-z0-9][a-z0-9+#.]*")
_STOP = frozenset(
    "a an and are as at be by for from in into is it of on or the to with"
    " using use used via our we i my".split())
# A query token found in the item's name/title counts this much more than one
# found only in body text.
_TITLE_WEIGHT = 3.0


# ── keys ──────────────────────────────────────────────────────────────────────

def _k(value: Any) -> str:
    return (str(value or "")).strip().lower()


def skill_key(s: Dict) -> str:
    return f"skill:{_k(s.get('name'))}"


def edu_key(e: Dict) -> str:
    degree = "" if e.get("degree") in (None, "—") else e.get("degree")
    return f"edu:{_k(e.get('institution'))}|{_k(degree)}"


def ach_key(a: Dict) -> str:
    return f"ach:{_k(a.get('title'))}"


_KEY_FN = {"skill": skill_key, "experience": exp_key, "project": proj_key,
           "education": edu_key, "achievement": ach_key}


# ── records ───────────────────────────────────────────────────────────────────

def _project_blurbs(project_ids: Sequence[str]) -> Dict[str, List[Dict]]:
    """Stored project bullet variants (`ProjectBlurb`), keyed by project id."""
    ids = []
    for pid in project_ids:
        try:
            ids.append(UUID(str(pid)))
        except ValueError:
            continue
    if not ids:
        return {}
    out: Dict[str, List[Dict]] = {}
    with Session(services.engine) as session:
        rows = session.exec(
            select(ProjectBlurb).where(ProjectBlurb.project_id.in_(ids))
        ).all()
    for b in sorted(rows, key=lambda r: (str(r.project_id), r.style or "", str(r.blurb_id))):
        out.setdefault(str(b.project_id), []).append({"style": b.style, "content": b.content})
    return out


def _records(user_id: UUID, kinds: Optional[Sequence[str]] = None) -> List[Dict]:
    """Every KG item for this user as `{key, kind, title, text, record}`."""
    wanted = [k for k in KINDS if not kinds or k in kinds]
    items: List[Dict] = []
    if "skill" in wanted:
        for s in services.get_skills(user_id):
            items.append({"kind": "skill", "record": s, "title": s["name"],
                          "text": f"{s.get('category', '')} {s.get('source', '')}"})
    if "experience" in wanted:
        for e in services.get_experiences(user_id):
            items.append({"kind": "experience", "record": e,
                          "title": f"{e['title']} @ {e['company']}",
                          "text": " ".join([e.get("description") or "", *(e.get("bullets") or [])])})
    if "project" in wanted:
        projects = services.get_projects(user_id)
        blurbs = _project_blurbs([p["id"] for p in projects])
        for p in projects:
            record = dict(p, blurbs=blurbs.get(p["id"], []))
            items.append({"kind": "project", "record": record, "title": p["name"],
                          "text": " ".join([p.get("description") or "",
                                            *(b["content"] for b in record["blurbs"])])})
    if "education" in wanted:
        for e in services.get_education(user_id):
            items.append({"kind": "education", "record": e,
                          "title": f"{e['institution']} — {e['degree']}",
                          "text": e.get("location") or ""})
    if "achievement" in wanted:
        for a in services.get_achievements(user_id):
            items.append({"kind": "achievement", "record": a, "title": a["title"],
                          "text": f"{a.get('issuer', '')} {a.get('description', '')}"})
    for it in items:
        it["key"] = _KEY_FN[it["kind"]](it["record"])
    return items


# ── tools ─────────────────────────────────────────────────────────────────────

def art_briefing(user_id: UUID, role_family: Optional[str] = None) -> Dict[str, Any]:
    """What the host must hold before planning, pushed rather than searched."""
    prefs = preferences_in_scope(services.load_preferences(user_id), role_family=role_family)
    prefs = sorted(prefs, key=lambda p: (-(p.get("strength") or 0), str(p.get("preference_id"))))
    pins = [
        {"text": p["text"], "polarity": p.get("polarity"),
         "target_key": p.get("target_key"), "scope": p.get("scope_type")}
        for p in prefs if (p.get("strength") or 0) >= PIN_STRENGTH
    ]
    persona = services.get_active_persona(user_id, role_family=role_family)
    cards = services.load_job_cards(user_id)
    if role_family:
        cards = select_cards(cards, role_family=role_family)
    return {
        "role_family": role_family,
        "pins": pins,
        "preferences": [
            {k: p.get(k) for k in ("preference_id", "text", "polarity", "target_key",
                                   "target_term", "scope_type", "scope_value", "strength")}
            for p in prefs
        ],
        "persona_traits": persona.get("traits", []),
        "job_cards": render_cards(cards),
        "counts": {"pins": len(pins), "preferences": len(prefs), "job_cards": len(cards)},
    }


def _tokens(text: str) -> List[str]:
    return [t.rstrip(".") for t in _TOKEN.findall((text or "").lower())
            if t.rstrip(".") and t.rstrip(".") not in _STOP]


def kg_search(user_id: UUID, query: str, kinds: Optional[Sequence[str]] = None,
              limit: int = 10) -> List[Dict[str, Any]]:
    """Lexical search over the KG. Deterministic: score desc, then key asc."""
    q = sorted(set(_tokens(query)))
    if not q:
        return []
    hits = []
    for it in _records(user_id, kinds):
        title = set(_tokens(it["title"]))
        body = set(_tokens(it["text"]))
        score = sum(_TITLE_WEIGHT if t in title else 1.0 if t in body else 0.0 for t in q)
        if score <= 0:
            continue
        snippet = (it["text"] or it["title"]).strip().replace("\n", " ")
        hits.append({"key": it["key"], "kind": it["kind"], "title": it["title"],
                     "score": round(score, 3), "snippet": snippet[:200]})
    hits.sort(key=lambda h: (-h["score"], h["key"]))
    return hits[: max(1, int(limit))]


def get_item(user_id: UUID, key: str) -> Dict[str, Any]:
    """The full record behind one key, or a structured not-found. Never raises."""
    norm = (key or "").strip().lower()
    prefix = norm.split(":", 1)[0]
    kind = _KIND_OF_PREFIX.get(prefix)
    if kind:
        for it in _records(user_id, [kind]):
            if it["key"] == norm:
                return {"key": it["key"], "kind": kind, "record": it["record"]}
    query = norm.split(":", 1)[-1].replace("|", " ")
    return {"key": key, "error": {
        "code": "not_found", "message": f"No item with key {key!r}.",
        "suggestions": [h["key"] for h in kg_search(user_id, query, limit=5)]}}


def list_items(user_id: UUID, kind: Optional[str] = None) -> List[Dict[str, Any]]:
    """Every item's key, kind and title — enumerate without guessing query words.

    The #189 e2e run could not list all experiences without inventing search
    terms that happened to match; this is that gap (#191).
    """
    items = _records(user_id, [kind] if kind else None)
    return sorted(({"key": it["key"], "kind": it["kind"], "title": it["title"]}
                   for it in items), key=lambda r: (r["kind"], r["key"]))


# Header fields only. Credentials (password hash, GitHub token), auth ids and raw
# scrape records never leave the store through a tool.
_PROFILE_FIELDS = ("name", "email", "phone", "location", "linkedin_url",
                   "github_username", "portfolio_url")


def get_profile(user_id: UUID) -> Dict[str, Any]:
    """Name and contact details for the resume header (#191; missing in #189)."""
    with Session(services.engine) as session:
        user = session.get(User, user_id)
        if user is None:
            return {"error": {"code": "not_found", "message": f"No user {user_id}."}}
        return {f: getattr(user, f, None) or None for f in _PROFILE_FIELDS}
