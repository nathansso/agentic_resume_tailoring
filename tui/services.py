"""
DB query functions extracted from tui/app.py widget methods.
Each function takes a user_id and returns plain data (lists/dicts), not widgets.
"""
from typing import Optional
from uuid import UUID

from sqlmodel import Session, select

from database.db import engine
from database.models import (
    Experience, JobDescription, Project,
    Skill, User, UserJobResult, UserSkill,
)


def get_first_user_id() -> Optional[UUID]:
    with Session(engine) as session:
        user = session.exec(select(User).limit(1)).first()
        return user.user_id if user else None


def get_skills(user_id: Optional[UUID]) -> list[dict]:
    if user_id is None:
        return []
    with Session(engine) as session:
        user_skills = session.exec(
            select(UserSkill).where(UserSkill.user_id == user_id)
        ).all()
        rows = []
        for us in user_skills:
            skill = session.get(Skill, us.skill_id)
            if skill:
                source = (us.evidence_source or "unknown").split(":")[0]
                rows.append({
                    "name": skill.name,
                    "source": source,
                    "proficiency": str(us.proficiency or "N/A"),
                    "confidence": f"{us.confidence_score:.1f}",
                })
        return rows


def get_experiences(user_id: Optional[UUID]) -> list[dict]:
    if user_id is None:
        return []
    with Session(engine) as session:
        exps = session.exec(
            select(Experience).where(Experience.user_id == user_id)
        ).all()
        return [
            {
                "title": e.title,
                "company": e.company,
                "start": e.start_date or "?",
                "end": e.end_date or "?",
            }
            for e in exps
        ]


def get_projects(user_id: Optional[UUID]) -> list[dict]:
    if user_id is None:
        return []
    with Session(engine) as session:
        projs = session.exec(
            select(Project).where(Project.user_id == user_id)
        ).all()
        return [
            {
                "name": p.name,
                "url": p.repo_url or "—",
                "desc": (p.description or "")[:60],
            }
            for p in projs
        ]


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
            })
        return result


def get_job_details(job_uuid: str) -> Optional[dict]:
    with Session(engine) as session:
        job = session.get(JobDescription, UUID(job_uuid))
        if not job:
            return None
        results = session.exec(
            select(UserJobResult).where(UserJobResult.job_id == job.job_id)
        ).all()
        detail: dict = {"title": job.title, "company": job.company}
        if results:
            latest = max(results, key=lambda r: r.created_at)
            detail["ats_score"] = latest.ats_score
            detail["matched_skills"] = list(latest.matched_skills.keys())[:10] if latest.matched_skills else []
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
