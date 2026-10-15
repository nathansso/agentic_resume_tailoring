"""The payload behind the evidence map (issue #136).

One caller's experiences, education and projects on one side, their skills on the
other, and an edge for every (item, skill) pair with evidence: the bullets that
name the skill, or, for a project, the GitHub repo that reported it. It is
assembled on top of `SkillGraphBuilder.build_graph()` and does not change it: that
graph feeds matching and tailoring, and the GitHub provenance edges added here are
for this view only.

**Bullet numbers.** `cites` on an edge are the ordinals the harness cites by
(`<item key>#b<n>`, `harness/executor.py::_KG`): an experience's bullets and a
project's blurbs are numbered over their non-empty entries, projects through
`database.db.ordered_blurbs` (#327). Each entry in an item's `text` carries both
that `index` (None for an empty entry, which has no ordinal) and its `raw_index`,
its position in the stored list, which is what an editor writes back.
"""
import re
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlmodel import Session, select

from agents.checks import exp_key, proj_key
from agents.skill_matching import SkillMatcher
from database.db import engine, ordered_blurbs
from database.models import (
    Achievement, Education, Experience, Project, ProjectBlurb, Skill, UserSkill,
)
from knowledge_graph.areas import skill_areas
from knowledge_graph.builder import SkillGraphBuilder, repo_slug

EMPTY_VIEW: Dict[str, Any] = {
    "items": [], "skills": [], "edges": [],
    "stats": {"items": 0, "skills": 0, "evidenced": 0, "unevidenced": 0,
              "github_only": 0, "needs_text": 0},
}


def _names_repo(haystack: str, slug: str) -> bool:
    """Whether `haystack` names the repo `slug` as a whole `owner/repo`: not as the start or
    end of a longer one (`owner/app` is not `owner/app-server`, `owner/ap` is not `owner/app`)."""
    return re.search(r"(?<![\w.-])" + re.escape(slug) + r"(?![\w.-])", haystack) is not None


def skill_source_kind(evidence_source: Optional[str]) -> str:
    """The kind of source behind a UserSkill row: resume, github, linkedin, manual, chat or host."""
    src = (evidence_source or "").strip().lower()
    for kind in ("github", "linkedin", "manual", "chat", "host"):
        if src.startswith(kind):
            return kind
    return "resume"     # a file path, "resume", or anything unrecognised


def _entries(texts: List[str], blurb_ids: Optional[List[Optional[str]]] = None) -> List[Dict[str, Any]]:
    """`text` entries for one item: raw position, harness ordinal (non-empty only), content."""
    out: List[Dict[str, Any]] = []
    ordinal = 0
    for raw_index, text in enumerate(texts):
        has_text = bool(text)           # the builder and the harness both filter on truthiness
        out.append({
            "raw_index": raw_index,
            "index": ordinal if has_text else None,
            "text": str(text) if text is not None else "",
            "blurb_id": blurb_ids[raw_index] if blurb_ids else None,
        })
        if has_text:
            ordinal += 1
    return out


def _experience_item(e: Experience) -> Dict[str, Any]:
    bullets = list(e.bullets or [])
    entries = _entries(bullets)
    return {
        "key": f"exp/{e.experience_id}", "id": str(e.experience_id), "kind": "exp",
        "title": e.title, "org": e.company,
        "start": e.start_date or "", "end": e.end_date or "",
        "description": e.description or "",
        "text": entries,
        "text_source": "bullets" if any(x["index"] is not None for x in entries) else "none",
        "cite_key": exp_key({"title": e.title, "company": e.company}),
        "parent_key": None, "achievements": [], "repo_url": "",
        "fields": {"title": e.title, "company": e.company, "start_date": e.start_date or "",
                   "end_date": e.end_date or "", "description": e.description or ""},
    }


def _project_item(p: Project, blurbs: List[ProjectBlurb]) -> Dict[str, Any]:
    if any(b.content for b in blurbs):
        entries = _entries([b.content for b in blurbs], [str(b.blurb_id) for b in blurbs])
        source = "blurbs"
    elif (p.description or "").strip():
        entries = _entries([p.description or ""])
        source = "description"
    else:
        entries, source = [], "none"
    return {
        "key": f"proj/{p.project_id}", "id": str(p.project_id), "kind": "proj",
        "title": p.name, "org": "",
        "start": p.start_date or "", "end": p.end_date or "",
        "description": p.description or "",
        "text": entries, "text_source": source,
        "cite_key": proj_key({"name": p.name}),
        "parent_key": None, "achievements": [], "repo_url": p.repo_url or "",
        "fields": {"name": p.name, "description": p.description or "",
                   "repo_url": p.repo_url or "", "start_date": p.start_date or "",
                   "end_date": p.end_date or ""},
    }


def _education_item(e: Education) -> Dict[str, Any]:
    return {
        "key": f"edu/{e.education_id}", "id": str(e.education_id), "kind": "edu",
        "title": e.degree or e.institution, "org": e.institution,
        "start": e.start_date or "", "end": e.end_date or "",
        "description": "",
        # Education names no skill edges (the builder derives none), so it has no text to link from.
        "text": [], "text_source": "fields", "cite_key": "",
        "parent_key": None, "achievements": [], "repo_url": "",
        "fields": {"institution": e.institution, "degree": e.degree or "",
                   "location": e.location or "", "start_date": e.start_date or "",
                   "end_date": e.end_date or "", "gpa": e.gpa or ""},
    }


def cited_skill_keys(user_id: Optional[UUID]) -> set:
    """Keys (`skill/<id>`) of the user's skills that at least one bullet cites: the ones a
    dismissal is refused for (#321). Read off the view itself, not a second matcher."""
    view = build_evidence_view(user_id)
    return {e["skill_key"] for e in view["edges"] if e["cites"]}


def build_evidence_view(user_id: Optional[UUID]) -> Dict[str, Any]:
    """The evidence map payload for one user. Every row read is scoped to `user_id`."""
    if user_id is None:
        return {**EMPTY_VIEW, "stats": dict(EMPTY_VIEW["stats"])}

    graph = SkillGraphBuilder(user_id).build_graph()

    with Session(engine) as session:
        exps = session.exec(
            select(Experience).where(Experience.user_id == user_id)
            .order_by(Experience.seq.is_(None), Experience.seq, Experience.created_at,
                      Experience.experience_id)).all()
        edus = session.exec(
            select(Education).where(Education.user_id == user_id)
            .order_by(Education.seq.is_(None), Education.seq, Education.created_at,
                      Education.education_id)).all()
        projs = session.exec(
            select(Project).where(Project.user_id == user_id)
            .order_by(Project.seq.is_(None), Project.seq, Project.created_at,
                      Project.project_id)).all()
        achievements = session.exec(
            select(Achievement).where(Achievement.user_id == user_id)
            .order_by(Achievement.seq.is_(None), Achievement.seq, Achievement.created_at,
                      Achievement.achievement_id)).all()
        blurbs = ordered_blurbs(session.exec(
            select(ProjectBlurb).where(ProjectBlurb.project_id.in_([p.project_id for p in projs]))
        ).all()) if projs else {}
        links = session.exec(
            select(UserSkill, Skill).join(Skill, Skill.skill_id == UserSkill.skill_id)
            .where(UserSkill.user_id == user_id)).all()

    # ── items ────────────────────────────────────────────────────────────────
    items: List[Dict[str, Any]] = [_experience_item(e) for e in exps]
    items += [_education_item(e) for e in edus]
    items += [_project_item(p, blurbs.get(p.project_id, [])) for p in projs]
    by_id = {it["id"]: it for it in items}
    by_key = {it["key"]: it for it in items}
    for p in projs:
        item = by_id[str(p.project_id)]
        parent = by_id.get(str(p.experience_id)) if p.experience_id else None
        parent = parent or (by_id.get(str(p.education_id)) if p.education_id else None)
        item["parent_key"] = parent["key"] if parent else None
    for a in achievements:
        host = by_id.get(str(a.project_id)) if a.project_id else None
        if host is not None:
            host["achievements"].append({"id": str(a.achievement_id), "title": a.title,
                                         "issuer": a.issuer or "", "date": a.date or ""})

    # ── skills ───────────────────────────────────────────────────────────────
    skill_nodes = {d["name"]: d for _, d in graph.nodes(data=True) if d.get("type") == "Skill"}
    meta: Dict[str, Dict[str, Any]] = {}
    for us, skill in links:
        m = meta.setdefault(skill.name, {"proficiency": None, "sources": set(), "is_core": False})
        if us.proficiency is not None:
            m["proficiency"] = max(m["proficiency"] or 0, us.proficiency)
        m["sources"].add(skill_source_kind(us.evidence_source))
        m["is_core"] = m["is_core"] or bool(us.is_core)

    areas = skill_areas((n, skill_nodes[n].get("category")) for n in skill_nodes)   # one cache read
    skills: List[Dict[str, Any]] = []
    skill_key_by_name: Dict[str, str] = {}
    for name in sorted(skill_nodes, key=lambda n: (n.lower(), n)):
        node = skill_nodes[name]
        m = meta.get(name, {"proficiency": None, "sources": set(), "is_core": False})
        key = f"skill/{node['id']}"
        skill_key_by_name[name] = key
        skills.append({
            "key": key, "name": name, "category": node.get("category") or "",
            "area": areas[(name, node.get("category"))],
            "proficiency": m["proficiency"], "sources": sorted(m["sources"]),
            "is_core": m["is_core"],
        })

    # ── edges ────────────────────────────────────────────────────────────────
    edges: Dict[tuple, Dict[str, Any]] = {}
    for u, v, data in graph.edges(data=True):
        if data.get("relation") not in ("USES", "DEMONSTRATES"):
            continue
        item = by_id.get(str(graph.nodes[u].get("id")))
        skill_key = skill_key_by_name.get(graph.nodes[v].get("name"))
        if item is None or skill_key is None:
            continue
        edges[(item["key"], skill_key)] = {
            "item_key": item["key"], "skill_key": skill_key,
            "cites": sorted(data.get("bullets") or []), "github": False,
        }

    # GitHub provenance: `UserSkill.evidence_source` records `github:owner/repo` at ingest,
    # a recorded fact where the text match is a guess. View-only; the graph is not changed.
    by_slug: Dict[str, List[Dict[str, Any]]] = {}
    for p in projs:
        slug = repo_slug(p.repo_url)
        if slug:
            by_slug.setdefault(slug, []).append(by_id[str(p.project_id)])
    if by_slug:
        for us, skill in links:
            skill_key = skill_key_by_name.get(skill.name)
            haystack = f"{us.evidence_source or ''} {us.evidence_detail or ''}".lower()
            if skill_key is None or not haystack.strip():
                continue
            for slug, matched in by_slug.items():
                if not _names_repo(haystack, slug):
                    continue
                for item in matched:
                    edge = edges.setdefault((item["key"], skill_key), {
                        "item_key": item["key"], "skill_key": skill_key,
                        "cites": [], "github": False})
                    edge["github"] = True

    # A link that rests only on a role's title or a project's name has no bullet and no
    # recorded source behind it: it is not evidence, so the map does not draw it.
    edge_list = sorted((e for e in edges.values() if e["cites"] or e["github"]),
                       key=lambda e: (e["item_key"], e["skill_key"]))

    # Which words of each citing bullet name the skill, so the panel can highlight them
    # (an alias such as "torch" for PyTorch included). `marks[i]` belongs to `cites[i]`.
    name_by_key = {s["key"]: s["name"] for s in skills}
    matchers: Dict[str, SkillMatcher] = {}
    for edge in edge_list:
        item = by_key[edge["item_key"]]
        by_ordinal = {t["index"]: t["text"] for t in item["text"] if t["index"] is not None}
        matcher = matchers.setdefault(edge["skill_key"], SkillMatcher(name_by_key[edge["skill_key"]]))
        edge["marks"] = [[list(span) for span in matcher.spans(by_ordinal.get(n, ""))]
                         for n in edge["cites"]]

    # ── stats ────────────────────────────────────────────────────────────────
    cited = {e["skill_key"] for e in edge_list if e["cites"]}
    github = {e["skill_key"] for e in edge_list if e["github"]} - cited
    stats = {
        "items": len(items), "skills": len(skills),
        "evidenced": len(cited),
        "github_only": len(github),
        "unevidenced": len(skills) - len(cited) - len(github),
        "needs_text": sum(1 for it in items if it["text_source"] == "none"),
    }
    return {"items": items, "skills": skills, "edges": edge_list, "stats": stats}
