"""
Shared DB query and ingestion service functions.
Query functions return plain data (lists/dicts) for the web API and CLI.
Ingestion functions return plain-English result strings and never raise.
"""
import contextlib
import io
import json
import logging
import sys
from pathlib import Path
from typing import Optional
from uuid import UUID

_ENV_PATH = Path(__file__).parent.parent / ".env"

from sqlmodel import Session, delete, select

from agents.skill_selection import skill_names
from database.db import engine
from database.models import (
    Achievement, ChatMessage, DeletedEntry, Education, Experience,
    JobDescription, JobSkill, Project, Skill, User, UserJobResult, UserSkill,
)

logger = logging.getLogger(__name__)


@contextlib.contextmanager
def _suppress_output():
    """Redirect stdout/stderr during heavy ingestion to keep library output off the console."""
    buf = io.StringIO()
    old_stdout, old_stderr = sys.stdout, sys.stderr
    sys.stdout = buf
    sys.stderr = buf
    try:
        yield
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr


def _snapshot_user_data(user_id: UUID) -> tuple[set, set, set]:
    """Return (skill_ids, exp_ids, proj_ids) for the user before ingestion."""
    with Session(engine) as session:
        skill_ids = {us.skill_id for us in session.exec(
            select(UserSkill).where(UserSkill.user_id == user_id)
        ).all()}
        exp_ids = {str(e.experience_id) for e in session.exec(
            select(Experience).where(Experience.user_id == user_id)
        ).all()}
        proj_ids = {str(p.project_id) for p in session.exec(
            select(Project).where(Project.user_id == user_id)
        ).all()}
    return skill_ids, exp_ids, proj_ids


def _format_ingestion_diff(
    user_id: UUID,
    pre_skill_ids: set,
    pre_exp_ids: set,
    pre_proj_ids: set,
    label: str,
) -> str:
    """Return a human-readable summary of what was added during ingestion."""
    with Session(engine) as session:
        # New skills: skill_ids not seen before this ingestion
        new_skill_names = []
        for us in session.exec(
            select(UserSkill).where(UserSkill.user_id == user_id)
        ).all():
            if us.skill_id not in pre_skill_ids:
                skill = session.get(Skill, us.skill_id)
                if skill and skill.name not in new_skill_names:
                    new_skill_names.append(skill.name)
                pre_skill_ids.add(us.skill_id)  # dedupe within this diff

        # New experiences
        new_exps = []
        for e in session.exec(
            select(Experience).where(Experience.user_id == user_id)
        ).all():
            if str(e.experience_id) not in pre_exp_ids:
                new_exps.append(f"{e.title} @ {e.company}")

        # New projects
        new_projs = []
        for p in session.exec(
            select(Project).where(Project.user_id == user_id)
        ).all():
            if str(p.project_id) not in pre_proj_ids:
                new_projs.append(p.name)

        total_skills = len(session.exec(
            select(UserSkill).where(UserSkill.user_id == user_id)
        ).all())
        total_exps = len(session.exec(
            select(Experience).where(Experience.user_id == user_id)
        ).all())
        total_projs = len(session.exec(
            select(Project).where(Project.user_id == user_id)
        ).all())

    lines = [f"Ingested: {label}", ""]

    if new_skill_names:
        preview = ", ".join(new_skill_names[:12])
        if len(new_skill_names) > 12:
            preview += f" (+{len(new_skill_names) - 12} more)"
        lines.append(f"New skills ({len(new_skill_names)}): {preview}")
    else:
        lines.append("New skills (0): all skills already on your profile")

    if new_exps:
        lines.append(f"New experiences ({len(new_exps)}): " + ", ".join(new_exps[:5]))
    else:
        lines.append("New experiences (0): none")

    if new_projs:
        lines.append(f"New projects ({len(new_projs)}): " + ", ".join(new_projs[:5]))
    else:
        lines.append("New projects (0): none")

    lines.append(f"\nProfile total: {total_skills} skills · {total_exps} experiences · {total_projs} projects")
    return "\n".join(lines)


def get_first_user_id() -> Optional[UUID]:
    from database.user_utils import get_active_profile
    user = get_active_profile()
    return user.user_id if user else None


def get_graph_summary(user_id: Optional[UUID]) -> dict:
    """Return structured graph data: top_skills, by_category, evidence."""
    if user_id is None:
        return {"top_skills": [], "by_category": {}, "evidence": {}}
    try:
        from knowledge_graph.builder import SkillGraphBuilder
        G = SkillGraphBuilder(user_id).build_graph()
    except Exception:
        return {"top_skills": [], "by_category": {}, "evidence": {}}

    skill_nodes = [(n, d) for n, d in G.nodes(data=True) if d.get("type") == "Skill"]

    # Top skills by in-degree (how many projects/experiences reference them)
    scored = sorted(
        [(d.get("name", n), G.in_degree(n)) for n, d in skill_nodes],
        key=lambda x: x[1],
        reverse=True,
    )
    top_skills = [{"name": name, "connections": count} for name, count in scored[:10]]

    # Count per category
    by_category: dict[str, int] = {}
    for _, d in skill_nodes:
        cat = d.get("category") or "Uncategorized"
        by_category[cat] = by_category.get(cat, 0) + 1

    # Evidence: for top 5 skills, list which projects/experiences reference them
    evidence: dict[str, list[str]] = {}
    for node, d in skill_nodes:
        name = d.get("name", node)
        if name in {s["name"] for s in top_skills[:5]}:
            sources = [
                G.nodes[p].get("name", p)
                for p in G.predecessors(node)
            ]
            if sources:
                evidence[name] = sources

    return {"top_skills": top_skills, "by_category": by_category, "evidence": evidence}


def get_profile_data() -> Optional[dict]:
    """Return the active profile's editable fields and stats, or None if no profile."""
    from database.user_utils import get_active_profile
    user = get_active_profile()
    if not user:
        return None
    with Session(engine) as session:
        skill_count = len(session.exec(
            select(UserSkill).where(UserSkill.user_id == user.user_id)
        ).all())
        exp_count = len(session.exec(
            select(Experience).where(Experience.user_id == user.user_id)
        ).all())
        proj_count = len(session.exec(
            select(Project).where(Project.user_id == user.user_id)
        ).all())
        sources: set[str] = set()
        for us in session.exec(
            select(UserSkill).where(UserSkill.user_id == user.user_id)
        ).all():
            if us.evidence_source:
                sources.add(us.evidence_source.split(":")[0])
    default_email = "user@example.com"
    return {
        "user_id": user.user_id,
        "name": user.name or "",
        "github_username": user.github_username or "",
        "linkedin_url": user.linkedin_url or "",
        "email": "" if (not user.email or user.email == default_email) else user.email,
        "phone": user.phone or "",
        "location": user.location or "",
        "skills": skill_count,
        "experiences": exp_count,
        "projects": proj_count,
        "sources": sorted(sources),
    }


def update_profile(
    user_id: UUID,
    name: str,
    github_username: str,
    linkedin_url: str,
    phone: str = "",
    email: str = "",
    location: str = "",
    portfolio_url: str = "",
) -> str:
    """Update the active profile's personal info fields."""
    from datetime import datetime
    try:
        with Session(engine) as session:
            user = session.get(User, user_id)
            if not user:
                return "Profile not found."
            user.name = name.strip() or user.name
            user.github_username = github_username.strip() or None
            user.linkedin_url = linkedin_url.strip() or None
            user.phone = phone.strip() or None
            user.location = location.strip() or None
            user.portfolio_url = portfolio_url.strip() or None
            if email.strip() and email.strip() != "user@example.com":
                user.email = email.strip()
            user.updated_at = datetime.utcnow()
            session.add(user)
            session.commit()
    except Exception as e:
        logger.error("update_profile failed: %s", e)
        return f"Failed to save profile: {e}"
    return "Profile updated."


def ingest_github_for_profile(user_id: Optional[UUID], username: str) -> str:
    """Ingest GitHub repos for the active profile."""
    return ingest_github(username)


def _format_skill_source(evidence_source: str) -> str:
    """Convert a raw evidence_source value to a human-readable display label."""
    src = (evidence_source or "").strip()
    if src.startswith("github:"):
        ref = src[len("github:"):]   # "username" or "owner/repo"
        return f"GitHub: {ref.split('/')[-1]}"
    if src.startswith("manual"):
        return "manual"
    return "resume"  # any file path, "resume" literal, or unknown → resume


def get_skills(user_id: Optional[UUID]) -> list[dict]:
    if user_id is None:
        return []
    with Session(engine) as session:
        user_skills = session.exec(
            select(UserSkill).where(UserSkill.user_id == user_id)
        ).all()

        # Deduplicate by normalized skill name; merge sources and keep highest confidence.
        merged: dict[str, dict] = {}
        for us in user_skills:
            skill = session.get(Skill, us.skill_id)
            if not skill:
                continue
            key = skill.name.lower().strip()
            source = _format_skill_source(us.evidence_source or "")
            confidence = us.confidence_score or 0.0

            if key not in merged:
                merged[key] = {
                    "name": skill.name,
                    "category": skill.category or "Uncategorized",
                    "proficiency": us.proficiency,
                    "confidence": confidence,
                    "sources": {source} if source else set(),
                    "is_core": bool(us.is_core),
                }
            else:
                if confidence > merged[key]["confidence"]:
                    merged[key]["confidence"] = confidence
                    if us.proficiency is not None:
                        merged[key]["proficiency"] = us.proficiency
                if source:
                    merged[key]["sources"].add(source)
                merged[key]["is_core"] = merged[key]["is_core"] or bool(us.is_core)

        rows = []
        for entry in merged.values():
            prof = entry["proficiency"]
            rows.append({
                "name": entry["name"],
                "category": entry["category"],
                "source": ", ".join(sorted(entry["sources"])) if entry["sources"] else "",
                "proficiency": str(prof) if prof is not None else "N/A",
                "confidence": f"{entry['confidence']:.1f}",
                "is_core": entry["is_core"],
            })
        return rows


def set_skill_core(user_id: UUID, skill_name: str, is_core: bool) -> str:
    """Pin/unpin a skill as 'core' on the user's profile. Returns plain-English. Never raises.

    A pinned skill is always rendered in the tailored skills section, bypassing
    the JD-relevance cap (issue #54). Applies to every UserSkill row for the
    skill (matched case-insensitively by name).
    """
    try:
        skill_name = (skill_name or "").strip()
        if not skill_name:
            return "Please provide a skill name."
        with Session(engine) as session:
            skill = session.exec(select(Skill).where(Skill.name == skill_name)).first()
            if not skill:
                all_skills = session.exec(select(Skill)).all()
                skill = next(
                    (s for s in all_skills if s.name.lower() == skill_name.lower()), None
                )
            if not skill:
                return f"'{skill_name}' is not in your profile."
            display_name = skill.name
            links = session.exec(
                select(UserSkill).where(
                    UserSkill.user_id == user_id,
                    UserSkill.skill_id == skill.skill_id,
                )
            ).all()
            if not links:
                return f"'{skill_name}' is not in your profile."
            for link in links:
                link.is_core = is_core
                session.add(link)
            session.commit()
        if is_core:
            return f"Pinned '{display_name}' as a core skill."
        return f"Unpinned '{display_name}'."
    except Exception as e:
        logger.error("set_skill_core failed: %s", e)
        return f"Failed to update skill: {e}"


# Sentinel title/company values an extractor emits when a real value is absent.
_EXP_PLACEHOLDERS = {"", "unknown", "unknown position", "n/a", "na", "none", "?"}


def _is_placeholder(value) -> bool:
    return str(value or "").strip().lower() in _EXP_PLACEHOLDERS


def _exp_missing(e: Experience) -> list[str]:
    """Which parts of an experience are missing, for the Data Explorer's
    'incomplete' badge (issue #85). Lets the user see a malformed row and
    complete it (edit) or drop it (delete) rather than it silently shipping."""
    missing: list[str] = []
    if _is_placeholder(e.title):
        missing.append("title")
    if _is_placeholder(e.company):
        missing.append("company")
    if not (e.start_date or e.end_date):
        missing.append("dates")
    if not (e.bullets or (e.description or "").strip()):
        missing.append("details")
    return missing


def get_experiences(user_id: Optional[UUID]) -> list[dict]:
    if user_id is None:
        return []
    with Session(engine) as session:
        exps = session.exec(
            select(Experience).where(Experience.user_id == user_id)
            .order_by(Experience.created_at)
        ).all()
        return [_exp_row_dict(e) for e in exps]


def get_education(user_id: Optional[UUID]) -> list[dict]:
    """This user's education rows for the Data Explorer (issue #73 follow-up)."""
    if user_id is None:
        return []
    with Session(engine) as session:
        entries = session.exec(
            select(Education)
            .where(Education.user_id == user_id)
            .order_by(Education.created_at)
        ).all()
        return [
            {
                "id": str(e.education_id),
                "institution": e.institution,
                "degree": e.degree or "—",
                "location": e.location or "",
                "start": e.start_date or "",
                "end": e.end_date or "",
                "gpa": e.gpa or "",
            }
            for e in entries
        ]


def get_achievements(user_id: Optional[UUID]) -> list[dict]:
    """This user's achievements for the Data Explorer, in resume-document order."""
    if user_id is None:
        return []
    with Session(engine) as session:
        entries = session.exec(
            select(Achievement)
            .where(Achievement.user_id == user_id)
            .order_by(Achievement.created_at)
        ).all()
        return [
            {
                "title": a.title,
                "description": a.description or "",
                "issuer": a.issuer or "",
                "date": a.date or "",
            }
            for a in entries
        ]


def get_projects(user_id: Optional[UUID]) -> list[dict]:
    if user_id is None:
        return []
    with Session(engine) as session:
        projs = session.exec(
            select(Project).where(Project.user_id == user_id)
            .order_by(Project.created_at)
        ).all()
        return [
            {
                "id": str(p.project_id),
                "name": p.name,
                "url": p.repo_url or "—",
                "desc": (p.description or "")[:60],
                "description": p.description or "",
                "repo_url": p.repo_url or "",
                "demo_url": p.demo_url or "",
                "start": p.start_date or "",
                "end": p.end_date or "",
            }
            for p in projs
        ]


# ── Manual edit & delete of knowledge-graph rows (issue #92) ────────────────────
# The durable fallback for corrections the automatic dedup/self-heal can't make.
# Every operation is caller-scoped: a row is touched only when it belongs to the
# passed user_id, never a client-supplied id, mirroring the isolation from #73.


def _exp_row_dict(e: Experience) -> dict:
    missing = _exp_missing(e)
    return {
        "id": str(e.experience_id), "title": e.title, "company": e.company,
        "start": e.start_date or "?", "end": e.end_date or "?",
        "description": e.description or "", "bullets": e.bullets or [],
        "incomplete": bool(missing), "missing": missing,
    }


def _edu_row_dict(e: Education) -> dict:
    return {
        "id": str(e.education_id), "institution": e.institution,
        "degree": e.degree or "—", "location": e.location or "",
        "start": e.start_date or "", "end": e.end_date or "", "gpa": e.gpa or "",
    }


def _proj_row_dict(p: Project) -> dict:
    return {
        "id": str(p.project_id), "name": p.name, "url": p.repo_url or "—",
        "desc": (p.description or "")[:60], "description": p.description or "",
        "repo_url": p.repo_url or "", "demo_url": p.demo_url or "",
        "start": p.start_date or "", "end": p.end_date or "",
    }


def _clean_str(val) -> Optional[str]:
    """Normalize an edited scalar: strip; empty string → None."""
    s = str(val or "").strip()
    return s or None


def _as_uuid(value) -> Optional[UUID]:
    """Coerce a path/string id to UUID; None on a malformed id (→ 404)."""
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


def update_experience(user_id: Optional[UUID], experience_id: str, fields: dict) -> Optional[dict]:
    """Edit a user's own Experience row. Returns the updated row dict, or None if
    not found / not owned. Raises ValueError on an empty required field. Sets
    manually_edited so a later re-ingest can't revert the change (issue #92)."""
    if user_id is None:
        return None
    with Session(engine) as session:
        eid = _as_uuid(experience_id)
        row = session.get(Experience, eid) if eid else None
        if not row or row.user_id != user_id:
            return None
        if "title" in fields:
            title = str(fields["title"] or "").strip()
            if not title:
                raise ValueError("Title cannot be empty.")
            row.title = title
        if "company" in fields:
            company = str(fields["company"] or "").strip()
            if not company:
                raise ValueError("Company cannot be empty.")
            row.company = company
        for key in ("start_date", "end_date", "description"):
            if key in fields:
                setattr(row, key, _clean_str(fields[key]))
        if "bullets" in fields:
            row.bullets = [str(b).strip() for b in (fields["bullets"] or []) if str(b or "").strip()]
        row.manually_edited = True
        from datetime import datetime
        row.updated_at = datetime.utcnow()
        session.add(row)
        session.commit()
        session.refresh(row)
        return _exp_row_dict(row)


def update_education(user_id: Optional[UUID], education_id: str, fields: dict) -> Optional[dict]:
    """Edit a user's own Education row (issue #92)."""
    if user_id is None:
        return None
    with Session(engine) as session:
        eid = _as_uuid(education_id)
        row = session.get(Education, eid) if eid else None
        if not row or row.user_id != user_id:
            return None
        if "institution" in fields:
            inst = str(fields["institution"] or "").strip()
            if not inst:
                raise ValueError("Institution cannot be empty.")
            row.institution = inst
        if "degree" in fields:
            row.degree = str(fields["degree"] or "").strip()
        for key in ("location", "start_date", "end_date", "gpa"):
            if key in fields:
                setattr(row, key, _clean_str(fields[key]))
        row.manually_edited = True
        from datetime import datetime
        row.updated_at = datetime.utcnow()
        session.add(row)
        session.commit()
        session.refresh(row)
        return _edu_row_dict(row)


def update_project(user_id: Optional[UUID], project_id: str, fields: dict) -> Optional[dict]:
    """Edit a user's own Project row (issue #92)."""
    if user_id is None:
        return None
    with Session(engine) as session:
        pid = _as_uuid(project_id)
        row = session.get(Project, pid) if pid else None
        if not row or row.user_id != user_id:
            return None
        if "name" in fields:
            name = str(fields["name"] or "").strip()
            if not name:
                raise ValueError("Name cannot be empty.")
            row.name = name
        for key in ("description", "repo_url", "demo_url", "start_date", "end_date"):
            if key in fields:
                setattr(row, key, _clean_str(fields[key]))
        row.manually_edited = True
        from datetime import datetime
        row.updated_at = datetime.utcnow()
        session.add(row)
        session.commit()
        session.refresh(row)
        return _proj_row_dict(row)


def _record_tombstone(session, user_id: UUID, entity_type: str, key_a: str, key_b) -> None:
    """Record a deletion tombstone so a re-ingest won't resurrect the row."""
    session.add(DeletedEntry(
        user_id=user_id, entity_type=entity_type,
        key_a=str(key_a or ""), key_b=(str(key_b).strip() or None) if key_b else None,
    ))


def delete_experience(user_id: Optional[UUID], experience_id: str) -> bool:
    """Delete a user's own Experience row and tombstone it (issue #92).
    Returns True on success, False if not found / not owned."""
    if user_id is None:
        return False
    with Session(engine) as session:
        eid = _as_uuid(experience_id)
        row = session.get(Experience, eid) if eid else None
        if not row or row.user_id != user_id:
            return False
        _record_tombstone(session, user_id, "experience", row.title, row.company)
        session.delete(row)
        session.commit()
        return True


def delete_education(user_id: Optional[UUID], education_id: str) -> bool:
    """Delete a user's own Education row and tombstone it (issue #92)."""
    if user_id is None:
        return False
    with Session(engine) as session:
        eid = _as_uuid(education_id)
        row = session.get(Education, eid) if eid else None
        if not row or row.user_id != user_id:
            return False
        _record_tombstone(session, user_id, "education", row.institution, row.degree)
        session.delete(row)
        session.commit()
        return True


def delete_project(user_id: Optional[UUID], project_id: str) -> bool:
    """Delete a user's own Project row and tombstone it (issue #92)."""
    if user_id is None:
        return False
    with Session(engine) as session:
        pid = _as_uuid(project_id)
        row = session.get(Project, pid) if pid else None
        if not row or row.user_id != user_id:
            return False
        _record_tombstone(session, user_id, "project", row.name, row.repo_url)
        session.delete(row)
        session.commit()
        return True


def get_jobs() -> list[dict]:
    with Session(engine) as session:
        jobs = session.exec(select(JobDescription)).all()
        result = []
        for job in jobs:
            results = session.exec(
                select(UserJobResult).where(UserJobResult.job_id == job.job_id)
            ).all()
            score = ""
            if results:
                best = max(r.ats_score for r in results)
                score = f" [{best:.0f}%]"
            result.append({
                "job_id": str(job.job_id),
                "title": job.title,
                "company": job.company,
                "score": score,
                "status": getattr(job, "status", "created"),
            })
        return result


def get_job_details(job_uuid: str) -> Optional[dict]:
    try:
        parsed_uuid = UUID(job_uuid)
    except (ValueError, AttributeError):
        return None
    with Session(engine) as session:
        job = session.get(JobDescription, parsed_uuid)
        if not job:
            return None
        results = session.exec(
            select(UserJobResult).where(UserJobResult.job_id == job.job_id)
        ).all()
        detail: dict = {
            "title": job.title,
            "company": job.company,
            "status": getattr(job, "status", "created"),
            "description": job.description or "",
        }
        if results:
            latest = max(results, key=lambda r: r.created_at)
            detail["ats_score"] = latest.ats_score
            detail["matched_skills"] = skill_names(latest.matched_skills)[:10]
            detail["missing_skills"] = latest.missing_skills[:10] if latest.missing_skills else []
        return detail


def compute_app_state() -> str:
    """Return 'setup' or 'profile_ready' based on current DB state."""
    with Session(engine) as session:
        user = session.exec(select(User).limit(1)).first()
        if not user:
            return "setup"
        skill = session.exec(
            select(UserSkill).where(UserSkill.user_id == user.user_id).limit(1)
        ).first()
        return "profile_ready" if skill else "setup"


# ── GitHub token (stored in .env, never in SQLite) ──────────

def get_github_token() -> str:
    """Read GITHUB_TOKEN from .env. Returns '' if not set."""
    from dotenv import dotenv_values
    return dotenv_values(_ENV_PATH).get("GITHUB_TOKEN", "") or ""


def save_github_token(token: str) -> None:
    """Write GITHUB_TOKEN to .env via dotenv. If token is '', remove the key. Never log the value."""
    from dotenv import set_key, unset_key
    _ENV_PATH.touch()
    if token:
        set_key(str(_ENV_PATH), "GITHUB_TOKEN", token)  # token value intentionally not logged
    else:
        unset_key(str(_ENV_PATH), "GITHUB_TOKEN")


# ── GitHub OAuth device flow ─────────────────────────────────

def start_github_device_flow() -> dict:
    """Initiate GitHub device flow. Returns { user_code, verification_uri, device_code, interval } or raises."""
    import requests as _req
    from config import GITHUB_CLIENT_ID
    if not GITHUB_CLIENT_ID:
        raise RuntimeError("GITHUB_CLIENT_ID is not set — cannot start device flow")
    resp = _req.post(
        "https://github.com/login/device/code",
        headers={"Accept": "application/json"},
        json={"client_id": GITHUB_CLIENT_ID, "scope": "repo read:user"},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    return {
        "device_code": data["device_code"],
        "user_code": data["user_code"],
        "verification_uri": data["verification_uri"],
        "interval": data.get("interval", 5),
        "expires_in": data.get("expires_in", 900),
    }


def poll_github_device_flow(device_code: str, interval: int = 5) -> str | None:
    """Poll once for a device-flow access token. Returns token string, None if still pending, or raises on error."""
    import time
    import requests as _req
    from config import GITHUB_CLIENT_ID
    time.sleep(interval)
    resp = _req.post(
        "https://github.com/login/oauth/access_token",
        headers={"Accept": "application/json"},
        json={
            "client_id": GITHUB_CLIENT_ID,
            "device_code": device_code,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        },
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("access_token"):
        return data["access_token"]
    error = data.get("error", "")
    if error in ("authorization_pending", "slow_down"):
        return None
    raise RuntimeError(f"Device flow error: {error} — {data.get('error_description', '')}")


# ── LLM provider + API key (stored in .env, never in SQLite) ─

def get_llm_config() -> tuple[str, bool]:
    """Return (provider, has_key) read from .env and os.environ.

    has_key is True if the API key for the current provider is set.
    """
    import os
    from dotenv import dotenv_values
    vals = dotenv_values(_ENV_PATH)
    from config import normalize_provider
    provider = normalize_provider(vals.get("LLM_PROVIDER") or os.environ.get("LLM_PROVIDER"))
    if provider == "anthropic":
        has_key = bool(vals.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_API_KEY"))
    else:
        has_key = bool(vals.get("OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY"))
    return provider, has_key


def save_llm_config(provider: str, api_key: str) -> None:
    """Persist LLM provider + API key to .env and os.environ for immediate effect.

    Setting os.environ means get_llm() picks up the new key on the very next call
    without requiring a restart. Never logs the key value.
    """
    import os
    from dotenv import set_key
    from config import normalize_provider
    provider = normalize_provider(provider)
    _ENV_PATH.touch()
    set_key(str(_ENV_PATH), "LLM_PROVIDER", provider)
    key_name = "ANTHROPIC_API_KEY" if provider == "anthropic" else "OPENAI_API_KEY"
    set_key(str(_ENV_PATH), key_name, api_key)  # key value intentionally not logged
    os.environ["LLM_PROVIDER"] = provider
    os.environ[key_name] = api_key


def save_llm_provider_only(provider: str) -> None:
    """Persist only the LLM_PROVIDER to .env and os.environ, leaving existing keys untouched."""
    import os
    from dotenv import set_key
    from config import normalize_provider
    provider = normalize_provider(provider)
    _ENV_PATH.touch()
    set_key(str(_ENV_PATH), "LLM_PROVIDER", provider)
    os.environ["LLM_PROVIDER"] = provider


# ── Resume path (stored on User row) ────────────────────────

def get_resume_path(user_id: UUID) -> Optional[str]:
    """Return resume_path for the given user, or None."""
    with Session(engine) as session:
        user = session.get(User, user_id)
        return user.resume_path if user else None


def get_resume_style(user_id: UUID) -> Optional[dict]:
    """Return the parsed style profile for the user's ingested resume, or None."""
    with Session(engine) as session:
        user = session.get(User, user_id)
        return user.resume_style if user else None


def update_resume_path(user_id: UUID, path: str) -> None:
    """Set resume_path on the User row."""
    with Session(engine) as session:
        user = session.get(User, user_id)
        if user:
            user.resume_path = path
            session.add(user)
            session.commit()


def add_skill_to_profile(user_id: UUID, skill_name: str, target: Optional[str] = None) -> str:
    """Add a skill to the user's profile. Returns plain-English result. Never raises."""
    try:
        skill_name = skill_name.strip()
        if not skill_name:
            return "Please provide a skill name."
        with Session(engine) as session:
            skill = session.exec(select(Skill).where(Skill.name == skill_name)).first()
            if not skill:
                all_skills = session.exec(select(Skill)).all()
                skill = next((s for s in all_skills if s.name.lower() == skill_name.lower()), None)
            if not skill:
                skill = Skill(name=skill_name)
                session.add(skill)
                session.flush()
            existing = session.exec(
                select(UserSkill).where(
                    UserSkill.user_id == user_id,
                    UserSkill.skill_id == skill.skill_id,
                )
            ).first()
            if existing:
                return f"'{skill_name}' is already in your profile."
            evidence = f"manual:{target}" if target else "manual"
            user_skill = UserSkill(
                user_id=user_id,
                skill_id=skill.skill_id,
                proficiency=3,
                evidence_source="manual",
                confidence_score=0.7,
                evidence_detail=evidence,
            )
            session.add(user_skill)
            session.commit()
            # Refresh the cached embedding for the new skill (issue #54).
            try:
                from agents.skill_embeddings import ensure_skill_embeddings
                ensure_skill_embeddings(session, [skill.skill_id])
            except Exception as exc:
                logger.warning("Skill embedding refresh skipped: %s", exc)
        return f"Added '{skill_name}' to your profile."
    except Exception as e:
        logger.error("add_skill_to_profile failed: %s", e)
        return f"Failed to add skill: {e}"


def create_artifact_from_chat(
    user_id: UUID,
    artifact_type: str,
    data: dict,
    source_context: str = "chat",
) -> str:
    """Create a skill, project, or experience row from structured chat-extracted data.

    artifact_type: 'skill' | 'project' | 'experience'
    data: type-specific fields (name/category for skill; name/description/repo_url for project;
          title/company/description for experience).  Must include a non-empty 'evidence' key:
          a verbatim quote or paraphrase from the conversation that supports saving this artifact.
    source_context: free-form back-reference (e.g. 'chat:job_<id>') stored on the artifact.
          Soft metadata only — a plain text column, never a foreign key, so deleting the job
          or its chat history can never cascade into these rows (issue #21).
    Returns a plain-English result string. Never raises.
    """
    try:
        # Evidence is required — callers must supply a quote/paraphrase from the conversation
        # that supports this artifact.  This prevents the LLM from hallucinating artifacts that
        # were never actually discussed.
        evidence = (data.get("evidence") or "").strip()
        if not evidence:
            return (
                "Evidence is required to save this artifact from chat. "
                "Describe what was said in the conversation that supports this."
            )

        artifact_type = artifact_type.lower().strip()
        if artifact_type == "skill":
            name = (data.get("name") or "").strip()
            if not name:
                return "Skill name is required."
            category = (data.get("category") or "").strip() or None
            with Session(engine) as session:
                skill = session.exec(select(Skill).where(Skill.name == name)).first()
                if not skill:
                    all_skills = session.exec(select(Skill)).all()
                    skill = next((s for s in all_skills if s.name.lower() == name.lower()), None)
                if not skill:
                    skill = Skill(name=name, category=category)
                    session.add(skill)
                    session.flush()
                existing = session.exec(
                    select(UserSkill).where(
                        UserSkill.user_id == user_id,
                        UserSkill.skill_id == skill.skill_id,
                    )
                ).first()
                if existing:
                    return f"'{name}' is already in your profile."
                proficiency = data.get("proficiency")
                session.add(UserSkill(
                    user_id=user_id,
                    skill_id=skill.skill_id,
                    proficiency=proficiency if isinstance(proficiency, int) else 3,
                    evidence_source="chat",
                    confidence_score=0.7,
                    evidence_detail=evidence,  # verbatim quote from the conversation
                    source_context=source_context,
                ))
                session.commit()
            return f"Added skill '{name}' to your profile (source: chat)."
        elif artifact_type == "project":
            name = (data.get("name") or "").strip()
            if not name:
                return "Project name is required."
            # Fall back to the evidence quote when no separate description is provided.
            description = (data.get("description") or evidence).strip() or None
            repo_url = (data.get("repo_url") or "").strip() or None
            with Session(engine) as session:
                existing = session.exec(
                    select(Project).where(
                        Project.user_id == user_id,
                        Project.name == name,
                    )
                ).first()
                if existing:
                    return f"Project '{name}' is already in your profile."
                session.add(Project(
                    user_id=user_id,
                    name=name,
                    description=description,
                    repo_url=repo_url,
                    source_context=source_context,
                ))
                session.commit()
            return f"Added project '{name}' to your profile."
        elif artifact_type == "experience":
            title = (data.get("title") or "").strip()
            company = (data.get("company") or "").strip()
            if not title or not company:
                return "Experience title and company are required."
            # Fall back to the evidence quote when no separate description is provided.
            description = (data.get("description") or evidence).strip() or None
            with Session(engine) as session:
                existing = session.exec(
                    select(Experience).where(
                        Experience.user_id == user_id,
                        Experience.title == title,
                        Experience.company == company,
                    )
                ).first()
                if existing:
                    return f"Experience '{title} @ {company}' is already in your profile."
                session.add(Experience(
                    user_id=user_id,
                    title=title,
                    company=company,
                    description=description,
                    source_context=source_context,
                ))
                session.commit()
            return f"Added experience '{title} @ {company}' to your profile."
        else:
            return f"Unknown artifact type: '{artifact_type}'. Use 'skill', 'project', or 'experience'."
    except Exception as e:
        logger.error("create_artifact_from_chat failed: %s", e)
        return f"Failed to create artifact: {e}"


# ── Chain-of-Note decision application (issue #21) ────────────────────────────
#
# The extractor in agents/knowledge_extractor.py proposes one of three actions
# per artifact; this is where a *confirmed* proposal becomes rows. Nothing here
# runs without an explicit user accept — there is no auto-write path.


def _supersede_skill(session, user_id: UUID, data: dict, evidence: str,
                     source_context: Optional[str]) -> Optional[str]:
    """Update an existing UserSkill in place. None when there's no row to update."""
    from datetime import datetime

    name = (data.get("name") or "").strip()
    all_skills = session.exec(select(Skill)).all()
    skill = next((s for s in all_skills if s.name.lower() == name.lower()), None)
    if not skill:
        return None
    link = session.exec(
        select(UserSkill).where(
            UserSkill.user_id == user_id,
            UserSkill.skill_id == skill.skill_id,
        )
    ).first()
    if not link:
        return None

    changes = []
    category = (data.get("category") or "").strip()
    if category and category != (skill.category or ""):
        changes.append(f"category → {category}")
        skill.category = category
        session.add(skill)
    proficiency = data.get("proficiency")
    if isinstance(proficiency, int) and proficiency != link.proficiency:
        changes.append(f"proficiency → {proficiency}")
        link.proficiency = proficiency
    link.evidence_detail = evidence
    link.evidence_source = "chat"
    link.source_context = source_context
    link.updated_at = datetime.utcnow()
    session.add(link)
    session.commit()
    detail = f" ({', '.join(changes)})" if changes else ""
    return f"Updated skill '{skill.name}' from our conversation{detail}."


def _target_name(target: Optional[str], kind: str) -> Optional[str]:
    """The artifact name inside a decision target label, e.g. 'project: Foo' → 'Foo'.

    The reason step points at the fact it means to replace by quoting the label
    `knowledge_extractor.load_known_facts` built. Parsing it back lets a rename
    or a promotion find the right row — the *new* name can't, by definition.
    """
    if not target:
        return None
    prefix = f"{kind}:"
    label = target.strip()
    if label.lower().startswith(prefix):
        label = label[len(prefix):].strip()
    if kind == "experience" and " @ " in label:
        label = label.split(" @ ", 1)[0].strip()
    return label or None


def _supersede_project(session, user_id: UUID, data: dict, evidence: str,
                       source_context: Optional[str]) -> Optional[str]:
    """Update an existing Project in place. None when there's no row to update."""
    from datetime import datetime

    from agents.parser import ResumeParserAgent

    name = (data.get("name") or "").strip()
    repo_url = (data.get("repo_url") or "").strip() or None
    rows = session.exec(select(Project).where(Project.user_id == user_id)).all()
    # Reuse the ingestion deduper's matcher rather than a second notion of
    # "same project" (shared repo URL, spacing/containment on the name).
    row = next(
        (p for p in rows
         if ResumeParserAgent._projects_match(name, repo_url, p.name, p.repo_url)),
        None,
    )
    if row is None:
        # A rename changes the name the matcher keys on; the decision's target
        # still names the row we mean.
        old_name = _target_name(data.get("target"), "project")
        row = next(
            (p for p in rows
             if old_name and ResumeParserAgent._names_match(old_name, p.name)),
            None,
        )
    if not row:
        return None

    changes = []
    if name and name.lower() != (row.name or "").lower():
        changes.append(f"name → {name}")
        row.name = name
    # Only fall back to the evidence quote when there is no description to lose:
    # on an update, overwriting a real description with a chat quote is a downgrade.
    description = (data.get("description") or "").strip() or (
        evidence if not row.description else None)
    if description and description != row.description:
        changes.append("description")
        row.description = description
    if repo_url and repo_url != row.repo_url:
        changes.append("repo link")
        row.repo_url = repo_url
    for field in ("start_date", "end_date"):
        value = (data.get(field) or "").strip()
        if value and value != getattr(row, field):
            changes.append(field.replace("_", " "))
            setattr(row, field, value)
    row.source_context = source_context
    row.updated_at = datetime.utcnow()
    session.add(row)
    session.commit()
    detail = f" ({', '.join(changes)})" if changes else ""
    return f"Updated project '{row.name}' from our conversation{detail}."


def _supersede_experience(session, user_id: UUID, data: dict, evidence: str,
                          source_context: Optional[str]) -> Optional[str]:
    """Update an existing Experience in place. None when there's no row to update.

    The headline case is a role change at the same employer ("I got promoted to
    Staff Engineer"): the title on the existing row moves rather than a second
    row appearing for the same job.
    """
    from datetime import datetime

    from agents.parser import ResumeParserAgent

    title = (data.get("title") or data.get("name") or "").strip()
    company = (data.get("company") or "").strip()
    rows = session.exec(select(Experience).where(Experience.user_id == user_id)).all()
    row = next(
        (e for e in rows
         if ResumeParserAgent._experiences_match(title, company, e.title, e.company)),
        None,
    )
    if row is None:
        # A promotion changes the title, so title matching fails by design.
        # The decision's target still names the role being replaced; fall back
        # to the employer only when it doesn't.
        old_title = _target_name(data.get("target"), "experience")
        row = next(
            (e for e in rows
             if ResumeParserAgent._experiences_match(
                 old_title, company, e.title, e.company)),
            None,
        ) if old_title else None
    if row is None and company:
        # Last resort: the employer alone. Only when it is unambiguous — with two
        # roles at the same company there is no way to tell which one the user
        # meant, and overwriting the wrong one is worse than adding a new row.
        at_company = [e for e in rows
                      if ResumeParserAgent._institutions_match(company, e.company)]
        row = at_company[0] if len(at_company) == 1 else None
    if not row:
        return None

    changes = []
    if title and title.lower() != (row.title or "").lower():
        changes.append(f"title → {title}")
        row.title = title
    description = (data.get("description") or "").strip()
    if description and description != row.description:
        changes.append("description")
        row.description = description
    for field in ("start_date", "end_date"):
        value = (data.get(field) or "").strip()
        if value and value != getattr(row, field):
            changes.append(field.replace("_", " "))
            setattr(row, field, value)
    row.source_context = source_context
    row.updated_at = datetime.utcnow()
    session.add(row)
    session.commit()
    detail = f" ({', '.join(changes)})" if changes else ""
    return f"Updated experience '{row.title} @ {row.company}' from our conversation{detail}."


_SUPERSEDERS = {
    "skill": _supersede_skill,
    "project": _supersede_project,
    "experience": _supersede_experience,
}


def apply_artifact_decision(
    user_id: UUID,
    proposal: dict,
    source_context: str = "chat",
) -> str:
    """Persist one *confirmed* Chain-of-Note proposal. Returns plain English.

    `proposal` is one entry from `agents.knowledge_extractor.run_chain_of_note`:
    the note's fields plus a `decision` of 'add' | 'supersede' | 'no_op'.

    - add       → create the row (delegates to create_artifact_from_chat)
    - supersede → update the matching row in place, so a later turn that
                  contradicts an earlier fact refreshes the graph instead of
                  duplicating it or being silently dropped as "already known"
    - no_op     → nothing to do; say so

    Never raises. Called only after an explicit user accept — this function is
    not wired to any automatic path.
    """
    try:
        decision = (proposal.get("decision") or "add").strip().lower()
        artifact_type = (proposal.get("type") or "").strip().lower()
        # The note schema carries the job title in `name` (one field across all
        # three artifact types); the Experience row and its creator want `title`.
        if artifact_type == "experience" and not proposal.get("title"):
            proposal = {**proposal, "title": proposal.get("name")}

        if decision == "no_op":
            label = proposal.get("name") or artifact_type or "That"
            return f"'{label}' is already in your profile — nothing to change."

        if decision != "supersede":
            return create_artifact_from_chat(
                user_id, artifact_type, proposal, source_context=source_context)

        evidence = (proposal.get("evidence") or "").strip()
        if not evidence:
            return (
                "Evidence is required to update this artifact from chat. "
                "Describe what was said in the conversation that supports this."
            )
        superseder = _SUPERSEDERS.get(artifact_type)
        if not superseder:
            return (
                f"Unknown artifact type: '{artifact_type}'. "
                "Use 'skill', 'project', or 'experience'."
            )
        with Session(engine) as session:
            result = superseder(session, user_id, proposal, evidence, source_context)
        if result:
            return result
        # Nothing matched — the graph doesn't hold what we meant to update, so
        # the honest action is to create it rather than report a phantom update.
        return create_artifact_from_chat(
            user_id, artifact_type, proposal, source_context=source_context)
    except Exception as e:
        logger.error("apply_artifact_decision failed: %s", e)
        return f"Failed to save artifact: {e}"


# ── JobCard: distilled completed-job memory (issue #137) ──────────────────────

# Jobs whose tailoring is finished enough to be worth remembering. A job still
# at 'created'/'analyzed' has no outcome to distil.
_JOBCARD_TERMINAL_STATUSES = ("tailored", "exported")


def _latest_job_result(session, user_id: UUID, job_id: UUID):
    """This user's most recent result for a job (a job can accumulate several)."""
    results = session.exec(
        select(UserJobResult)
        .where(UserJobResult.user_id == user_id)
        .where(UserJobResult.job_id == job_id)
    ).all()
    return max(results, key=lambda r: r.created_at) if results else None


def resolve_role_family(
    user_id: UUID, job, *, allow_classify: bool = True
) -> Optional[str]:
    """The active JD's role family — the query side of the selection index.

    Checks the card cache first, keyed by the classify inputs rather than by
    job, so a JD the user has already had classified (a re-tailor, or simply a
    similar posting) costs nothing. Only a genuinely novel JD reaches the model,
    and callers thread the answer into `rebuild_job_card` so a single tailoring
    run never classifies twice.

    *allow_classify* False makes this cache-only. `plan_preview` uses that: a
    preview is a cheap look-ahead and must not spend a model call on ranking.

    Returns None when unresolved, which simply zeroes the role-match term and
    leaves the other three selection signals to rank.
    """
    from agents.job_card import (
        ROLE_FAMILY_VERSION, classify_role_family, role_family_key,
    )
    from database.models import JobCard

    title = getattr(job, "title", "") or ""
    company = getattr(job, "company", "") or ""
    description = getattr(job, "description", "") or ""
    try:
        key = role_family_key(title, company, description)
        with Session(engine) as session:
            cached = session.exec(
                select(JobCard)
                .where(JobCard.user_id == user_id)
                .where(JobCard.role_family_key == key)
                .where(JobCard.role_family_version == ROLE_FAMILY_VERSION)
            ).first()
        if cached and cached.role_family:
            return cached.role_family
        if not allow_classify:
            return None
        return classify_role_family(title, company, description)
    except Exception as exc:
        logger.warning("resolve_role_family failed for job %s: %s",
                       getattr(job, "job_id", None), exc)
        return None


def rebuild_job_card(
    user_id: UUID, job_id: UUID, role_family: Optional[str] = None
) -> Optional[UUID]:
    """Recompile this job's JobCard from its current result. Never raises.

    Event-driven, not lazy — called when the job's result changes, so the
    compile is paid off the next turn's critical path. That is not a style
    preference: per the #109 amortization amendment, k-step summarization only
    pays when its prefill cost sits outside the inline latency budget, and
    compiling lazily at next-tailoring time would put that cost back inline and
    break the condition that justifies distilling at all.

    *role_family* lets a caller hand back a classification it already resolved
    this run (see `resolve_role_family`), so the tailoring path never pays for
    the same label twice.

    Returns the card id, or None when there is nothing worth carrying forward
    (no result, no tailored content) or the compile failed. Failure is always
    silent-with-a-log: a card is an optimization, and losing one must never take
    down the tailoring run that triggered it.
    """
    from datetime import datetime

    from agents.job_card import (
        ROLE_FAMILY_VERSION, build_index_keys, classify_role_family,
        compile_card_payload, payload_digest, role_family_key,
    )
    from database.models import JobCard

    try:
        with Session(engine) as session:
            job = session.get(JobDescription, job_id)
            if not job:
                return None
            result = _latest_job_result(session, user_id, job_id)
            if not result:
                return None
            content = result.tailored_resume_content
            if isinstance(content, str):
                try:
                    content = json.loads(content)
                except (ValueError, TypeError):
                    content = {}
            if not content or not isinstance(content, dict) or "error" in content:
                # Nothing was successfully tailored — there is no outcome to
                # distil, and an empty card would only dilute selection.
                return None

            card = session.exec(
                select(JobCard)
                .where(JobCard.user_id == user_id)
                .where(JobCard.job_id == job_id)
            ).first()

            # The one LLM call, and only when the cache is cold or stale. A
            # rebuild triggered by a re-tailor of the same JD reuses the label.
            key = role_family_key(job.title or "", job.company or "",
                                  job.description or "")
            if role_family:
                family = role_family
            elif (card and card.role_family
                    and card.role_family_key == key
                    and card.role_family_version == ROLE_FAMILY_VERSION):
                family = card.role_family
            else:
                family = classify_role_family(
                    job.title or "", job.company or "", job.description or "")

            payload = compile_card_payload(job, result, role_family=family)
            digest = payload_digest(payload)
            now = datetime.utcnow()

            if card is None:
                card = JobCard(user_id=user_id, job_id=job_id)
            elif card.payload_hash == digest and card.role_family == family:
                # Identical projection — the determinism guarantee means there
                # is genuinely nothing to write.
                return card.card_id

            card.result_id = result.result_id
            card.title = job.title or ""
            card.company = job.company or ""
            card.role_family = family
            card.role_family_key = key
            card.role_family_version = ROLE_FAMILY_VERSION
            card.payload = payload
            card.payload_hash = digest
            card.index_keys = build_index_keys(payload)
            card.source_updated_at = result.updated_at or now
            card.updated_at = now
            session.add(card)
            session.commit()
            session.refresh(card)
            return card.card_id
    except Exception as exc:
        logger.warning("JobCard rebuild failed for job %s: %s", job_id, exc)
        return None


def load_job_cards(user_id: UUID, exclude_job_id: Optional[UUID] = None) -> list[dict]:
    """This user's cards for *completed* jobs, ready for `job_card.select_cards`.

    Each dict carries the card plus its source job's cached JD centroid
    (`JobDescription.embedding`, the portable JSON column) as `embedding`, so
    ranking can score a card against the active JD without any new embedding
    infrastructure — populating the Postgres `embedding_vec` accelerator is a
    separate write-path concern.

    *exclude_job_id* drops the job currently being tailored: its own card is
    memory of the run in progress, not a prior job. Returns [] on any failure,
    which makes injection a no-op and reproduces today's behavior exactly.
    """
    from agents.skill_embeddings import deserialize
    from database.models import JobCard

    try:
        with Session(engine) as session:
            cards = session.exec(
                select(JobCard).where(JobCard.user_id == user_id)
            ).all()
            out: list[dict] = []
            for card in cards:
                if exclude_job_id and card.job_id == exclude_job_id:
                    continue
                job = session.get(JobDescription, card.job_id)
                if not job or job.status not in _JOBCARD_TERMINAL_STATUSES:
                    continue
                payload = card.payload
                if isinstance(payload, str):
                    try:
                        payload = json.loads(payload)
                    except (ValueError, TypeError):
                        continue
                index_keys = card.index_keys
                if isinstance(index_keys, str):
                    try:
                        index_keys = json.loads(index_keys)
                    except (ValueError, TypeError):
                        index_keys = []
                out.append({
                    "card_id": card.card_id,
                    "job_id": card.job_id,
                    "payload": payload or {},
                    "index_keys": index_keys or [],
                    "role_family": card.role_family,
                    "embedding": deserialize(job.embedding),
                    "source_updated_at": card.source_updated_at,
                })
            return out
    except Exception as exc:
        logger.warning("load_job_cards failed for user %s: %s", user_id, exc)
        return []


# ── JD profile (issue #121) ─────────────────────────────────────────────────

def rebuild_jd_profile(
    job_id: UUID, user_id: Optional[UUID] = None, force: bool = False,
) -> Optional[UUID]:
    """Extract and persist this job's JDProfile. Never raises.

    **The cache check is the feature.** When the stored `extraction_key` already
    matches the current JD text this returns immediately without reaching a
    model, which is what makes "a second tailoring run performs no
    re-extraction" true by construction rather than by hoping an LLM is
    deterministic. Everything downstream — a stationary reward for #51 Phase 2,
    #113 scoring ~6 prefixes off one extraction — rests on that short-circuit.

    *force* re-extracts even on a key hit. It is how the explicit, versioned
    re-extraction path the issue requires is expressed; nothing calls it
    implicitly. A forced re-extraction still runs `merge_edits`, so hand-
    corrected requirements survive it.

    Returns the profile id, or None when there is nothing to extract (no job, no
    description) or the extraction failed — in which case no row is written and
    the absent profile reproduces today's behavior exactly.
    """
    from datetime import datetime

    from agents.jd_profile import (
        PROFILE_VERSION, compile_profile_payload, extract_profile,
        extraction_key, merge_edits, payload_digest,
    )
    from database.models import JDProfile

    try:
        with Session(engine) as session:
            job = session.get(JobDescription, job_id)
            if not job or not (job.description or "").strip():
                return None

            profile = session.exec(
                select(JDProfile).where(JDProfile.job_id == job_id)
            ).first()

            key = extraction_key(job.description)
            if (not force and profile is not None
                    and profile.extraction_key == key
                    and profile.extraction_version == PROFILE_VERSION):
                # The stored profile already describes this exact posting.
                return profile.profile_id

            extraction = extract_profile(
                job.title or "", job.company or "", job.description)
            if extraction is None:
                return profile.profile_id if profile else None

            payload = compile_profile_payload(
                job.title or "", job.description, extraction)
            payload = merge_edits(profile.payload if profile else None, payload)
            digest = payload_digest(payload)
            now = datetime.utcnow()

            if profile is None:
                profile = JDProfile(job_id=job_id, user_id=user_id or job.user_id)
            profile.payload = payload
            profile.payload_hash = digest
            profile.extraction_key = key
            profile.extraction_version = PROFILE_VERSION
            profile.role_level = payload.get("role_level")
            profile.updated_at = now
            session.add(profile)
            session.commit()
            session.refresh(profile)
            return profile.profile_id
    except Exception as exc:
        logger.warning("JD profile rebuild failed for job %s: %s", job_id, exc)
        return None


def load_jd_profile(job_id: UUID, backfill: bool = False) -> Optional[dict]:
    """This job's JD profile as a plain dict, or None when it has none.

    The read path for downstream consumers (#125 weighting, #126 semantic
    coverage, #151 skill decomposition). Returns None rather than raising or
    synthesizing an empty profile, so every consumer's absent-profile branch is
    the pre-#121 behavior.

    *backfill* extracts on miss, for jobs analyzed before this shipped — those
    have no profile and would otherwise never gain one. It is off by default
    because it can spend an LLM call, so a caller opts in knowingly.
    """
    from database.models import JDProfile

    try:
        with Session(engine) as session:
            profile = session.exec(
                select(JDProfile).where(JDProfile.job_id == job_id)
            ).first()
            if profile is None and backfill:
                session.close()
                if rebuild_jd_profile(job_id) is None:
                    return None
                with Session(engine) as s2:
                    profile = s2.exec(
                        select(JDProfile).where(JDProfile.job_id == job_id)
                    ).first()
                    return _jd_profile_dict(profile) if profile else None
            return _jd_profile_dict(profile) if profile else None
    except Exception as exc:
        logger.warning("load_jd_profile failed for job %s: %s", job_id, exc)
        return None


def _jd_profile_dict(profile) -> dict:
    payload = profile.payload
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (ValueError, TypeError):
            payload = {}
    weights = profile.weights
    if isinstance(weights, str):
        try:
            weights = json.loads(weights)
        except (ValueError, TypeError):
            weights = {}
    return {
        "profile_id": profile.profile_id,
        "job_id": profile.job_id,
        "payload": payload or {},
        "payload_hash": profile.payload_hash,
        "extraction_version": profile.extraction_version,
        "role_level": profile.role_level,
        "weights": weights or {},
        "updated_at": profile.updated_at,
    }


# Fields a human is allowed to correct. `terms` and `text` are included because
# a mis-split requirement is one of the failure modes worth fixing by hand;
# `ordinal` is not, because reordering would destroy the source-order signal
# #125 reads, and `edited` is set by this function rather than supplied.
_EDITABLE_REQUIREMENT_FIELDS = {
    "text", "type", "criticality", "terms", "source_section", "confidence",
}


def update_jd_profile_requirements(job_id: UUID, edits: list[dict]) -> Optional[dict]:
    """Apply human corrections to requirements, keyed by `ordinal`.

    Each edit is `{"ordinal": N, <field>: <value>, ...}`. A touched requirement
    is stamped `edited=True`, which is what makes `merge_edits` carry it through
    a later re-extraction instead of silently overwriting the correction — the
    mitigation the issue requires for LLM-extracted structure that has no
    visible failure symptom.

    Returns the updated profile dict, or None if the job has no profile.
    Unknown fields and unknown ordinals are ignored rather than erroring: a
    partial correction is better than a rejected one.
    """
    from datetime import datetime

    from agents.jd_profile import (
        _clamp_confidence, _clamp_criticality, _clean_terms, payload_digest,
    )
    from agents.extraction_schemas import RequirementType
    from database.models import JDProfile

    valid_types = {t.value for t in RequirementType}
    try:
        with Session(engine) as session:
            profile = session.exec(
                select(JDProfile).where(JDProfile.job_id == job_id)
            ).first()
            if profile is None:
                return None

            payload = profile.payload
            if isinstance(payload, str):
                payload = json.loads(payload)
            payload = dict(payload or {})
            requirements = [dict(r) for r in (payload.get("requirements") or [])]
            by_ordinal = {r.get("ordinal"): r for r in requirements}

            for edit in edits or []:
                target = by_ordinal.get(edit.get("ordinal"))
                if target is None:
                    continue
                touched = False
                for field, value in edit.items():
                    if field not in _EDITABLE_REQUIREMENT_FIELDS:
                        continue
                    if field == "criticality":
                        value = _clamp_criticality(value)
                    elif field == "confidence":
                        value = _clamp_confidence(value)
                    elif field == "terms":
                        value = _clean_terms(value)
                    elif field == "type":
                        if value not in valid_types:
                            continue
                    elif field == "text":
                        value = str(value or "").strip()
                        if not value or value == target.get("text"):
                            continue
                        # Stash the text as extracted, once, so `merge_edits`
                        # can still recognize this requirement after a human
                        # rewrites it — identity across a re-extraction is the
                        # extracted text, not the corrected one.
                        target.setdefault("original_text", target.get("text"))
                    target[field] = value
                    touched = True
                if touched:
                    target["edited"] = True

            payload["requirements"] = requirements
            profile.payload = payload
            profile.payload_hash = payload_digest(payload)
            profile.updated_at = datetime.utcnow()
            session.add(profile)
            session.commit()
            session.refresh(profile)
            return _jd_profile_dict(profile)
    except Exception as exc:
        logger.warning("JD profile edit failed for job %s: %s", job_id, exc)
        return None


# ── User preferences (issue #129) ───────────────────────────────────────────
#
# **The write barrier.** `tailor()` reads preferences and never writes one, and
# the only function here that writes is reached by an explicit user decision.
# #118 established the rule for `layout_overrides` — a pipeline that can write
# the user's tier launders its own output into a counterfeit user choice — and
# it binds harder here, because these preferences are *inferred*. A pipeline
# allowed to write this table would suppress an item, observe the suppression,
# infer a standing preference from it, and then cite that preference back as the
# user's own instruction. So proposing is separated from persisting the same way
# #21 separates them, and there is no automatic-write path at all.

def _preference_dict(row) -> dict:
    provenance = row.provenance
    if isinstance(provenance, str):
        try:
            provenance = json.loads(provenance)
        except (ValueError, TypeError):
            provenance = {}
    return {
        "preference_id": str(row.preference_id),
        "text": row.text,
        "polarity": row.polarity,
        "target_type": row.target_type,
        "target_key": row.target_key,
        "target_term": row.target_term,
        "scope_type": row.scope_type,
        "scope_value": row.scope_value,
        "strength": row.strength,
        "status": row.status,
        "supersedes_id": str(row.supersedes_id) if row.supersedes_id else None,
        "confidence": row.confidence,
        "provenance": provenance or {},
        "edited": bool(row.edited),
        "extraction_version": row.extraction_version,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def load_preferences(user_id: UUID, include_inactive: bool = False) -> list[dict]:
    """This user's preferences, newest last. Read-only.

    Returns [] on any failure rather than raising: an unreadable preference
    table must degrade to pre-#129 tailoring, never break a run.
    """
    from datetime import datetime

    from agents.preferences import STATUS_ACTIVE
    from database.models import UserPreference

    try:
        with Session(engine) as session:
            rows = session.exec(
                select(UserPreference).where(UserPreference.user_id == user_id)
            ).all()
            out = [_preference_dict(r) for r in rows]
    except Exception as exc:
        logger.warning("load_preferences failed for user %s: %s", user_id, exc)
        return []
    if not include_inactive:
        out = [p for p in out if p["status"] == STATUS_ACTIVE]
    out.sort(key=lambda p: (p["created_at"] or datetime.min, p["preference_id"]))
    return out


def propose_preferences(
    user_id: UUID, job_id: Optional[str], messages: list[dict],
) -> list[dict]:
    """Preference proposals from this chat. **Writes nothing.**

    Returns each compiled preference plus a `decision` of `add` / `supersede` /
    `no_op` against what the user already holds. `no_op` proposals are kept in
    the return value rather than filtered here so the caller can decide whether
    to show "already recorded" — the router filters them, matching #21.
    """
    from agents.preferences import (
        build_transcript, compile_preferences, extract_preference_notes,
        resolve_against_existing, target_catalog,
    )

    try:
        catalog = target_catalog(user_id)
        notes = extract_preference_notes(build_transcript(messages), catalog)
        if not notes:
            return []
        compiled = compile_preferences(notes, catalog, provenance={
            "job_id": str(job_id) if job_id else None,
            "source": "chat",
        })
        return resolve_against_existing(compiled, load_preferences(user_id))
    except Exception as exc:
        logger.warning("propose_preferences failed for user %s: %s", user_id, exc)
        return []


def apply_preference_decision(user_id: UUID, proposal: dict) -> str:
    """Persist one accepted proposal. The **only** write path into this table.

    A `supersede` marks the prior preference `superseded` and links the new row
    to it — it never deletes, because a contradicted preference must not expire
    (#133) and the transition is the signal #51 Phase 2 learns from. A `no_op`
    writes nothing and says so.

    Returns a plain-English result string. Never raises.
    """
    from datetime import datetime

    from agents.preferences import (
        POLARITIES, SCOPE_TYPES, STATUS_ACTIVE, STATUS_SUPERSEDED,
        TARGET_TYPES, _clamp_confidence, _clamp_strength,
    )
    from database.models import UserPreference

    try:
        text = (proposal.get("text") or "").strip()
        if not text:
            return "Nothing to save — the preference has no text."
        if (proposal.get("decision") or "add") == "no_op":
            return "Already recorded — nothing changed."

        polarity = proposal.get("polarity") or "suppress"
        if polarity not in POLARITIES:
            polarity = "suppress"
        target_type = proposal.get("target_type") or "topic"
        if target_type not in TARGET_TYPES:
            target_type = "topic"
        scope_type = proposal.get("scope_type") or "job"
        if scope_type not in SCOPE_TYPES:
            scope_type = "job"

        with Session(engine) as session:
            superseded = None
            raw_prior = proposal.get("supersedes_id")
            if raw_prior:
                try:
                    prior = session.get(UserPreference, UUID(str(raw_prior)))
                except (ValueError, TypeError):
                    prior = None
                # Owner check: a proposal is client-held round-tripped state, so
                # the id in it is untrusted (issue #73).
                if prior is not None and prior.user_id == user_id:
                    prior.status = STATUS_SUPERSEDED
                    prior.updated_at = datetime.utcnow()
                    session.add(prior)
                    superseded = prior.preference_id

            row = UserPreference(
                user_id=user_id,
                text=text,
                polarity=polarity,
                target_type=target_type,
                target_key=proposal.get("target_key") or None,
                target_term=proposal.get("target_term") or None,
                scope_type=scope_type,
                scope_value=proposal.get("scope_value") or None,
                strength=_clamp_strength(proposal.get("strength")),
                status=STATUS_ACTIVE,
                supersedes_id=superseded,
                confidence=_clamp_confidence(proposal.get("confidence")),
                provenance=proposal.get("provenance") or {},
                extraction_version=int(proposal.get("extraction_version") or 1),
            )
            session.add(row)
            session.commit()

        if superseded:
            return f"Updated — this replaces what you told me earlier: {text}"
        return f"Saved: {text}"
    except Exception as exc:
        logger.warning("apply_preference_decision failed for user %s: %s", user_id, exc)
        return "Could not save that preference."


# Fields a human may correct. `polarity` and `strength` are the two that change
# what the arbitration does, so they are the two that most need fixing when the
# extraction reads a passing remark as an absolute rule. `target_key` is not
# editable by hand: it must stay a key the planner can bind, and a free-text
# correction would produce one that silently matches nothing.
_EDITABLE_PREFERENCE_FIELDS = {
    "text", "polarity", "strength", "scope_type", "scope_value", "target_term",
}


def update_preference(
    user_id: UUID, preference_id: UUID, edits: dict,
) -> Optional[dict]:
    """Apply a human correction. Stamps `edited=True`.

    That flag means the same thing it means on a JD requirement: a correction is
    never silently overwritten by a later extraction. It does not freeze the
    preference — an explicit later reversal in chat still supersedes it, because
    the user changing their mind out loud is not an extraction error.

    Returns the updated preference, or None when it does not exist or is not
    this user's.
    """
    from datetime import datetime

    from agents.preferences import (
        POLARITIES, SCOPE_TYPES, _clamp_strength,
    )
    from database.models import UserPreference

    try:
        with Session(engine) as session:
            row = session.get(UserPreference, preference_id)
            if row is None or row.user_id != user_id:
                return None
            touched = False
            for field, value in (edits or {}).items():
                if field not in _EDITABLE_PREFERENCE_FIELDS:
                    continue
                if field == "polarity":
                    if value not in POLARITIES:
                        continue
                elif field == "scope_type":
                    if value not in SCOPE_TYPES:
                        continue
                elif field == "strength":
                    value = _clamp_strength(value)
                elif field == "text":
                    value = str(value or "").strip()
                    if not value:
                        continue
                setattr(row, field, value)
                touched = True
            if touched:
                row.edited = True
                row.updated_at = datetime.utcnow()
                session.add(row)
                session.commit()
                session.refresh(row)
            return _preference_dict(row)
    except Exception as exc:
        logger.warning("update_preference failed for %s: %s", preference_id, exc)
        return None


def retract_preference(user_id: UUID, preference_id: UUID) -> Optional[dict]:
    """Withdraw a preference. Sets `status='retracted'`; never deletes the row.

    Retraction is explicit and distinct from supersession: superseded means the
    user replaced it with a different preference, retracted means they took it
    back. Both stay on the table, so the profile remains a complete record of
    what the user asked for and when.
    """
    from datetime import datetime

    from agents.preferences import STATUS_RETRACTED
    from database.models import UserPreference

    try:
        with Session(engine) as session:
            row = session.get(UserPreference, preference_id)
            if row is None or row.user_id != user_id:
                return None
            row.status = STATUS_RETRACTED
            row.updated_at = datetime.utcnow()
            session.add(row)
            session.commit()
            session.refresh(row)
            return _preference_dict(row)
    except Exception as exc:
        logger.warning("retract_preference failed for %s: %s", preference_id, exc)
        return None


# ── Keyword weights (issue #125) ────────────────────────────────────────────

def build_support_index(user_id: UUID) -> dict:
    """The candidate's supportability evidence, as two token lists.

        {"strong": [...], "claimed": [...]}

    **strong** — tokens the candidate can point at logged work for: every token
    of a skill name the knowledge graph ties to a project or an experience, plus
    every token of the projects' and experiences' own text (names, titles,
    companies, descriptions, bullets). Those are the words already in their
    history, so covering them in a resume is restating, not inventing.

    **claimed** — tokens of a skill on the profile that *no* project or
    experience backs. The user says they have it and there is no logged work
    behind it, which is exactly the partial-credit case: reframing can earn it,
    asserting it outright cannot.

    Both lists are sorted, so the persisted weights blob is byte-stable for a
    fixed graph. Returns empty lists on any failure — the scorer reads that as
    "no supportability information" and falls back to uniform weighting rather
    than scoring every term zero.
    """
    from agents.keyword_weights import _tokens
    from knowledge_graph.builder import SkillGraphBuilder

    strong: set = set()
    claimed: set = set()
    try:
        builder = SkillGraphBuilder(user_id)
        builder.build_graph()

        with Session(engine) as session:
            skill_rows = session.exec(
                select(Skill.name)
                .join(UserSkill, UserSkill.skill_id == Skill.skill_id)
                .where(UserSkill.user_id == user_id)
            ).all()
            projects = session.exec(
                select(Project).where(Project.user_id == user_id)
            ).all()
            experiences = session.exec(
                select(Experience).where(Experience.user_id == user_id)
            ).all()

            for name in skill_rows:
                if not name:
                    continue
                backed = bool(
                    builder.get_projects_using_skill(name)
                    or builder.get_experiences_using_skill(name)
                )
                (strong if backed else claimed).update(_tokens(name))

            for proj in projects:
                strong |= _tokens(f"{proj.name or ''} {proj.description or ''}")
            for exp in experiences:
                bullets = " ".join(str(b) for b in (exp.bullets or []))
                strong |= _tokens(
                    f"{exp.title or ''} {exp.company or ''} "
                    f"{exp.description or ''} {bullets}"
                )
    except Exception as exc:
        logger.warning("support index build failed for user %s: %s", user_id, exc)
        return {"strong": [], "claimed": []}

    # A token with logged work behind it is never demoted to the partial tier.
    claimed -= strong
    return {"strong": sorted(strong), "claimed": sorted(claimed)}


def resolve_keyword_weights(
    job_id: UUID,
    user_id: Optional[UUID] = None,
    jd_text: Optional[str] = None,
    persist: bool = True,
) -> Optional[dict]:
    """Term -> weight for this job and candidate, persisting the map. Never raises.

    Returns `None` when the job has no #121 profile — every job analyzed before
    #121 shipped and every job whose extraction failed — which is what makes the
    scorer's uniform fallback the exact pre-#125 behavior rather than an
    approximation of it.

    **Recomputed on every call, then persisted.** The weights are a pure
    function of the profile payload, the JD text and the candidate's graph, and
    computing them costs three small queries and no model call, so caching them
    would buy nothing and would go stale the moment the user ingests another
    project — silently, and in the direction that matters (a term that just
    became supportable would keep weighing zero). Persistence exists so the map
    is inspectable and so #51 Phase 2 can replay the exact reward a run was
    scored against, not as a cache.

    *persist* False returns the same map without writing, for read-only callers
    — `plan_preview` (issue #91) guarantees a preview performs no DB writes.
    """
    from datetime import datetime

    from agents.keyword_weights import compute_weights, weights_digest
    from database.models import JDProfile

    try:
        with Session(engine) as session:
            profile = session.exec(
                select(JDProfile).where(JDProfile.job_id == job_id)
            ).first()
            if profile is None:
                return None

            if jd_text is None:
                job = session.get(JobDescription, job_id)
                jd_text = (job.description if job else "") or ""

            payload = profile.payload
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except (ValueError, TypeError):
                    payload = {}

            support = build_support_index(user_id or profile.user_id)
            blob = compute_weights(payload or {}, jd_text, support)

            stored = profile.weights
            if isinstance(stored, str):
                try:
                    stored = json.loads(stored)
                except (ValueError, TypeError):
                    stored = None
            if persist and weights_digest(stored) != weights_digest(blob):
                profile.weights = blob
                profile.updated_at = datetime.utcnow()
                session.add(profile)
                session.commit()

            return blob.get("terms") or {}
    except Exception as exc:
        logger.warning("keyword weight resolution failed for job %s: %s", job_id, exc)
        return None


def delete_resume(user_id: UUID) -> None:
    """Clear resume_path on the User row. Does not delete the file or any ingested data."""
    with Session(engine) as session:
        user = session.get(User, user_id)
        if user:
            user.resume_path = None
            session.add(user)
            session.commit()


def delete_job(job_uuid: str) -> str:
    """Delete a JobDescription and all dependent rows (UserJobResult, JobSkill,
    ChatMessage, JobCard, JDProfile).
    Returns plain-English result. Never raises.

    JobCard and JDProfile both carry a real FK to `jobdescription.job_id`, which
    Postgres enforces and SQLite does not (foreign_keys is off by default). Left
    out, deleting an analyzed job succeeds locally and fails in production.
    """
    try:
        from uuid import UUID as _UUID
        from database.models import JDProfile, JobCard
        jid = _UUID(job_uuid)
        with Session(engine) as session:
            session.exec(delete(UserJobResult).where(UserJobResult.job_id == jid))
            session.exec(delete(JobSkill).where(JobSkill.job_id == jid))
            session.exec(delete(ChatMessage).where(ChatMessage.job_id == jid))
            session.exec(delete(JobCard).where(JobCard.job_id == jid))
            session.exec(delete(JDProfile).where(JDProfile.job_id == jid))
            session.commit()
            job = session.get(JobDescription, jid)
            if job:
                session.delete(job)
                session.commit()
        return "Job deleted."
    except Exception as e:
        return f"Failed to delete job: {e}"


# ── Per-job tailor-run budget (issue 70) ─────────────────────

def job_tailor_limit() -> int:
    """Lifetime cap on tailor runs per job. The first tailor consumes one."""
    import os
    try:
        return max(1, int(os.getenv("JOB_TAILOR_LIMIT", "5")))
    except ValueError:
        return 5


def tailor_runs_remaining(job: JobDescription) -> int:
    return max(0, job_tailor_limit() - (job.retailor_count or 0))


# ── Chat history (persisted per job) ────────────────────────

_MAX_CHAT_MESSAGES_PER_JOB = 100


def _acting_user_id() -> Optional[UUID]:
    """The current acting user's id (request binding or CLI pointer), or None."""
    from database.user_utils import get_active_profile
    user = get_active_profile()
    return user.user_id if user else None


def save_chat_message(job_id: Optional[str], role: str, content: str) -> None:
    """Persist one message to the ChatMessage table. Never raises.

    Stamped with the acting user so landing-context messages (job_id=None)
    stay isolated between users (issue #73).
    """
    try:
        jid = UUID(job_id) if job_id else None
        uid = _acting_user_id()
        with Session(engine) as session:
            session.add(ChatMessage(job_id=jid, user_id=uid, role=role, content=content))
            session.commit()
        _prune_chat_messages(jid, user_id=uid)
    except Exception as e:
        logger.warning("save_chat_message failed: %s", e)


def _prune_chat_messages(
    jid: Optional[UUID],
    keep: int = _MAX_CHAT_MESSAGES_PER_JOB,
    user_id: Optional[UUID] = None,
) -> None:
    """Delete oldest messages beyond `keep` for the given job_id. Never raises.

    Landing context (jid=None) prunes only the given user's messages.
    """
    try:
        with Session(engine) as session:
            query = (
                select(ChatMessage.message_id)
                .where(ChatMessage.job_id == jid)
                .order_by(ChatMessage.created_at.desc())
            )
            if jid is None:
                query = query.where(ChatMessage.user_id == user_id)
            ids = session.exec(query).all()
            if len(ids) > keep:
                to_delete = list(ids[keep:])
                session.exec(delete(ChatMessage).where(ChatMessage.message_id.in_(to_delete)))
                session.commit()
    except Exception:
        pass


def load_chat_history(
    job_id: Optional[str], limit: int = 20, user_id: Optional[UUID] = None
) -> list[dict]:
    """Return the last `limit` messages for this job as {role, content} dicts, oldest-first.
    Returns [] if none found or on error.

    Landing context (job_id=None) is scoped to `user_id` — or the acting user
    when not passed — so users never see each other's landing chat (issue #73).
    Job contexts are scoped by job_id; callers verify job ownership.
    """
    try:
        jid = UUID(job_id) if job_id else None
        with Session(engine) as session:
            query = (
                select(ChatMessage)
                .where(ChatMessage.job_id == jid)
                .order_by(ChatMessage.created_at.desc())
                .limit(limit)
            )
            if jid is None:
                uid = user_id if user_id is not None else _acting_user_id()
                query = query.where(ChatMessage.user_id == uid)
            msgs = session.exec(query).all()
        return [{"role": m.role, "content": m.content, "created_at": m.created_at.isoformat()} for m in reversed(msgs)]
    except Exception as e:
        logger.warning("load_chat_history failed: %s", e)
        return []


def save_chat_summary(job_id: Optional[str], summary: str) -> None:
    """Persist a conversation summary to JobDescription.chat_summary. Never raises."""
    if not job_id:
        return
    try:
        jid = UUID(job_id)
        with Session(engine) as session:
            job = session.get(JobDescription, jid)
            if job:
                job.chat_summary = summary
                session.add(job)
                session.commit()
    except Exception as e:
        logger.warning("save_chat_summary failed: %s", e)


def load_chat_summary(job_id: Optional[str]) -> Optional[str]:
    """Return the persisted chat summary for this job, or None if absent or on error."""
    if not job_id:
        return None
    try:
        jid = UUID(job_id)
        with Session(engine) as session:
            job = session.get(JobDescription, jid)
            return job.chat_summary if job else None
    except Exception as e:
        logger.warning("load_chat_summary failed: %s", e)
        return None


# ── Ingestion service functions ─────────────────────────────
# Each returns a plain-English result string and never raises.

def _backfill_contact_fields(user: User, style: dict) -> None:
    """Fill in header contact fields from a freshly-ingested resume (issue #75).

    Only sets a field when it's currently empty — never overwrites a value
    the user already has (manually entered, or from GitHub/LinkedIn connect).
    """
    values = (style or {}).get("header", {}).get("contact_values", {})
    if not values:
        return
    if not user.linkedin_url and values.get("linkedin"):
        user.linkedin_url = values["linkedin"]
    if not user.github_username and values.get("github"):
        user.github_username = values["github"]
    if not user.portfolio_url and values.get("portfolio"):
        user.portfolio_url = values["portfolio"]
    if not user.phone and values.get("phone"):
        user.phone = values["phone"]
    if not user.location and values.get("location"):
        user.location = values["location"]


def ingest_resume_file(file_path: str, display_name: str | None = None) -> str:
    """Parse a resume file (MD, PDF, DOCX) and save to DB.

    display_name: label to show in the result summary — pass the original upload
    filename when file_path is a server-side temp file.
    """
    from pathlib import Path
    path = Path(file_path)
    if not path.exists():
        return f"File not found: {file_path}"
    try:
        from database.db import init_db
        from database.user_utils import get_active_profile
        init_db()
        user = get_active_profile()
        pre = _snapshot_user_data(user.user_id) if user else (set(), set(), set())
        with _suppress_output():
            if file_path.endswith(".md"):
                ingestion_data = {
                    "source_file": "resume",  # normalized: prevents multi-resume duplicate rows
                    "full_text": path.read_text(encoding="utf-8"),
                    "parsed_sections": {},
                }
            else:
                from ingestion.resume import ResumeIngestor
                ingestion_data = ResumeIngestor().ingest(file_path)
                ingestion_data["source_file"] = "resume"  # normalize regardless of ingestor value
            from agents.parser import ResumeParserAgent
            ResumeParserAgent().parse_and_save(ingestion_data)
        if user:
            from ingestion.resume import extract_style_profile
            full_text = ingestion_data.get("full_text", "")
            style = ingestion_data.get("resume_style") or extract_style_profile(full_text)
            with Session(engine) as session:
                db_user = session.get(User, user.user_id)
                if db_user:
                    db_user.resume_markdown = full_text
                    db_user.resume_style = style
                    _backfill_contact_fields(db_user, style)
                    session.add(db_user)
                    session.commit()
            return _format_ingestion_diff(
                user.user_id, pre[0], pre[1], pre[2], display_name or path.name
            )
        return f"Resume ingested: {display_name or path.name}."
    except Exception as e:
        return f"Ingestion failed: {e}"


def _build_repo_metrics(repos: list) -> dict:
    """Map repo name -> GitHub signals for project complexity scoring (issue #46).

    Authorship signals (issue #155) are merged in only when the ingestor
    returned them, so repos scanned before the change — or skipped by the
    language gate — keep scoring on the original signals alone.
    """
    metrics = {}
    for repo in repos:
        entry = {
            "stars": repo.get("stars", 0),
            "languages": repo.get("languages", []),
            "readme_length": len(repo.get("readme") or ""),
        }
        contributions = repo.get("contributions")
        if contributions:
            entry.update(contributions)
            # >1 contributor means other people worked on it — stronger evidence
            # than a solo repo, and the distinction hiring-agent draws.
            entry["project_type"] = (
                "open_source" if contributions.get("contributors", 0) > 1 else "self_project"
            )
        metrics[repo["name"]] = entry
    return metrics


_GITHUB_RATE_LIMIT_MESSAGE = (
    "GitHub API rate limit reached. This server ingests without a dedicated "
    "GitHub token, so unauthenticated requests share a 60/hour limit across all "
    "users — try again in a few minutes, or connect your GitHub account "
    "(Profile menu) for a much higher limit."
)


def ingest_github(username: str = "", token: str | None = None) -> str:
    """Fetch GitHub repos for a user and save skills/projects to DB.

    token: OAuth or PAT to use. Falls back to GITHUB_TOKEN env var if not provided.
    """
    from config import GITHUB_USERNAME, GITHUB_TOKEN
    target = username.strip() or GITHUB_USERNAME
    if not target:
        return "No GitHub username provided and GITHUB_USERNAME is not set in .env."
    auth_token = token or GITHUB_TOKEN
    try:
        from database.db import init_db
        from database.user_utils import get_active_profile
        from ingestion.github import GitHubIngestor, GitHubRateLimitError
        from agents.parser import ResumeParserAgent
        init_db()
        user = get_active_profile()
        pre = _snapshot_user_data(user.user_id) if user else (set(), set(), set())
        with _suppress_output():
            repos = GitHubIngestor(username=target, token=auth_token).ingest()
            if not repos:
                return f"No new or updated repos found for {target}."
            lines = []
            for repo in repos:
                desc = repo.get("description") or "No description"
                langs = ", ".join(repo.get("languages", []))
                lines += [
                    f"Project: {repo['name']}", f"Description: {desc}",
                    f"Languages: {langs}", f"URL: {repo.get('url', '')}",
                ]
                if repo.get("readme"):
                    lines.append(f"README:\n{repo['readme']}")
                for dep_file, dep_content in repo.get("dependencies", {}).items():
                    lines.append(f"{dep_file}:\n{dep_content}")
                lines.append("")
            ResumeParserAgent().parse_and_save({
                "source_file": f"github:{target}",
                "full_text": "\n".join(lines),
                "parsed_sections": {},
                "repo_metrics": _build_repo_metrics(repos),
            })
        if user:
            return _format_ingestion_diff(user.user_id, pre[0], pre[1], pre[2], f"github:{target} ({len(repos)} repos)")
        return f"GitHub ingested: {len(repos)} repos parsed for {target}."
    except GitHubRateLimitError:
        return _GITHUB_RATE_LIMIT_MESSAGE
    except Exception as e:
        return f"GitHub ingestion failed: {e}"


def parse_github_repo_ref(repo_ref: str) -> "tuple[str, str] | None":
    """Parse a GitHub repo ref into (owner, repo_name). Accepts owner/repo or full GitHub URLs."""
    import re
    ref = repo_ref.strip().rstrip("/")
    m = re.match(r'^https?://github\.com/([^/\s]+)/([^/\s]+?)(?:\.git)?$', ref)
    if m:
        return m.group(1), m.group(2)
    m = re.match(r'^([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)$', ref)
    if m:
        return m.group(1), m.group(2)
    return None


def ingest_github_repo(repo_ref: str, token: str | None = None) -> str:
    """Fetch a single GitHub repo and save to DB. Returns a plain-English summary string and never raises.

    token: OAuth or PAT to use. Falls back to GITHUB_TOKEN env var if not provided.
    """
    parsed = parse_github_repo_ref(repo_ref)
    if not parsed:
        return (
            f"Invalid GitHub repo ref: '{repo_ref}'. "
            "Use owner/repo (e.g. openai/evals) or https://github.com/owner/repo."
        )
    owner, repo_name = parsed
    try:
        from config import GITHUB_TOKEN
        from database.db import init_db
        from database.user_utils import get_active_profile
        from ingestion.github import GitHubIngestor, GitHubRateLimitError
        from agents.parser import ResumeParserAgent
        init_db()
        auth_token = token or GITHUB_TOKEN
        user = get_active_profile()
        pre = _snapshot_user_data(user.user_id) if user else (set(), set(), set())
        with _suppress_output():
            repo = GitHubIngestor.fetch_repo(owner, repo_name, token=auth_token)
            if not repo:
                return f"Could not fetch {owner}/{repo_name}. Check the owner/repo name and your network connection."
            langs = ", ".join(repo.get("languages", [])) or "unknown"
            lines = [
                f"Project: {repo['name']}",
                f"Description: {repo.get('description') or 'No description'}",
                f"Languages: {langs}",
                f"URL: {repo.get('url', '')}",
            ]
            if repo.get("readme"):
                lines.append(f"README:\n{repo['readme']}")
            for dep_file, dep_content in repo.get("dependencies", {}).items():
                lines.append(f"{dep_file}:\n{dep_content}")
            ResumeParserAgent().parse_and_save({
                "source_file": f"github:{owner}/{repo_name}",
                "full_text": "\n".join(lines),
                "parsed_sections": {},
                "repo_metrics": _build_repo_metrics([repo]),
            })
        has_readme = "yes" if repo.get("readme") else "no"
        has_deps = "yes" if repo.get("dependencies") else "no"
        if user:
            diff = _format_ingestion_diff(
                user.user_id, pre[0], pre[1], pre[2],
                f"single repo: {owner}/{repo_name}",
            )
            return (
                f"Single repo ingest: {owner}/{repo_name}\n"
                f"Owner: {owner} | Languages: {langs} | README: {has_readme} | Dependency files: {has_deps}\n\n"
                + diff
            )
        return (
            f"Single repo ingested: {owner}/{repo_name}\n"
            f"Owner: {owner} | Languages: {langs} | README: {has_readme} | Dependency files: {has_deps}"
        )
    except GitHubRateLimitError:
        return _GITHUB_RATE_LIMIT_MESSAGE
    except Exception as e:
        return f"Repo ingestion failed: {e}"


def _set_linkedin_status(
    user_id: Optional[UUID],
    status: Optional[str],
    error: Optional[str] = None,
    ingested_url: Optional[str] = None,
) -> None:
    """Record the LinkedIn ingestion lifecycle on the user row."""
    if user_id is None:
        return
    from datetime import datetime
    try:
        with Session(engine) as session:
            db_user = session.get(User, user_id)
            if not db_user:
                return
            db_user.linkedin_ingest_status = status
            db_user.linkedin_ingest_error = error
            if status == "done":
                db_user.linkedin_ingested_at = datetime.utcnow()
                if ingested_url:
                    db_user.linkedin_ingested_url = ingested_url
            session.add(db_user)
            session.commit()
    except Exception as e:
        logger.error("Failed to update LinkedIn ingest status: %s", e)


def _persist_linkedin_raw(user_id: Optional[UUID], record: Optional[dict]) -> None:
    """Store the raw Bright Data scrape record (JSON) on the user row (issue #69).

    Best-effort and user-scoped: a persistence failure must never fail the
    ingest itself. A falsy/non-dict record is ignored so a bad scrape doesn't
    wipe a previously stored good one.
    """
    if user_id is None or not isinstance(record, dict) or not record:
        return
    try:
        payload = json.dumps(record, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        logger.warning("Could not serialize LinkedIn raw record: %s", exc)
        return
    try:
        with Session(engine) as session:
            db_user = session.get(User, user_id)
            if not db_user:
                return
            db_user.linkedin_raw_record = payload
            session.add(db_user)
            session.commit()
    except Exception as exc:
        logger.error("Failed to persist LinkedIn raw record: %s", exc)


def replay_linkedin(user_id: Optional[UUID] = None) -> str:
    """Re-run LinkedIn structured mapping against the stored raw scrape (issue #69).

    Lets mapping improvements be applied to an existing profile without a new,
    paid Bright Data scrape. Reads the raw record persisted on the user row,
    reconstructs the parser's ingestion payload, and re-runs parse_and_save.
    Never raises — returns a plain-English result.
    """
    from database.db import init_db
    from database.user_utils import get_active_profile, set_request_user
    from ingestion.linkedin import LinkedInIngestor
    from agents.parser import ResumeParserAgent

    init_db()
    if user_id is None:
        user = get_active_profile()
        user_id = user.user_id if user else None
    else:
        set_request_user(user_id)
    if user_id is None:
        return "No active profile to replay LinkedIn data for."

    with Session(engine) as session:
        db_user = session.get(User, user_id)
        raw = db_user.linkedin_raw_record if db_user else None
        url = (db_user.linkedin_ingested_url if db_user else None) or ""
    if not raw:
        return "No stored LinkedIn scrape to replay. Run a LinkedIn import first."
    try:
        record = json.loads(raw)
    except (TypeError, ValueError):
        return "Stored LinkedIn scrape is corrupt and cannot be replayed."

    pre = _snapshot_user_data(user_id)
    data = {
        "source_type": "linkedin",
        "source_file": f"linkedin:{url}",
        "full_text": LinkedInIngestor()._brightdata_to_text(record, url),
        "linkedin_record": record,
    }
    try:
        with _suppress_output():
            ResumeParserAgent().parse_and_save(data)
    except Exception as exc:
        logger.error("LinkedIn replay failed: %s", exc)
        return f"LinkedIn replay failed: {exc}"
    return _format_ingestion_diff(user_id, pre[0], pre[1], pre[2], "LinkedIn (replay)")


def ingest_linkedin(profile_url: str, user_id: Optional[UUID] = None) -> str:
    """
    Scrape a LinkedIn profile via Bright Data and save it to the DB.

    Records the ingestion lifecycle (importing/done/failed) on the user row so
    the UI can poll for progress. Never raises — returns a plain-English result.
    """
    from database.db import init_db
    from database.user_utils import get_active_profile
    from ingestion.linkedin import LinkedInIngestor, LinkedInIngestionError
    from agents.parser import ResumeParserAgent

    init_db()
    if user_id is None:
        user = get_active_profile()
        user_id = user.user_id if user else None
    else:
        # Point the parser at this user (parse_and_save uses the active profile).
        # Context-scoped, not the shared pointer file — background LinkedIn
        # ingests for different users must not race each other (issue #73).
        from database.user_utils import set_request_user
        set_request_user(user_id)

    _set_linkedin_status(user_id, "importing")
    pre = _snapshot_user_data(user_id) if user_id else (set(), set(), set())
    try:
        with _suppress_output():
            data = LinkedInIngestor().ingest_brightdata(profile_url)
            # Persist the raw scrape before mapping so future mapping
            # improvements can be replayed without a new paid scrape (issue #69).
            _persist_linkedin_raw(user_id, data.get("linkedin_record"))
            ResumeParserAgent().parse_and_save(data)
    except LinkedInIngestionError as e:
        _set_linkedin_status(user_id, "failed", error=str(e))
        return f"LinkedIn import failed: {e}"
    except Exception as e:
        logger.error("LinkedIn ingestion failed: %s", e)
        _set_linkedin_status(user_id, "failed", error=str(e))
        return f"LinkedIn import failed: {e}"

    ingested_url = data.get("source_file", "").replace("linkedin:", "")
    _set_linkedin_status(user_id, "done", ingested_url=ingested_url)
    if user_id:
        return _format_ingestion_diff(user_id, pre[0], pre[1], pre[2], "LinkedIn")
    return "LinkedIn profile ingested."


def ingest_linkedin_pdf(file_path: str, display_name: str | None = None) -> str:
    """Parse a LinkedIn PDF export and save to DB.

    display_name: label to show in the result summary — pass the original upload
    filename when file_path is a server-side temp file.
    """
    from pathlib import Path
    if not Path(file_path).exists():
        return f"File not found: {file_path}"
    try:
        from database.db import init_db
        from database.user_utils import get_active_profile
        from ingestion.linkedin import LinkedInIngestor
        from agents.parser import ResumeParserAgent
        init_db()
        user = get_active_profile()
        pre = _snapshot_user_data(user.user_id) if user else (set(), set(), set())
        with _suppress_output():
            data = LinkedInIngestor().ingest_pdf(file_path)
            ResumeParserAgent().parse_and_save(data)
        if user:
            return _format_ingestion_diff(
                user.user_id, pre[0], pre[1], pre[2], display_name or Path(file_path).name
            )
        return f"LinkedIn PDF ingested: {display_name or Path(file_path).name}."
    except Exception as e:
        return f"LinkedIn PDF ingestion failed: {e}"
