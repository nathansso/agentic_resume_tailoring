"""Read-only harness tools over the knowledge graph (issue #189, the H0 spike).

Three tools, and the whole of what a host agent can see in this spike:

- `art_briefing` **pushes** what must not be missed: strength-5 preferences
  verbatim (a strength-5 suppression is a negative pin), the scoped preference
  set, the compiled persona, and prior JobCards with their user rejections.
  Push, not search, because the dominant preference signal is negation and
  similarity search cannot represent "not this" (#109, docs/harness.md § 10).
  `art_pins` is the pins alone, scoped the same way, plus a job's own (#202).
- `kg_search` **pulls** items by full-text search (SQLite FTS5 with stemming,
  #194), falling back to a plain token match. Only `Skill` and
  `JobDescription` carry embeddings, so there is no semantic search over
  experiences and projects.
- `get_item` resolves one stable key to its full record.
- `suggest_project_contexts` proposes where each project was done (a role, a
  degree); `set_project_context` and `link_achievement` record the user's
  answer. The only writes here, and only on the user's confirmation.

Records carry the stored links: a project's `context` and `context_status`, a
role's or degree's `projects`, an award's `project`. A role or degree is also
findable by the names of the projects done under it.

Every function takes `user_id` explicitly, reads through `services` (whose
`engine` the test fixture isolates), and never writes. Keys are the
planner's own (`agents.checks.exp_key`/`proj_key`) so a host can hand them straight
to the executor later (#197).
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple
from uuid import UUID

from sqlmodel import Session, select

import services
from agents import project_context
from agents.checks import exp_key, proj_key  # the planner's own keys (#190)
from agents.job_card import render_cards, select_cards
from agents.preferences import preferences_in_scope
from database.db import ordered_blurbs
from database.vector_search import fts_search
from database.models import Achievement, Project, ProjectBlurb, User
from harness import rules as rules_mod
from harness.decisions.negative_pins import is_negative_pin

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


# ── project bullets without blurbs (#260) ────────────────────────────────────
# A project with no blurbs renders its description as its one bullet. A host that
# folds a tagline into the description ("Semantic search library. Open-source
# semantic search library using ...") then repeats itself on the page. At render
# time only, the leading sentence is dropped when it adds nothing: short, no
# number, and every content word is in the project's name or in the words right
# after it. Stored descriptions are never changed. When in doubt, keep.

_WORD = re.compile(r"[a-z0-9+#]+")
_SENTENCE_END = re.compile(r"(?<!\.)\.(?=\s)")
_ABBREVIATIONS = frozenset("e.g i.e vs etc inc ltd approx".split())
# Words that name a kind of project, not a technology: "library" restates "a library called X".
_TYPE_WORDS = frozenset(
    "app application api bot cli client dashboard engine extension framework game library lib"
    " module package platform plugin project repo repository script sdk server service site"
    " system tool tools toolkit website wrapper".split())
LEAD_IN_MAX_WORDS = 8
LEAD_IN_WINDOW = 12         # content words after the lead-in that must repeat its words
BULLET_MAX_WORDS = 30       # a description longer than this is not bullet-like


def _stem(word: str) -> str:
    return word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word


def _content_words(text: str) -> List[str]:
    return [_stem(w) for w in _WORD.findall((text or "").lower()) if w not in _STOP]


def drop_redundant_lead_in(description: str, name: str = "") -> str:
    """`description` without a first sentence that only restates the project, else
    unchanged (stripped). Rendering only: callers never store the result."""
    text = (description or "").strip()
    m = _SENTENCE_END.search(text)
    if not m:
        return text                          # one sentence: never drop the whole description
    lead, rest = text[:m.start()].strip(), text[m.end():].strip()
    words = lead.split()
    if (not rest or not words or len(words) > LEAD_IN_MAX_WORDS
            or re.search(r"\d|%", lead)       # a number is a fact
            or words[-1].lower() in _ABBREVIATIONS):
        return text
    content = set(_content_words(lead))
    if not content:
        return text
    name_words = set(_content_words(name))
    restates_name = bool(content & name_words) and content <= name_words | _TYPE_WORDS
    repeated_after = content <= set(_content_words(rest)[:LEAD_IN_WINDOW])
    return rest if (restates_name or repeated_after) else text


def _bullet_like(description: str) -> bool:
    """One short sentence on one line: fit to stand as a bullet as written."""
    text = (description or "").strip()
    return bool(text) and "\n" not in text and not _SENTENCE_END.search(text.rstrip(". ")) \
        and len(text.split()) <= BULLET_MAX_WORDS


def project_source_bullets(record: Dict) -> List[str]:
    """A project's bullets for the page: its blurbs, else its description with a
    redundant lead-in dropped. A project with blurbs is unchanged."""
    blurbs = [b["content"] for b in record.get("blurbs") or [] if b.get("content")]
    if blurbs:
        return blurbs
    desc = drop_redundant_lead_in(record.get("description") or "", record.get("name") or "")
    return [desc] if desc else []


def project_missing(record: Dict) -> List[str]:
    """`["blurbs"]` when a project has none and its description would not read as
    a bullet, so the host should offer to write some; else `[]`."""
    if any(b.get("content") for b in record.get("blurbs") or []):
        return []
    return [] if _bullet_like(record.get("description") or "") else ["blurbs"]


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
    for pid, blurbs in ordered_blurbs(rows).items():    # bullet order, #327
        out[str(pid)] = [{"style": b.style, "content": b.content} for b in blurbs]
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
            # Modeled as experiences are (`incomplete`, `missing`): the host offers to write bullets.
            record["missing"] = project_missing(record)
            record["incomplete"] = bool(record["missing"])
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
    _attach_links(user_id, items)
    return items


def _attach_links(user_id: UUID, items: List[Dict]) -> None:
    """Stored project links onto the records, in place. A role's or degree's
    search text gains the names of its projects, so "recommender" finds the
    job the recommender was built in."""
    if not any(it["kind"] in ("project", "experience", "education", "achievement")
               for it in items):
        return
    with Session(services.engine) as session:
        index = project_context.context_index(session, user_id)
        names = {p.project_id: p.name for p in session.exec(
            select(Project).where(Project.user_id == user_id)).all()}
        awards = {project_context.ach_key({"title": a.title}): names[a.project_id]
                  for a in session.exec(select(Achievement).where(
                      Achievement.user_id == user_id)).all()
                  if a.project_id in names}
    under: Dict[str, List[str]] = {}
    for pid, c in index.items():
        if c["key"]:
            under.setdefault(c["key"], []).append(names[pid])
    for it in items:
        record = it["record"]
        if it["kind"] == "project":
            try:
                c = index.get(UUID(str(record.get("id"))))
            except ValueError:
                c = None
            record["context_status"] = c["status"] if c else project_context.UNREVIEWED
            record["context"] = ({"key": c["key"], "kind": c["kind"], "title": c["title"]}
                                 if c and c["key"] else None)
        elif it["kind"] in ("experience", "education"):
            projects = sorted(under.get(it["key"], []))
            record["projects"] = [proj_key({"name": n}) for n in projects]
            if projects:
                it["text"] = f"{it['text']} {' '.join(projects)}"
        elif it["kind"] == "achievement":
            name = awards.get(it["key"])
            record["project"] = proj_key({"name": name}) if name else None


# ── tools ─────────────────────────────────────────────────────────────────────

def _pins(prefs: Sequence[Dict]) -> List[Dict[str, Any]]:
    """The strength-5 preferences among `prefs`, verbatim: the hard rules. A strength-5
    suppression the executor enforces is a negative pin (#198, #254): what it names can never
    reach the page."""
    return [
        {"text": p["text"], "polarity": p.get("polarity"), "target_key": p.get("target_key"),
         "target_term": p.get("target_term"), "scope": p.get("scope_type"),
         "scope_value": p.get("scope_value"),
         "negative_pin": is_negative_pin(p)}
        for p in prefs if (p.get("strength") or 0) >= PIN_STRENGTH
    ]


def scope_family(user_id: UUID, job_id: Optional[str] = None,
                 role_family: Optional[str] = None) -> Optional[str]:
    """The role family a scope resolves to: the one given, else the one `open_job` recorded for
    `job_id` (`library.role_family_of`, deterministic and model-free), else None. The one place
    preferences (#291) and document rules (#298) get their family from."""
    if job_id and not role_family:
        from harness import library  # library imports this module
        try:
            role_family = library.role_family_of(user_id, UUID(str(job_id)))[0]
        except ValueError:      # not a job id: scope by job alone
            pass
    return role_family


def preferences_for(user_id: UUID, job_id: Optional[str] = None,
                    role_family: Optional[str] = None) -> Tuple[List[Dict], Optional[str]]:
    """The active preferences in scope, and the role family that scoped them (#291).

    The one scope every reader uses — the briefing, the pins, the hook's restore after
    compaction and the executor — so what the host is shown is what `execute_plan` enforces.
    Global preferences always; a job's when `job_id` names it; a role family's when
    `role_family` names it, else the family `open_job` recorded for the job."""
    role_family = scope_family(user_id, job_id, role_family)
    prefs = preferences_in_scope(services.load_preferences(user_id),
                                 job_id=str(job_id) if job_id else None, role_family=role_family)
    return prefs, role_family


def rules_for(user_id: UUID, job_id: Optional[str] = None,
              role_family: Optional[str] = None) -> Tuple[List[Dict], Optional[str]]:
    """The active document rules in scope (#298), and the role family that scoped them: the same
    scope as `preferences_for`, so the briefing shows what `execute_plan` enforces."""
    role_family = scope_family(user_id, job_id, role_family)
    return rules_mod.in_scope(services.load_resume_rules(user_id), job_id, role_family), role_family


def art_pins(user_id: UUID, role_family: Optional[str] = None,
             job_id: Optional[str] = None) -> Dict[str, Any]:
    """The pins alone, word for word: what a host must restore after compaction (#202).
    The same set `art_briefing` pushes, scoped the same way (`preferences_for`). Strength-5
    document rules come with them (#298), as the rules the executor enforces: a narrower rule
    that cannot take effect is left out. `pending_bans` (#299) lists every suppression of a thing
    no item holds yet, whatever its strength: a strength-5 one is also in `pins`, and the executor
    enforces it as a page-wide term; a softer one is only here."""
    prefs, role_family = preferences_for(user_id, job_id, role_family)
    prefs = sorted(prefs, key=lambda p: str(p.get("preference_id")))
    pins = _pins(prefs)
    rules, _ = rules_for(user_id, job_id, role_family)
    hard = rules_mod.active_view(rules_mod.resolve(rules))["hard"]
    from harness.constraints import pending_bans  # constraints imports this module
    return {"role_family": role_family, "job_id": job_id, "pins": pins, "count": len(pins),
            "rules": hard, "pending_bans": pending_bans(prefs)}


def art_briefing(user_id: UUID, role_family: Optional[str] = None,
                 job_id: Optional[str] = None) -> Dict[str, Any]:
    """What the host must hold before planning, pushed rather than searched."""
    prefs, role_family = preferences_for(user_id, job_id, role_family)
    prefs = sorted(prefs, key=lambda p: (-(p.get("strength") or 0), str(p.get("preference_id"))))
    pins = _pins(prefs)
    persona = services.get_active_persona(user_id, role_family=role_family)
    cards = services.load_job_cards(user_id)
    if role_family:
        cards = select_cards(cards, role_family=role_family)
    from harness.ingest import list_rules  # harness/ingest imports this module
    rules = list_rules(user_id)
    page_rules, _ = rules_for(user_id, job_id, role_family)
    page = rules_mod.active_view(rules_mod.resolve(page_rules))
    return {
        "role_family": role_family,
        "job_id": job_id,
        "pins": pins,
        "rules": page,
        "preferences": [
            {k: p.get(k) for k in ("preference_id", "text", "polarity", "target_key",
                                   "target_term", "scope_type", "scope_value", "strength")}
            for p in prefs
        ],
        "persona_traits": persona.get("traits", []),
        "job_cards": render_cards(cards),
        "job_rules": rules,
        "counts": {"pins": len(pins), "preferences": len(prefs), "job_cards": len(cards),
                   "job_rules": len(rules), "page_rules": len(page_rules)},
    }


def _tokens(text: str) -> List[str]:
    return [t.rstrip(".") for t in _TOKEN.findall((text or "").lower())
            if t.rstrip(".") and t.rstrip(".") not in _STOP]


def _snippet(it: Dict) -> str:
    return (it["text"] or it["title"]).strip().replace("\n", " ")[:200]


def _item_preference(p: Dict) -> Dict[str, Any]:
    return {k: p.get(k) for k in ("preference_id", "text", "polarity", "target_key", "target_term",
                                  "scope_type", "scope_value", "strength")} | {
        "negative_pin": is_negative_pin(p)}


def _attach_attachments(user_id: UUID, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """What the user has said about each item, on the item (#297): `preferences` (the active ones
    keyed to its key, in every scope, each carrying its `scope_type` / `scope_value`, strength-5 first)
    and `job_rules` (`JobRule` rows whose `item_key` it is). One read of each table, not one per row;
    an item with neither gets two empty lists. Both are additive fields on the item records.

    The size of a list result is bounded by the number of active preferences and rules the user
    holds (each attaches to one item), not by the number of items, so `list_items` carries them in full
    instead of counts. A preference with no key (pending) is on no item."""
    by_pref: Dict[str, List[Dict]] = {}
    for p in services.load_preferences(user_id):
        key = (p.get("target_key") or "").strip().lower()
        if key:
            by_pref.setdefault(key, []).append(p)
    from harness.ingest import list_rules  # harness/ingest imports this module
    by_rule: Dict[str, List[Dict]] = {}
    for rule in list_rules(user_id):
        by_rule.setdefault(rule["item_key"].strip().lower(), []).append(rule)
    for row in rows:
        key = (row.get("key") or "").strip().lower()
        prefs = sorted(by_pref.get(key, []),
                       key=lambda p: (-(p.get("strength") or 0), str(p.get("preference_id"))))
        row["preferences"] = [_item_preference(p) for p in prefs]
        row["job_rules"] = list(by_rule.get(key, []))
    return rows


def kg_search(user_id: UUID, query: str, kinds: Optional[Sequence[str]] = None,
              limit: int = 10) -> List[Dict[str, Any]]:
    """Full-text search over the KG (#194): SQLite FTS5 with stemming and BM25,
    item names weighted over body text. A skill's name is indexed as body text:
    it is the skill's whole document, and at title weight every one-word skill
    outranked the experiences that use it (#189). Falls back to a plain token
    match when FTS5 is unavailable. Deterministic: score desc, then key asc."""
    q = sorted(set(_tokens(query)))
    if not q:
        return []
    items = _records(user_id, kinds)
    docs = [(it["key"], "" if it["kind"] == "skill" else it["title"],
             f"{it['title']} {it['text']}" if it["kind"] == "skill" else it["text"])
            for it in items]
    ranked = fts_search(docs, " ".join(q), weights=(_TITLE_WEIGHT, 1.0))
    if ranked is not None:
        by_key = {it["key"]: it for it in items}
        hits = [_with_context({"key": k, "kind": by_key[k]["kind"], "title": by_key[k]["title"],
                               "score": score, "snippet": _snippet(by_key[k])}, by_key[k])
                for k, score in ranked[: max(1, int(limit))]]
    else:
        hits = _lexical_search(items, q, limit)
    return _attach_attachments(user_id, hits)


def _with_context(hit: Dict[str, Any], item: Dict) -> Dict[str, Any]:
    """A project hit names the role or degree it was done under, so the host
    can follow the edge with get_item instead of searching again."""
    context = item["record"].get("context") if item["kind"] == "project" else None
    if context:
        hit["context"] = context["key"]
    return hit


def _lexical_search(items: List[Dict], q: List[str], limit: int) -> List[Dict[str, Any]]:
    """The pre-#194 scorer: title tokens count `_TITLE_WEIGHT`, body tokens 1."""
    hits = []
    for it in items:
        title = set(_tokens(it["title"]))
        body = set(_tokens(it["text"]))
        score = sum(_TITLE_WEIGHT if t in title else 1.0 if t in body else 0.0 for t in q)
        if score <= 0:
            continue
        hits.append(_with_context({"key": it["key"], "kind": it["kind"], "title": it["title"],
                                   "score": round(score, 3), "snippet": _snippet(it)}, it))
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
                out = {"key": it["key"], "kind": kind, "record": it["record"]}
                if kind in ("experience", "project"):
                    # The item's approved bullet variants (#229): wording the user confirmed,
                    # to start from instead of the raw bullets. Drafts are never listed.
                    from harness import library  # library imports this module
                    out["variants"] = library.list_variants(user_id, norm)
                return _attach_attachments(user_id, [out])[0]
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
    return _attach_attachments(user_id, sorted(
        ({"key": it["key"], "kind": it["kind"], "title": it["title"]} for it in items),
        key=lambda r: (r["kind"], r["key"])))


def suggest_project_contexts(user_id: UUID, include_reviewed: bool = False) -> Dict[str, Any]:
    """Where each unreviewed project was probably done, from deterministic rules
    (employer names in repo names, course codes). Never writes."""
    return {"projects": project_context.suggest_for_user(services.engine, user_id,
                                                          include_reviewed)}


def set_project_context(user_id: UUID, project: str, context: str) -> Dict[str, Any]:
    """Record the user's answer for one project. Writes."""
    return project_context.set_project_context(services.engine, user_id, project, context)


def link_achievement(user_id: UUID, achievement: str,
                     project: Optional[str] = None) -> Dict[str, Any]:
    """Tie an award to the project it was won for (None unties it). Writes."""
    return project_context.link_achievement(services.engine, user_id, achievement, project)


# Header fields only. Credentials (password hash, GitHub token), auth ids and raw
# scrape records never leave the store through a tool.
_PROFILE_FIELDS = ("name", "email", "phone", "location", "linkedin_url",
                   "github_username", "portfolio_url")
# A profile setting beside the header: where `render` saves the user's copy (#394).
_SETTING_FIELDS = ("default_output_dir",)


def get_profile(user_id: UUID) -> Dict[str, Any]:
    """Name and contact details for the resume header (#191; missing in #189), and
    where `render` saves the user's copy (#394)."""
    with Session(services.engine) as session:
        user = session.get(User, user_id)
        if user is None:
            return {"error": {"code": "not_found", "message": f"No user {user_id}."}}
        return {f: getattr(user, f, None) or None for f in _PROFILE_FIELDS + _SETTING_FIELDS}


def update_profile(user_id: UUID, fields: Dict[str, Any]) -> Dict[str, Any]:
    """Set header fields (#201): a fresh install's profile is "Default User".
    Only the fields `get_profile` returns; an empty string clears one, and a
    blank name is refused. `default_output_dir` must be downloads, desktop,
    documents or an absolute path, and is stored normalised (#394). Returns the
    profile as `get_profile` does."""
    from harness import destinations

    allowed = _PROFILE_FIELDS + _SETTING_FIELDS
    unknown = sorted(set(fields) - set(allowed))
    if unknown:
        return {"error": {"code": "invalid_arguments",
                          "message": f"Not a profile field: {', '.join(unknown)}.",
                          "suggestions": list(allowed)}}
    if "name" in fields and not str(fields["name"] or "").strip():
        return {"error": {"code": "invalid_arguments", "message": "name cannot be blank."}}
    if str(fields.get("default_output_dir") or "").strip():
        try:
            fields = dict(fields, default_output_dir=destinations.parse(
                fields["default_output_dir"]))
        except destinations.DestinationError as exc:
            return {"error": {"code": "invalid_arguments", "message": str(exc),
                              "suggestions": list(destinations.WELL_KNOWN)}}
    with Session(services.engine) as session:
        user = session.get(User, user_id)
        if user is None:
            return {"error": {"code": "not_found", "message": f"No user {user_id}."}}
        for field, value in fields.items():
            setattr(user, field, (str(value).strip() or None) if value is not None else None)
        session.add(user)
        session.commit()
    return get_profile(user_id)


# ── document rules (#298) ────────────────────────────────────────────────────

def _rule_view(rule: Dict[str, Any]) -> Dict[str, Any]:
    """A stored rule as the host reads it back, with how it is enforced."""
    kind, hard = rule["kind"], (rule.get("strength") or 0) >= PIN_STRENGTH
    if kind == "style_note":
        enforcement = ("pushed to the host verbatim in art_briefing and never blocks, whatever its "
                       "strength: a style note is advice, not a check")
    elif hard:
        enforcement = ("hard: a plan that loosens it is refused at finalize, and a narrower scope "
                       "may only tighten it")
    else:
        enforcement = ("soft: never a gate. A default a plan may override where one can be applied "
                       "(a section left off, the section order, the skills list); otherwise "
                       "measured. The finalize result reports it applied or not_applied")
    return {"rule_id": rule["rule_id"], "kind": kind, "params": rule.get("params") or {},
            "text": rule["text"], "scope_type": rule["scope_type"],
            "scope_value": rule.get("scope_value"), "strength": rule["strength"],
            "status": rule.get("status", "active"), "hard": hard and kind != "style_note",
            "enforcement": enforcement}


def record_rule(user_id: UUID, text: str, kind: str, params: Optional[Dict[str, Any]] = None,
                strength: int = 3, scope: str = "global", scope_value: Optional[str] = None,
                quote: Optional[str] = None, routed_from: Optional[str] = None) -> Dict[str, Any]:
    """Store one document rule the user stated or confirmed, or refuse it with a reason (#298).

    `kind` is `limit`, `section_order` or `style_note`; `params` is validated for the kind
    (`harness/rules.py`). A `limit` given with no params is read from the words only when they
    state exactly one plain thing ("one page", "no projects section"); otherwise it is refused
    and the host sends the params. A rule of the same kind, scope and subject already held is
    superseded, never deleted. A rule that would loosen a broader hard rule is refused, naming
    that rule: a narrower scope may only tighten."""
    def refuse(reason: str, **extra: Any) -> Dict[str, Any]:
        return {"status": "refused", "reason": reason, **extra}

    text = " ".join((text or "").split())
    if not text:
        return refuse("text is empty: give the rule in the user's own words.")
    if kind not in rules_mod.KINDS:
        return refuse(f"kind must be one of {', '.join(rules_mod.KINDS)}.")
    if not isinstance(strength, int) or isinstance(strength, bool) or not 1 <= strength <= 5:
        return refuse("strength must be 1 (a passing remark) to 5 (an absolute rule).")
    if scope not in rules_mod.SCOPES:
        return refuse(f"scope must be one of {', '.join(rules_mod.SCOPES)}.")
    scope_value = (scope_value or "").strip() or None
    family = None
    if scope == "job":
        try:
            UUID(str(scope_value))
        except ValueError:
            return refuse("a job-scoped rule needs scope_value: the job id (see list_jobs).")
        family = scope_family(user_id, scope_value)
    elif scope == "role_family":
        if not scope_value:
            return refuse("a role_family rule needs scope_value: the role family, e.g. data_science.")
        scope_value = scope_value.lower()
    else:
        scope_value = None

    params = dict(params or {})
    if kind == "limit" and not params:
        params = rules_mod.infer_limit(text) or {}
        if not params:
            return refuse("the words do not state one plain limit, so say which: params for a "
                          f"limit are {rules_mod.SHAPES['limit']}.", shape=rules_mod.SHAPES["limit"])
    if kind == "section_order" and not params:
        return refuse("a section_order rule needs params: " + rules_mod.SHAPES["section_order"] + ".",
                      shape=rules_mod.SHAPES["section_order"])
    try:
        params = rules_mod.normalize_params(kind, params)
    except rules_mod.RuleError as exc:
        return refuse(str(exc), shape=rules_mod.SHAPES[kind])

    provenance = {"source": "host", "quote": quote or text}
    if routed_from:
        provenance["routed_from"] = routed_from
    candidate = {"kind": kind, "params": params, "text": text, "scope_type": scope,
                 "scope_value": scope_value, "strength": strength, "provenance": provenance}
    active = services.load_resume_rules(user_id)
    blocker = rules_mod.loosens_hard(candidate, active, family)
    if blocker is not None:
        return refuse(
            f"a {scope} rule may only tighten {rules_mod.describe(blocker)}, which is hard "
            f"(strength 5) at {blocker['scope_type']} scope: the narrower rule would loosen it, so "
            "it could never take effect. Ask the user whether to change the broader rule "
            "(retract_rule, then record it again).",
            blocked_by=_rule_view(blocker))

    same = [r for r in active if rules_mod.identity(r) == rules_mod.identity(candidate)]
    for r in same:
        if (r["params"] == params and r["strength"] == strength
                and " ".join(r["text"].split()) == text):
            return {"status": "already_recorded", "rule": _rule_view(r)}
    stored = services.save_resume_rule(user_id, candidate, supersedes=[r["rule_id"] for r in same])
    if stored is None:
        return refuse("the rule could not be stored.")
    return {"status": "superseded" if same else "stored", "rule": _rule_view(stored),
            "supersedes": same[0]["text"] if same else None}


def retract_rule(user_id: UUID, rule_id: str) -> Dict[str, Any]:
    """Withdraw a document rule: it stops applying and stays on record (#298)."""
    row = services.retract_resume_rule(user_id, rule_id)
    if row is None:
        return {"status": "not_found", "reason": f"No rule {rule_id!r} of yours."}
    status = "retracted" if row["status"] == "retracted" else "already_inactive"
    return {"status": status, "rule": _rule_view(row)}
