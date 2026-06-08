import asyncio
import tempfile
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, File, UploadFile
from pydantic import BaseModel

from database.models import User
from database.user_utils import ACTIVE_PROFILE_FILE, ART_DIR
from web.auth import get_current_user
from tui import services

router = APIRouter(prefix="/api/ingest", tags=["ingest"])


class GithubBody(BaseModel):
    username: str


class GithubRepoBody(BaseModel):
    repo_ref: str


def _write_active_profile(user_id: UUID) -> None:
    ART_DIR.mkdir(parents=True, exist_ok=True)
    ACTIVE_PROFILE_FILE.write_text(str(user_id))


@router.post("/resume")
async def ingest_resume(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
):
    _write_active_profile(user.user_id)
    suffix = Path(file.filename or "resume").suffix or ".pdf"
    contents = await file.read()
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(contents)
        tmp_path = tmp.name
    try:
        result = await asyncio.to_thread(services.ingest_resume_file, tmp_path)
    finally:
        Path(tmp_path).unlink(missing_ok=True)
    return {"result": result}


@router.post("/github")
async def ingest_github(
    body: GithubBody,
    user: User = Depends(get_current_user),
):
    _write_active_profile(user.user_id)
    token = user.github_access_token or None
    result = await asyncio.to_thread(services.ingest_github, body.username.strip(), token=token)
    return {"result": result}


@router.post("/github/repo")
async def ingest_github_repo(
    body: GithubRepoBody,
    user: User = Depends(get_current_user),
):
    _write_active_profile(user.user_id)
    token = user.github_access_token or None
    result = await asyncio.to_thread(services.ingest_github_repo, body.repo_ref.strip(), token=token)
    return {"result": result}
