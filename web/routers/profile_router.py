from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlmodel import Session, select

from database.db import engine
from database.models import User, UserSkill, Experience, Project
from web.auth import get_current_user
from tui import services

router = APIRouter(prefix="/api/profile", tags=["profile"])


class UpdateProfileBody(BaseModel):
    name: str = ""
    github_username: str = ""
    linkedin_url: str = ""
    phone: str = ""
    email: str = ""
    location: str = ""


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
        "skills": skill_count,
        "experiences": exp_count,
        "projects": proj_count,
        "sources": sorted(source_set),
    }


@router.patch("/")
def update_profile(body: UpdateProfileBody, user: User = Depends(get_current_user)):
    result = services.update_profile(
        user_id=user.user_id,
        name=body.name,
        github_username=body.github_username,
        linkedin_url=body.linkedin_url,
        phone=body.phone,
        email=body.email,
        location=body.location,
    )
    return {"result": result}


@router.get("/skills")
def get_skills(user: User = Depends(get_current_user)):
    return services.get_skills(user.user_id)


@router.get("/experiences")
def get_experiences(user: User = Depends(get_current_user)):
    return services.get_experiences(user.user_id)


@router.get("/projects")
def get_projects(user: User = Depends(get_current_user)):
    return services.get_projects(user.user_id)


@router.get("/graph")
def get_graph(user: User = Depends(get_current_user)):
    return services.get_graph_summary(user.user_id)
