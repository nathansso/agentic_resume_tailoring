import asyncio
import json as _json
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlmodel import Session

from database.db import engine
from database.models import JobDescription, User
from database.user_utils import set_request_user
from web.auth import get_current_user
from web.routers.dependencies import check_ai_quota, increment_ai_usage
import services

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatBody(BaseModel):
    message: str


class ArtifactDecisionBody(BaseModel):
    """One proposal from /artifacts/propose, plus what the user chose to do."""
    action: str  # "accept" | "dismiss"
    proposal: dict


def _get_or_create_agent(request: Request, user: User):
    from agents.chat import ChatAgent
    key = str(user.user_id)
    if key not in request.app.state.chat_agents:
        request.app.state.chat_agents[key] = ChatAgent()
    return request.app.state.chat_agents[key]


def _require_own_job(job_id: str, user: User) -> None:
    """Job chats are keyed by job_id — verify the job belongs to the caller (issue #73)."""
    try:
        jid = UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Job not found")
    with Session(engine) as session:
        job = session.get(JobDescription, jid)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.user_id and str(job.user_id) != str(user.user_id):
        raise HTTPException(status_code=403, detail="Not your job")


@router.get("/{job_id}/history")
def get_history(job_id: str, user: User = Depends(get_current_user)):
    if job_id == "landing":
        return services.load_chat_history(None, user_id=user.user_id)
    _require_own_job(job_id, user)
    return services.load_chat_history(job_id)


@router.post("/{job_id}/send")
async def send_message(
    job_id: str,
    body: ChatBody,
    request: Request,
    user: User = Depends(get_current_user),
    _quota: None = Depends(check_ai_quota),
):
    # Bind the acting user to this request's context (issue #73): the agent and
    # service code below resolve it via get_active_profile(). Set in the async
    # endpoint body — not the sync get_current_user dependency, which FastAPI
    # runs in a threadpool where ContextVar writes don't propagate back.
    set_request_user(user.user_id)
    agent = _get_or_create_agent(request, user)
    jid = None if job_id == "landing" else job_id
    agent.set_active_job(jid)

    async def event_stream():
        try:
            result: str = await asyncio.to_thread(agent.chat, body.message)
            with Session(engine) as s:
                increment_ai_usage(user.user_id, s)
        except Exception as exc:
            result = f"Error: {exc}"
        yield f"data: {_json.dumps({'content': result, 'done': True})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Knowledge artifacts from chat (issue #21) ─────────────────────────────────
#
# Two endpoints, deliberately split so that proposing can never write: /propose
# reads the transcript and returns candidates, /decide is the only thing that
# touches the knowledge graph and only ever on an explicit user action. There is
# no automatic-write path. Proposals are stateless — the client holds the one it
# is acting on and hands it back — so a dismissed proposal leaves no residue.


@router.post("/{job_id}/artifacts/propose")
async def propose_artifacts(
    job_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    _quota: None = Depends(check_ai_quota),
):
    """Chain-of-Note proposals from this chat's recent turns. Writes nothing.

    Each item carries `decision`: 'add' (new), 'supersede' (updates the `target`
    fact already in the graph), or 'no_op' (already known, filtered out here).
    """
    set_request_user(user.user_id)
    if job_id != "landing":
        _require_own_job(job_id, user)

    agent = _get_or_create_agent(request, user)
    jid = None if job_id == "landing" else job_id
    agent.set_active_job(jid)

    history = services.load_chat_history(jid, user_id=user.user_id)
    proposals = await asyncio.to_thread(agent._extract_chat_artifacts, history[-10:])
    with Session(engine) as s:
        increment_ai_usage(user.user_id, s)
    return {"proposals": [p for p in proposals if p.get("decision") != "no_op"]}


@router.post("/{job_id}/artifacts/decide")
async def decide_artifact(
    job_id: str,
    body: ArtifactDecisionBody,
    user: User = Depends(get_current_user),
):
    """Accept or dismiss one proposal. Accepting is the only write path."""
    set_request_user(user.user_id)
    if job_id != "landing":
        _require_own_job(job_id, user)

    action = (body.action or "").strip().lower()
    if action not in ("accept", "dismiss"):
        raise HTTPException(status_code=422, detail="action must be 'accept' or 'dismiss'")
    if action == "dismiss":
        return {"saved": False, "message": "Dismissed — nothing saved."}

    message = await asyncio.to_thread(
        services.apply_artifact_decision,
        user.user_id, body.proposal, f"chat:{job_id}",
    )
    return {"saved": True, "message": message}
