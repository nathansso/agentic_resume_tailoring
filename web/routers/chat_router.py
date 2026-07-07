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


def _get_or_create_agent(request: Request, user: User):
    from agents.chat import ChatAgent
    key = str(user.user_id)
    if key not in request.app.state.chat_agents:
        request.app.state.chat_agents[key] = ChatAgent()
    return request.app.state.chat_agents[key]


@router.get("/{job_id}/history")
def get_history(job_id: str, user: User = Depends(get_current_user)):
    if job_id == "landing":
        return services.load_chat_history(None, user_id=user.user_id)
    # Job chats are keyed by job_id — verify the job belongs to the caller
    # before returning its history (issue #73).
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
