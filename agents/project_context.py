"""Project context: where a project was done.

A project is done under at most one *context*, a role (`Experience`) or a degree
(`Education`), or it is personal. That is the graph's
`Project -PART_OF-> Experience | Education` edge; `Achievement -AWARDED_FOR->
Project` ties an award (a hackathon placing) to the project it was won for.

- `suggest_contexts` proposes links from deterministic rules. It never writes.
- `set_project_context` and `link_achievement` are the only write paths, and
  are reached only on an explicit user confirmation: the same write barrier as
  chat-captured artifacts (#21) and preferences (#129). A link inferred and
  stored without the user would be cited back to them as fact.
- `repoint` and `unlink` keep the edge valid when heal merges or deletes rows.

`context_status` is what separates "personal" (confirmed: no role or degree)
from "unreviewed" (nobody has said yet); two null ids cannot tell them apart.

Model-free, so the harness can import it (tests/test_harness_boundary.py).
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple
from uuid import UUID

from sqlmodel import Session, select

from agents.checks import exp_key, proj_key
from database.clock import utc_now
from database.models import Achievement, Education, Experience, Project

UNREVIEWED, LINKED, PERSONAL = "unreviewed", "linked", "personal"
STATUSES = (UNREVIEWED, LINKED, PERSONAL)

# Company-name words too generic to tie a repo to an employer.
_STOP = frozenset(
    "inc llc ltd corp co company the of and at for department dept university "
    "college school institute group labs lab team".split())
_TOKEN = re.compile(r"[a-z0-9]+")
# A course code in a repo name: 2-4 subject letters, an optional separator, a
# 2-3 digit number, an optional section letter (dsc30-pa0, DSC-80, cse101b,
# dsc207miniproject). The lookbehind keeps it from starting mid-word.
_COURSE = re.compile(r"(?<![a-z])([a-z]{2,4})[-_ ]?(\d{2,3})[a-z]?(?![0-9])")
# Course numbers at or above this read as graduate courses. The US convention
# at UC schools (lower division < 100, upper < 200, graduate 200+); elsewhere
# the suggestion may pick the wrong degree, which is why it is only a suggestion.
GRADUATE_COURSE_MIN = 200


# ── keys ──────────────────────────────────────────────────────────────────────

def edu_key(e: Dict) -> str:
    """Same formula as `harness.tools.edu_key` (a test holds them together);
    kept here so agents/ never imports harness/."""
    degree = e.get("degree")
    degree = "" if degree in (None, "—") else degree
    return f"edu:{(str(e.get('institution') or '')).strip().lower()}|{str(degree).strip().lower()}"


def ach_key(a: Dict) -> str:
    """Same formula as `harness.tools.ach_key`."""
    return f"ach:{(str(a.get('title') or '')).strip().lower()}"


def _exp_dict(e: Experience) -> Dict:
    return {"title": e.title, "company": e.company}


def _edu_dict(e: Education) -> Dict:
    return {"institution": e.institution, "degree": e.degree}


def _exp_title(e: Experience) -> str:
    return f"{e.title} @ {e.company}"


def _edu_title(e: Education) -> str:
    return f"{e.institution} — {e.degree}" if e.degree else e.institution


# ── rules (pure) ──────────────────────────────────────────────────────────────

def _tokens(text: Any) -> List[str]:
    return _TOKEN.findall(str(text or "").lower())


def employer_tokens(company: Any) -> set:
    """Distinctive words of a company name: 'IDX Exchange' -> {idx, exchange}."""
    return {t for t in _tokens(company) if len(t) >= 3 and not t.isdigit() and t not in _STOP}


def course_code(name: Any) -> Optional[Tuple[str, int]]:
    """('dsc', 207) for 'dsc207miniproject', or None."""
    m = _COURSE.search(str(name or "").lower())
    return (m.group(1), int(m.group(2))) if m else None


def degree_level(degree: Any) -> str:
    """'graduate', 'undergraduate', or '' when the degree string says neither."""
    d = str(degree or "").lower()
    if re.search(r"\b(ph\.?\s?d|doctor|master|m\.?\s?s\b|m\.?\s?a\b|mba|m\.?\s?eng|m\.?\s?sc)", d):
        return "graduate"
    if re.search(r"\b(bachelor|b\.?\s?s\b|b\.?\s?a\b|b\.?\s?sc|b\.?\s?eng|associate)", d):
        return "undergraduate"
    return ""


def _employer_candidates(project: Dict, experiences: Sequence[Dict]) -> List[Dict]:
    name_tokens = set(_tokens(project.get("name"))) | set(_tokens(_repo_name(project)))
    desc = " ".join(_tokens(project.get("description")))
    out = []
    for e in experiences:
        shared = sorted(employer_tokens(e.get("company")) & name_tokens)
        company = " ".join(_tokens(e.get("company")))
        if shared:
            out.append((e, f"repo name shares '{shared[0]}' with {e.get('company')}"))
        elif company and len(company) >= 3 and re.search(rf"\b{re.escape(company)}\b", desc):
            out.append((e, f"description names {e.get('company')}"))
    return [{"context_key": exp_key(e), "kind": "experience",
             "title": f"{e.get('title')} @ {e.get('company')}", "rule": "employer",
             "reason": reason} for e, reason in out]


def _course_candidates(project: Dict, education: Sequence[Dict]) -> List[Dict]:
    code = course_code(project.get("name")) or course_code(_repo_name(project))
    if not code or not education:
        return []
    subject, number = code
    level = "graduate" if number >= GRADUATE_COURSE_MIN else "undergraduate"
    at_level = [e for e in education if degree_level(e.get("degree")) == level]
    picks = at_level or (list(education) if len(education) == 1 else [])
    reason = (f"course code {subject.upper()} {number} reads as a {level} course"
              if at_level else f"course code {subject.upper()} {number}; your only degree")
    return [{"context_key": edu_key(e), "kind": "education",
             "title": f"{e.get('institution')} — {e.get('degree')}", "rule": "course_code",
             "reason": reason} for e in picks]


def _repo_name(project: Dict) -> str:
    url = str(project.get("repo_url") or "").rstrip("/")
    return url.rsplit("/", 1)[-1] if "/" in url else ""


def suggest_contexts(projects: Sequence[Dict], experiences: Sequence[Dict],
                     education: Sequence[Dict], include_reviewed: bool = False) -> List[Dict]:
    """One entry per project: its current context and rule-based candidates.

    Rules in order, first that fires wins: (1) employer: a distinctive word of
    a company name appears in the repo name, or the whole company name in the
    description; (2) course code: the repo name carries one, mapped to the
    degree at the matching level. No rule proposes "personal": that can only be
    the user's answer. Pure; never writes.
    """
    out = []
    for p in projects:
        status = p.get("context_status") or UNREVIEWED
        if status != UNREVIEWED and not include_reviewed:
            continue
        candidates = _employer_candidates(p, experiences) or _course_candidates(p, education)
        out.append({"project_key": proj_key(p), "project": p.get("name"),
                    "context_status": status, "context_key": p.get("context_key"),
                    "suggestions": candidates})
    return out


# ── reads ─────────────────────────────────────────────────────────────────────

def _user_rows(session: Session, user_id: UUID):
    exps = session.exec(select(Experience).where(Experience.user_id == user_id)
                        .order_by(Experience.seq.is_(None), Experience.seq,
                                  Experience.created_at, Experience.experience_id)).all()
    edus = session.exec(select(Education).where(Education.user_id == user_id)
                        .order_by(Education.seq.is_(None), Education.seq,
                                  Education.created_at, Education.education_id)).all()
    projs = session.exec(select(Project).where(Project.user_id == user_id)
                         .order_by(Project.seq.is_(None), Project.seq,
                                   Project.created_at, Project.project_id)).all()
    return list(exps), list(edus), list(projs)


def context_index(session: Session, user_id: UUID) -> Dict[UUID, Dict]:
    """project_id -> {status, kind, key, title} for every project of this user.
    kind/key/title are None unless the project is linked to a row that exists."""
    exps, edus, projs = _user_rows(session, user_id)
    exp_by_id = {e.experience_id: e for e in exps}
    edu_by_id = {e.education_id: e for e in edus}
    out: Dict[UUID, Dict] = {}
    for p in projs:
        entry = {"status": p.context_status or UNREVIEWED, "kind": None, "key": None, "title": None}
        if p.experience_id in exp_by_id:
            e = exp_by_id[p.experience_id]
            entry.update(kind="experience", key=exp_key(_exp_dict(e)), title=_exp_title(e))
        elif p.education_id in edu_by_id:
            e = edu_by_id[p.education_id]
            entry.update(kind="education", key=edu_key(_edu_dict(e)), title=_edu_title(e))
        out[p.project_id] = entry
    return out


def suggest_for_user(engine, user_id: UUID, include_reviewed: bool = False) -> List[Dict]:
    """`suggest_contexts` over this user's stored rows."""
    with Session(engine) as session:
        exps, edus, projs = _user_rows(session, user_id)
        index = context_index(session, user_id)
        projects = [{"name": p.name, "repo_url": p.repo_url, "description": p.description,
                     "context_status": p.context_status, "context_key": index[p.project_id]["key"]}
                    for p in projs]
        return suggest_contexts(
            projects,
            [{"title": e.title, "company": e.company} for e in exps],
            [{"institution": e.institution, "degree": e.degree} for e in edus],
            include_reviewed=include_reviewed)


# ── writes (explicit confirmation only) ──────────────────────────────────────

def _error(code: str, message: str, suggestions: Sequence[str] = ()) -> Dict:
    return {"error": {"code": code, "message": message, "suggestions": list(suggestions)}}


def _find_project(projs: Sequence[Project], ref: str) -> Optional[Project]:
    ref = (ref or "").strip()
    for p in projs:
        if str(p.project_id) == ref:
            return p
    key = ref.lower() if ref.lower().startswith("proj:") else proj_key({"name": ref})
    return next((p for p in projs if proj_key({"name": p.name}) == key), None)


def set_project_context(engine, user_id: UUID, project: str, context: str) -> Dict:
    """Record where `project` (a key, name or id) was done.

    `context` is an experience key (`exp:<title>|<company>`), an education key
    (`edu:<institution>|<degree>`), `personal`, or `unreviewed` to clear it.
    Only rows owned by `user_id` resolve, so a client-supplied key can never
    link into another user's graph.
    """
    context = (context or "").strip()
    with Session(engine) as session:
        exps, edus, projs = _user_rows(session, user_id)
        row = _find_project(projs, project)
        if row is None:
            return _error("not_found", f"No project {project!r}.",
                          [proj_key({"name": p.name}) for p in projs][:10])
        exp_id = edu_id = None
        low = context.lower()
        if low in (PERSONAL, UNREVIEWED):
            status = low
        elif low.startswith("exp:"):
            match = next((e for e in exps if exp_key(_exp_dict(e)) == low), None)
            if match is None:
                return _error("not_found", f"No experience {context!r}.",
                              [exp_key(_exp_dict(e)) for e in exps])
            exp_id, status = match.experience_id, LINKED
        elif low.startswith("edu:"):
            match = next((e for e in edus if edu_key(_edu_dict(e)) == low), None)
            if match is None:
                return _error("not_found", f"No education {context!r}.",
                              [edu_key(_edu_dict(e)) for e in edus])
            edu_id, status = match.education_id, LINKED
        else:
            return _error("invalid_context",
                          "context must be an exp: or edu: key, 'personal' or 'unreviewed'.")
        row.experience_id, row.education_id, row.context_status = exp_id, edu_id, status
        row.updated_at = utc_now()
        session.add(row)
        session.commit()
        entry = context_index(session, user_id)[row.project_id]
        return {"project_key": proj_key({"name": row.name}), "context_status": entry["status"],
                "context_key": entry["key"]}


def link_achievement(engine, user_id: UUID, achievement: str, project: Optional[str]) -> Dict:
    """Tie an award (key or title) to the project it was won for, or untie it
    with `project=None`."""
    with Session(engine) as session:
        achs = session.exec(select(Achievement).where(Achievement.user_id == user_id)
                            .order_by(Achievement.seq.is_(None), Achievement.seq,
                                      Achievement.created_at, Achievement.achievement_id)).all()
        ref = (achievement or "").strip().lower()
        key = ref if ref.startswith("ach:") else ach_key({"title": ref})
        ach = next((a for a in achs if ach_key({"title": a.title}) == key), None)
        if ach is None:
            return _error("not_found", f"No achievement {achievement!r}.",
                          [ach_key({"title": a.title}) for a in achs])
        target = None
        if project:
            _, _, projs = _user_rows(session, user_id)
            target = _find_project(projs, project)
            if target is None:
                return _error("not_found", f"No project {project!r}.",
                              [proj_key({"name": p.name}) for p in projs][:10])
        ach.project_id = target.project_id if target else None
        ach.updated_at = utc_now()
        session.add(ach)
        session.commit()
        return {"achievement_key": key,
                "project_key": proj_key({"name": target.name}) if target else None}


# ── integrity: merges and deletes ────────────────────────────────────────────

def repoint(session: Session, model, column: str, old_id: UUID, new_id: UUID) -> int:
    """Move every link from a row heal is deleting onto the row that survives."""
    rows = session.exec(select(model).where(getattr(model, column) == old_id)).all()
    for r in rows:
        setattr(r, column, new_id)
        session.add(r)
    return len(rows)


def unlink(session: Session, model, column: str, old_id: UUID) -> int:
    """Drop every link to a row being deleted. A project that loses its context
    goes back to 'unreviewed': the user said where it was done, not that it was
    personal."""
    rows = session.exec(select(model).where(getattr(model, column) == old_id)).all()
    for r in rows:
        setattr(r, column, None)
        if model is Project:
            r.context_status = UNREVIEWED
        session.add(r)
    return len(rows)
