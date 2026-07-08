from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel
from sqlmodel import Session, select

from config import BRIGHTDATA_API_KEY
from database.db import engine
from database.models import User, UserSkill, Experience, Project
from ingestion.linkedin import LinkedInIngestor
from web.auth import get_current_user
from web.routers.dependencies import linkedin_quota_remaining, increment_linkedin_usage
import services

router = APIRouter(prefix="/api/profile", tags=["profile"])


def _linkedin_ingest_task(user_id, url: str, email: str = "") -> None:
    """Background job: scrape LinkedIn via Bright Data after a profile update.

    Self-guards against the daily LinkedIn cap so the auto-trigger path can't
    bypass the rate limit enforced on the manual endpoint.
    """
    with Session(engine) as session:
        if not linkedin_quota_remaining(session, user_id, email):
            return
        increment_linkedin_usage(user_id, session)
    services.ingest_linkedin(url, user_id)


class UpdateProfileBody(BaseModel):
    name: str = ""
    github_username: str = ""
    linkedin_url: str = ""
    phone: str = ""
    email: str = ""
    location: str = ""
    portfolio_url: str = ""


@router.get("/")
def get_profile(user: User = Depends(get_current_user)):
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
        source_set: set[str] = set()
        for us in session.exec(
            select(UserSkill).where(UserSkill.user_id == user.user_id)
        ).all():
            if us.evidence_source:
                source_set.add(us.evidence_source.split(":")[0])

    default_email = "user@example.com"
    return {
        "user_id": str(user.user_id),
        "name": user.name or "",
        "email": "" if (not user.email or user.email == default_email) else user.email,
        "phone": user.phone or "",
        "location": user.location or "",
        "github_username": user.github_username or "",
        "linkedin_url": user.linkedin_url or "",
        "portfolio_url": user.portfolio_url or "",
        "linkedin_ingest_status": user.linkedin_ingest_status,
        "linkedin_ingest_error": user.linkedin_ingest_error,
        "linkedin_ingested_at": user.linkedin_ingested_at.isoformat() if user.linkedin_ingested_at else None,
        "skills": skill_count,
        "experiences": exp_count,
        "projects": proj_count,
        "sources": sorted(source_set),
    }


@router.patch("/")
def update_profile(
    body: UpdateProfileBody,
    background: BackgroundTasks,
    user: User = Depends(get_current_user),
):
    prev_ingested = user.linkedin_ingested_url
    result = services.update_profile(
        user_id=user.user_id,
        name=body.name,
        github_username=body.github_username,
        linkedin_url=body.linkedin_url,
        phone=body.phone,
        email=body.email,
        location=body.location,
        portfolio_url=body.portfolio_url,
    )

    # Auto-trigger LinkedIn ingestion when the URL is newly set or changed.
    # This is the "initialize / update the knowledge graph" hook: the scrape
    # runs in the background so the save returns immediately.
    new_url = (body.linkedin_url or "").strip()
    if new_url and BRIGHTDATA_API_KEY:
        try:
            normalized = LinkedInIngestor._normalize_url(new_url)
        except Exception:
            normalized = None
        already = LinkedInIngestor._normalize_url(prev_ingested) if prev_ingested else None
        if normalized and normalized != already:
            background.add_task(
                _linkedin_ingest_task, user.user_id, normalized, user.email
            )

    return {"result": result}


@router.get("/skills")
def get_skills(user: User = Depends(get_current_user)):
    return services.get_skills(user.user_id)


class SkillCoreUpdate(BaseModel):
    name: str
    is_core: bool


@router.post("/skills/core")
def set_skill_core(body: SkillCoreUpdate, user: User = Depends(get_current_user)):
    """Pin/unpin a skill as 'core' so it always renders in tailored output (issue #54)."""
    return {"result": services.set_skill_core(user.user_id, body.name, body.is_core)}


@router.get("/experiences")
def get_experiences(user: User = Depends(get_current_user)):
    return services.get_experiences(user.user_id)


@router.get("/education")
def get_education(user: User = Depends(get_current_user)):
    return services.get_education(user.user_id)


@router.get("/projects")
def get_projects(user: User = Depends(get_current_user)):
    return services.get_projects(user.user_id)


@router.get("/graph")
def get_graph(user: User = Depends(get_current_user)):
    return services.get_graph_summary(user.user_id)
