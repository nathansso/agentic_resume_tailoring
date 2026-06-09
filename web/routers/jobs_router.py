import asyncio
from datetime import datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sqlmodel import Session, select

from database.db import engine
from database.models import JobDescription, UserJobResult, User
from web.auth import get_current_user
from web.routers.dependencies import check_ai_quota, increment_ai_usage

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


class CreateJobBody(BaseModel):
    title: str
    company: str


class DescriptionBody(BaseModel):
    description: str


def _job_list_item(job: JobDescription, result: UserJobResult | None) -> dict:
    return {
        "job_id": str(job.job_id),
        "title": job.title,
        "company": job.company,
        "status": job.status or "created",
        "ats_score": result.ats_score if result else None,
    }


def _job_detail(job: JobDescription, result: UserJobResult | None) -> dict:
    base = _job_list_item(job, result)
    base["description"] = job.description or ""
    if result:
        matched = list(result.matched_skills.keys())[:10] if result.matched_skills else []
        missing = result.missing_skills[:10] if result.missing_skills else []
    else:
        matched, missing = [], []
    base["matched_skills"] = matched
    base["missing_skills"] = missing
    return base


def _latest_result(session: Session, job_id: UUID) -> UserJobResult | None:
    results = session.exec(
        select(UserJobResult).where(UserJobResult.job_id == job_id)
    ).all()
    return max(results, key=lambda r: r.created_at) if results else None


def _get_owned_job(job_id: str, user: User) -> tuple[JobDescription, Session]:
    try:
        jid = UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Job not found")
    session = Session(engine)
    job = session.get(JobDescription, jid)
    if not job:
        session.close()
        raise HTTPException(status_code=404, detail="Job not found")
    if job.user_id and str(job.user_id) != str(user.user_id):
        session.close()
        raise HTTPException(status_code=403, detail="Not your job")
    return job, session


# ── List jobs ────────────────────────────────────────────────

@router.get("/")
def list_jobs(user: User = Depends(get_current_user)):
    with Session(engine) as session:
        jobs = session.exec(
            select(JobDescription).where(JobDescription.user_id == user.user_id)
        ).all()
        result = []
        for job in jobs:
            latest = _latest_result(session, job.job_id)
            result.append(_job_list_item(job, latest))
    return result


# ── Create job ───────────────────────────────────────────────

@router.post("/")
def create_job(body: CreateJobBody, user: User = Depends(get_current_user)):
    if not body.title.strip() or not body.company.strip():
        raise HTTPException(status_code=422, detail="Title and company are required")
    with Session(engine) as session:
        job = JobDescription(
            title=body.title.strip(),
            company=body.company.strip(),
            description="",
            status="created",
            user_id=user.user_id,
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        return _job_list_item(job, None)


# ── Get job detail ───────────────────────────────────────────

@router.get("/{job_id}")
def get_job(job_id: str, user: User = Depends(get_current_user)):
    job, session = _get_owned_job(job_id, user)
    with session:
        latest = _latest_result(session, job.job_id)
        return _job_detail(job, latest)


# ── Save description ─────────────────────────────────────────

@router.post("/{job_id}/description")
def save_description(job_id: str, body: DescriptionBody, user: User = Depends(get_current_user)):
    job, session = _get_owned_job(job_id, user)
    with session:
        job.description = body.description.strip()
        session.add(job)
        session.commit()
        session.refresh(job)
        latest = _latest_result(session, job.job_id)
        return _job_detail(job, latest)


# ── Delete job ───────────────────────────────────────────────

@router.delete("/{job_id}")
def delete_job(job_id: str, user: User = Depends(get_current_user)):
    job, session = _get_owned_job(job_id, user)
    session.close()
    from tui import services
    result = services.delete_job(job_id)
    if result.startswith("Failed"):
        raise HTTPException(status_code=500, detail=result)
    return {"ok": True}


# ── Analyze job ──────────────────────────────────────────────

@router.post("/{job_id}/analyze")
async def analyze_job(job_id: str, user: User = Depends(get_current_user)):
    job, session = _get_owned_job(job_id, user)
    with session:
        if not job.description or not job.description.strip():
            raise HTTPException(status_code=422, detail="Paste a job description first before analyzing.")

        def _run():
            from agents.job_analyzer import JobAnalyzerAgent
            from agents.matcher import SkillMatcherAgent
            from sqlmodel import Session as S
            analyzer = JobAnalyzerAgent()
            analyzer.analyze_and_save({"raw_text": job.description, "source": "web", "job_id": str(job.job_id)})
            with S(engine) as s2:
                j2 = s2.get(JobDescription, job.job_id)
                if j2 and j2.status != "analyzed":
                    j2.status = "analyzed"
                    j2.updated_at = datetime.utcnow()
                    s2.add(j2)
                    s2.commit()
            SkillMatcherAgent().match(user.user_id, job.job_id)
            with S(engine) as s3:
                j3 = s3.get(JobDescription, job.job_id)
                if j3:
                    j3.status = "analyzed"
                    j3.updated_at = datetime.utcnow()
                    s3.add(j3)
                    s3.commit()

        await asyncio.to_thread(_run)
        with Session(engine) as s:
            refreshed = s.get(JobDescription, job.job_id)
            latest = _latest_result(s, job.job_id)
            return _job_detail(refreshed, latest)


# ── Tailor job ───────────────────────────────────────────────

@router.post("/{job_id}/tailor")
async def tailor_job(job_id: str, user: User = Depends(get_current_user), _quota: None = Depends(check_ai_quota)):
    job, session = _get_owned_job(job_id, user)
    with session:
        if job.status not in ("analyzed", "tailored", "exported"):
            raise HTTPException(status_code=422, detail="Analyze the job description before tailoring.")

        latest = _latest_result(session, job.job_id)
        if not latest:
            raise HTTPException(status_code=422, detail="No analysis result found — run Analyze first.")
        result_id = latest.result_id

    def _run():
        from agents.tailor import ResumeTailorAgent
        from tui.services import get_resume_style
        from sqlmodel import Session as S
        resume_text = ""
        with S(engine) as s:
            u = s.get(User, user.user_id)
            resume_text = (u.resume_markdown or "") if u else ""
        ResumeTailorAgent().tailor(user.user_id, UUID(job_id), result_id, resume_text)
        with S(engine) as s:
            j = s.get(JobDescription, UUID(job_id))
            if j:
                j.status = "tailored"
                j.updated_at = datetime.utcnow()
                s.add(j)
                s.commit()

    await asyncio.to_thread(_run)
    with Session(engine) as s:
        increment_ai_usage(user.user_id, s)
        refreshed = s.get(JobDescription, UUID(job_id))
        latest2 = _latest_result(s, UUID(job_id))
        matched = list(latest2.matched_skills.keys())[:10] if latest2 and latest2.matched_skills else []
        missing = latest2.missing_skills[:10] if latest2 and latest2.missing_skills else []
        return {
            "ats_score": latest2.ats_score if latest2 else 0.0,
            "matched_skills": matched,
            "missing_skills": missing,
            "status": refreshed.status if refreshed else "tailored",
        }


# ── Export job ───────────────────────────────────────────────

@router.get("/{job_id}/export")
async def export_job(job_id: str, format: str = "pdf", user: User = Depends(get_current_user)):
    if format not in ("pdf", "tex", "docx"):
        raise HTTPException(status_code=422, detail="format must be 'pdf', 'tex', or 'docx'")
    job, session = _get_owned_job(job_id, user)
    with session:
        latest = _latest_result(session, job.job_id)
        if not latest or not latest.tailored_resume_content:
            raise HTTPException(status_code=422, detail="No tailored resume found — run Tailor first.")
        tailored_content = latest.tailored_resume_content
        job_title = f"{job.title}_{job.company}".replace(" ", "_")

    def _render():
        from agents.formatter import ResumeFormatterAgent
        agent = ResumeFormatterAgent(user.user_id)
        if format == "pdf":
            return agent.format_pdf(tailored_content, job_title=job.title)
        if format == "tex":
            return agent.format_tex(tailored_content, job_title=job.title)
        return agent.format_docx(tailored_content, job_title=job.title)

    content = await asyncio.to_thread(_render)

    if format == "pdf":
        return Response(
            content=content,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="tailored_{job_title}.pdf"'},
        )
    if format == "tex":
        return Response(
            content=content if isinstance(content, bytes) else content.encode("utf-8"),
            media_type="text/plain",
            headers={"Content-Disposition": f'attachment; filename="tailored_{job_title}.tex"'},
        )
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="tailored_{job_title}.docx"'},
    )
