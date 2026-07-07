import asyncio
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlmodel import Session

from database.db import get_session
from database.models import User
from database.user_utils import set_request_user
from web.auth import get_current_user
from web.routers.dependencies import check_linkedin_quota, increment_linkedin_usage
import services

router = APIRouter(prefix="/api/ingest", tags=["ingest"])


class GithubBody(BaseModel):
    # Optional: when omitted, the connected OAuth account's username is used.
    username: str | None = None


class GithubRepoBody(BaseModel):
    repo_ref: str


class LinkedInBody(BaseModel):
    url: str


@router.post("/resume")
async def ingest_resume(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
):
    set_request_user(user.user_id)  # bind acting user for downstream service code (issue #73)
    suffix = Path(file.filename or "resume").suffix or ".pdf"
    contents = await file.read()
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(contents)
        tmp_path = tmp.name
    try:
        result = await asyncio.to_thread(
            services.ingest_resume_file, tmp_path, file.filename or None
        )
    finally:
        Path(tmp_path).unlink(missing_ok=True)
    return {"result": result}


@router.post("/github")
async def ingest_github(
    body: GithubBody,
    user: User = Depends(get_current_user),
):
    set_request_user(user.user_id)  # bind acting user for downstream service code (issue #73)
    username = (body.username or "").strip() or (user.github_username or "").strip()
    if not username:
        raise HTTPException(
            status_code=400,
            detail="No GitHub username available — connect GitHub or enter a username.",
        )
    token = user.github_access_token or None
    result = await asyncio.to_thread(services.ingest_github, username, token=token)
    return {"result": result}


@router.post("/github/repo")
async def ingest_github_repo(
    body: GithubRepoBody,
    user: User = Depends(get_current_user),
):
    set_request_user(user.user_id)  # bind acting user for downstream service code (issue #73)
    token = user.github_access_token or None
    result = await asyncio.to_thread(services.ingest_github_repo, body.repo_ref.strip(), token=token)
    return {"result": result}


@router.post("/linkedin")
async def ingest_linkedin(
    body: LinkedInBody,
    user: User = Depends(get_current_user),
    _: None = Depends(check_linkedin_quota),
    session: Session = Depends(get_session),
):
    """Manually trigger a Bright Data LinkedIn scrape (blocking, rate-limited)."""
    set_request_user(user.user_id)  # bind acting user for downstream service code (issue #73)
    # Count the attempt up front: a Bright Data call is about to be made, so a
    # retry on a bad URL still consumes quota and can't hammer the paid API.
    increment_linkedin_usage(user.user_id, session)
    result = await asyncio.to_thread(
        services.ingest_linkedin, body.url.strip(), user.user_id
    )
    return {"result": result}


@router.post("/linkedin/pdf")
async def ingest_linkedin_pdf(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
):
    """Fallback: ingest a LinkedIn PDF export."""
    set_request_user(user.user_id)  # bind acting user for downstream service code (issue #73)
    suffix = Path(file.filename or "linkedin").suffix or ".pdf"
    contents = await file.read()
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(contents)
        tmp_path = tmp.name
    try:
        result = await asyncio.to_thread(
            services.ingest_linkedin_pdf, tmp_path, file.filename or None
        )
    finally:
        Path(tmp_path).unlink(missing_ok=True)
    return {"result": result}
