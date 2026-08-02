import asyncio
from datetime import datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sqlmodel import Session, select

from agents.skill_selection import skill_names
from database.db import engine
from database.models import JobDescription, UserJobResult, User
from web.auth import get_current_user
from web.routers.dependencies import (
    check_ai_quota,
    check_compile_quota,
    increment_ai_usage,
    increment_compile_usage,
)

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


class CreateJobBody(BaseModel):
    title: str
    company: str
    description: str = ""


class DescriptionBody(BaseModel):
    description: str


class TailorBody(BaseModel):
    revision_notes: str = ""


class TexBody(BaseModel):
    tex: str


class BulletGroupBody(BaseModel):
    """One item's bullets in the order the user arranged them (issue #118)."""
    section: str
    item: str
    order: list[str]


class LayoutBody(BaseModel):
    """Explicit arrangement override. Every field is optional, so a user can pin
    section order without pinning skills, or reorder one item's bullets without
    touching anything else."""
    section_order: list[str] | None = None
    skills: list[str] | None = None
    bullets: list[BulletGroupBody] | None = None


# At most two concurrent LaTeX compiles — pdflatex spikes memory (issue #71
# preview endpoint). The cap was chosen for a 512 MB VM; the current host has
# 8 GB, so this is tunable on measured numbers rather than a hard ceiling.
_compile_semaphore = asyncio.Semaphore(2)

_MAX_TEX_BYTES = 200_000


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
        matched = skill_names(result.matched_skills)[:10]
        missing = result.missing_skills[:10] if result.missing_skills else []
    else:
        matched, missing = [], []
    base["matched_skills"] = matched
    base["missing_skills"] = missing
    base["explainability"] = (result.matched_skills or {}).get("_explainability") if result else None
    base["score_breakdown"] = result.score_breakdown if result else {}
    base["tailored_score_breakdown"] = result.tailored_score_breakdown if result else {}
    from services import job_tailor_limit
    base["retailor_count"] = job.retailor_count or 0
    base["retailor_limit"] = job_tailor_limit()
    base["has_manual_edits"] = bool(result.edited_tex) if result else False
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

# The SPA catch-all route in web/app.py swallows FastAPI's automatic
# slash-redirect for /api paths, so collection endpoints answer both forms.
@router.get("/")
@router.get("", include_in_schema=False)
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
@router.post("", include_in_schema=False)
def create_job(body: CreateJobBody, user: User = Depends(get_current_user)):
    if not body.title.strip() or not body.company.strip():
        raise HTTPException(status_code=422, detail="Title and company are required")
    with Session(engine) as session:
        job = JobDescription(
            title=body.title.strip(),
            company=body.company.strip(),
            description=body.description.strip(),
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
    import services
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


# ── JD profile (issue #121) ──────────────────────────────────
# The inspection and correction surface. The profile is LLM-extracted, so a
# "preferred" mis-parsed as "required" biases every downstream decision for that
# job with no visible symptom — the same failure mode as #69/#96 on the
# ingestion side. Extraction does not ship without a path to see and fix it.


class RequirementEdit(BaseModel):
    ordinal: int
    text: str | None = None
    type: str | None = None
    criticality: int | None = None
    terms: list[str] | None = None
    source_section: str | None = None
    confidence: float | None = None


class ProfileEditBody(BaseModel):
    requirements: list[RequirementEdit] = []


@router.get("/{job_id}/profile")
def get_jd_profile(job_id: str, user: User = Depends(get_current_user)):
    """This job's extracted JD profile. 404 when it has none."""
    job, session = _get_owned_job(job_id, user)
    with session:
        jid = job.job_id
    import services
    profile = services.load_jd_profile(jid)
    if profile is None:
        raise HTTPException(status_code=404, detail="No JD profile for this job yet.")
    return profile


@router.put("/{job_id}/profile")
def edit_jd_profile(
    job_id: str, body: ProfileEditBody, user: User = Depends(get_current_user),
):
    """Correct extracted requirements. Touched ones are marked `edited`.

    The mark is what makes a later re-extraction preserve the correction rather
    than silently overwrite it.
    """
    job, session = _get_owned_job(job_id, user)
    with session:
        jid = job.job_id
    import services
    edits = [e.model_dump(exclude_none=True) for e in body.requirements]
    profile = services.update_jd_profile_requirements(jid, edits)
    if profile is None:
        raise HTTPException(status_code=404, detail="No JD profile for this job yet.")
    return profile


@router.post("/{job_id}/profile/reextract")
async def reextract_jd_profile(
    job_id: str,
    user: User = Depends(get_current_user),
    _quota: None = Depends(check_ai_quota),
):
    """Re-run extraction for this job. Explicit and versioned, never automatic.

    Hand-edited requirements are carried through the merge, so re-extracting
    after a JD text change does not cost the user their corrections.
    """
    job, session = _get_owned_job(job_id, user)
    with session:
        jid, uid = job.job_id, job.user_id
    import services
    profile_id = await asyncio.to_thread(
        services.rebuild_jd_profile, jid, uid, True)
    if profile_id is None:
        raise HTTPException(status_code=422, detail="Could not extract a profile for this job.")
    with Session(engine) as s:
        increment_ai_usage(user.user_id, s)
    return services.load_jd_profile(jid)


# ── Tailor job ───────────────────────────────────────────────

@router.post("/{job_id}/tailor")
async def tailor_job(
    job_id: str,
    body: TailorBody | None = None,
    user: User = Depends(get_current_user),
    _quota: None = Depends(check_ai_quota),
):
    from services import job_tailor_limit

    revision_notes = (body.revision_notes if body else "").strip()
    job, session = _get_owned_job(job_id, user)
    with session:
        if job.status not in ("analyzed", "tailored", "exported"):
            raise HTTPException(status_code=422, detail="Analyze the job description before tailoring.")

        limit = job_tailor_limit()
        if (job.retailor_count or 0) >= limit:
            # 409, not 429: the per-job budget is lifetime and never resets
            raise HTTPException(
                status_code=409,
                detail=f"Re-tailor limit reached ({limit}/{limit}) for this job.",
            )

        latest = _latest_result(session, job.job_id)
        if not latest:
            raise HTTPException(status_code=422, detail="No analysis result found — run Analyze first.")
        result_id = latest.result_id

    def _run():
        from agents.tailor import ResumeTailorAgent
        from services import get_resume_style
        from sqlmodel import Session as S
        resume_text = ""
        with S(engine) as s:
            u = s.get(User, user.user_id)
            resume_text = (u.resume_markdown or "") if u else ""
        ResumeTailorAgent().tailor(
            user.user_id, UUID(job_id), result_id, resume_text,
            revision_notes=revision_notes,
        )
        with S(engine) as s:
            j = s.get(JobDescription, UUID(job_id))
            if j:
                j.status = "tailored"
                j.retailor_count = (j.retailor_count or 0) + 1
                j.updated_at = datetime.utcnow()
                s.add(j)
                s.commit()

    await asyncio.to_thread(_run)
    with Session(engine) as s:
        increment_ai_usage(user.user_id, s)
        refreshed = s.get(JobDescription, UUID(job_id))
        latest2 = _latest_result(s, UUID(job_id))
        matched = skill_names(latest2.matched_skills)[:10] if latest2 else []
        missing = latest2.missing_skills[:10] if latest2 and latest2.missing_skills else []
        from services import job_tailor_limit as _limit
        return {
            "ats_score": latest2.ats_score if latest2 else 0.0,
            "matched_skills": matched,
            "missing_skills": missing,
            "status": refreshed.status if refreshed else "tailored",
            "retailor_count": (refreshed.retailor_count or 0) if refreshed else 0,
            "retailor_limit": _limit(),
        }


# ── Resume .tex editing (issue #71) ──────────────────────────

@router.get("/{job_id}/tex")
async def get_tex(job_id: str, user: User = Depends(get_current_user)):
    """Current editable .tex: the saved manual edit, or freshly generated source."""
    job, session = _get_owned_job(job_id, user)
    with session:
        latest = _latest_result(session, job.job_id)
        if not latest or not latest.tailored_resume_content:
            raise HTTPException(status_code=422, detail="No tailored resume found — run Tailor first.")
        if latest.edited_tex:
            return {
                "tex": latest.edited_tex,
                "source": "edited",
                "updated_at": latest.edited_tex_updated_at.isoformat() if latest.edited_tex_updated_at else None,
            }
        tailored_content = latest.tailored_resume_content

    def _generate():
        from agents.formatter import ResumeFormatterAgent
        agent = ResumeFormatterAgent(user.user_id)
        return agent.format_tex(
            tailored_content, section_order=tailored_content.get("_section_order")
        )

    tex = await asyncio.to_thread(_generate)
    return {"tex": tex, "source": "generated", "updated_at": None}


@router.put("/{job_id}/tex")
async def save_tex(job_id: str, body: TexBody, user: User = Depends(get_current_user)):
    """Persist manually edited .tex; exports (tex/pdf) serve it until re-tailor."""
    tex = body.tex
    if not tex.strip():
        raise HTTPException(status_code=422, detail="Empty .tex — use Discard to reset instead.")
    if len(tex.encode("utf-8")) > _MAX_TEX_BYTES:
        raise HTTPException(status_code=422, detail="Resume .tex too large (200KB max).")
    job, session = _get_owned_job(job_id, user)
    with session:
        latest = _latest_result(session, job.job_id)
        if not latest:
            raise HTTPException(status_code=422, detail="No tailoring result to attach edits to — run Tailor first.")
        latest.edited_tex = tex
        latest.edited_tex_updated_at = datetime.utcnow()
        latest.updated_at = datetime.utcnow()
        session.add(latest)
        session.commit()
        return {"saved": True, "updated_at": latest.edited_tex_updated_at.isoformat()}


@router.delete("/{job_id}/tex")
async def discard_tex(job_id: str, user: User = Depends(get_current_user)):
    """Drop manual edits so the editor reseeds from the AI-tailored content."""
    job, session = _get_owned_job(job_id, user)
    with session:
        latest = _latest_result(session, job.job_id)
        if latest and latest.edited_tex:
            latest.edited_tex = None
            latest.edited_tex_updated_at = None
            latest.updated_at = datetime.utcnow()
            session.add(latest)
            session.commit()
        return {"discarded": True}


# ── Explicit arrangement overrides (issue #118) ──────────────

def _known_layout_targets(content: dict) -> tuple[set, set, dict]:
    """Sections, skill names, and item->bullets this content actually has."""
    from agents.tailor import ResumeTailorAgent

    sections = set(ResumeTailorAgent._expected_sections(content or {}))
    skills = {
        (s.get("name") or "").strip().lower()
        for s in (content or {}).get("skills_ranked") or []
        if isinstance(s, dict) and s.get("name")
    }
    items = {
        "experience": {
            (e.get("title") or "").strip().lower()
            for e in (content or {}).get("experiences") or [] if isinstance(e, dict)
        },
        "projects": {
            (p.get("name") or "").strip().lower()
            for p in (content or {}).get("projects") or [] if isinstance(p, dict)
        },
    }
    return sections, skills, items


@router.get("/{job_id}/layout")
def get_layout(job_id: str, user: User = Depends(get_current_user)):
    """This job's explicit arrangement override, or nulls when the ranker decides.

    The frontend reads this to know whether to show the "using your custom
    order — reset to recommended" affordance.
    """
    job, session = _get_owned_job(job_id, user)
    with session:
        latest = _latest_result(session, job.job_id)
        overrides = (latest.layout_overrides if latest else None) or {}
        return {
            "section_order": overrides.get("section_order"),
            "skills": overrides.get("skills"),
            "bullets": overrides.get("bullets"),
            "active": bool(overrides),
        }


@router.put("/{job_id}/layout")
def save_layout(job_id: str, body: LayoutBody, user: User = Depends(get_current_user)):
    """Persist an arrangement the user chose. Outranks the ranker from here on.

    Entries are validated against the current tailored content, so a client bug
    fails loudly instead of writing an override that quietly does nothing. The
    *reconciler* is deliberately more forgiving at render time — content
    legitimately drifts under a stored override, and dropping a stale entry then
    is correct where rejecting the write now is not.
    """
    job, session = _get_owned_job(job_id, user)
    with session:
        latest = _latest_result(session, job.job_id)
        if not latest or not latest.tailored_resume_content:
            raise HTTPException(
                status_code=422,
                detail="No tailored resume to arrange — run Tailor first.")
        content = latest.tailored_resume_content
        sections, skills, items = _known_layout_targets(content)

        overrides: dict = {}
        if body.section_order is not None:
            unknown = [s for s in body.section_order if s not in sections]
            if unknown:
                raise HTTPException(
                    status_code=422,
                    detail=f"Unknown section(s): {', '.join(sorted(unknown))}")
            overrides["section_order"] = body.section_order
        if body.skills is not None:
            unknown = [s for s in body.skills if s.strip().lower() not in skills]
            if unknown:
                raise HTTPException(
                    status_code=422,
                    detail=f"Unknown skill(s): {', '.join(sorted(unknown))}")
            overrides["skills"] = body.skills
        if body.bullets is not None:
            groups = []
            for group in body.bullets:
                if group.section not in items:
                    raise HTTPException(
                        status_code=422,
                        detail=f"Section '{group.section}' has no reorderable bullets.")
                if group.item.strip().lower() not in items[group.section]:
                    raise HTTPException(
                        status_code=422,
                        detail=f"Unknown {group.section} item: {group.item}")
                groups.append(group.model_dump())
            overrides["bullets"] = groups

        # An empty body is a reset, not an override that pins nothing.
        latest.layout_overrides = overrides or None
        latest.updated_at = datetime.utcnow()
        session.add(latest)
        session.commit()
        return {"saved": True, "active": bool(overrides)}


@router.delete("/{job_id}/layout")
def clear_layout(job_id: str, user: User = Depends(get_current_user)):
    """Hand arrangement back to the ranker.

    Without this the override is a one-way door: a user who reorders into a
    worse arrangement has no way back to the recommended one.
    """
    job, session = _get_owned_job(job_id, user)
    with session:
        latest = _latest_result(session, job.job_id)
        if latest and latest.layout_overrides is not None:
            latest.layout_overrides = None
            latest.updated_at = datetime.utcnow()
            session.add(latest)
            session.commit()
        return {"cleared": True}


@router.post("/{job_id}/preview")
async def preview_tex(
    job_id: str,
    body: TexBody,
    user: User = Depends(get_current_user),
    _quota: None = Depends(check_compile_quota),
):
    """Compile posted .tex (the editor's current buffer) to a preview PDF."""
    if not body.tex.strip():
        raise HTTPException(status_code=422, detail="Nothing to compile.")
    if len(body.tex.encode("utf-8")) > _MAX_TEX_BYTES:
        raise HTTPException(status_code=422, detail="Resume .tex too large (200KB max).")
    _get_owned_job(job_id, user)[1].close()

    def _compile():
        from agents.formatter import _compile_tex_to_pdf
        return _compile_tex_to_pdf(body.tex)

    async with _compile_semaphore:
        try:
            pdf = await asyncio.to_thread(_compile)
        except RuntimeError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    with Session(engine) as s:
        increment_compile_usage(user.user_id, s)
    return Response(content=pdf, media_type="application/pdf")


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
        edited_tex = latest.edited_tex
        job_title = f"{job.title}_{job.company}".replace(" ", "_")

    def _render():
        from agents.formatter import ResumeFormatterAgent, _compile_tex_to_pdf
        # Manual .tex edits win for tex/pdf (issue #71). No one-page auto-fit
        # here — trimming works on the JSON, not raw source; the editor preview
        # shows any overflow. DOCX has no .tex representation and stays
        # generated from the tailored JSON.
        if edited_tex and format == "tex":
            return edited_tex
        if edited_tex and format == "pdf":
            return _compile_tex_to_pdf(edited_tex)
        agent = ResumeFormatterAgent(user.user_id)
        section_order = tailored_content.get("_section_order")
        if format == "pdf":
            return agent.format_pdf(tailored_content, job_title=job.title, section_order=section_order)
        if format == "tex":
            return agent.format_tex(tailored_content, job_title=job.title, section_order=section_order)
        return agent.format_docx(tailored_content, job_title=job.title, section_order=section_order)

    try:
        content = await asyncio.to_thread(_render)
    except RuntimeError as exc:
        # Edited .tex that no longer compiles — surface the log tail.
        raise HTTPException(status_code=422, detail=f"LaTeX compile failed: {exc}")

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
