import asyncio
import json as _json
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlmodel import Session

from database.db import engine
from database.models import User
from database.user_utils import ACTIVE_PROFILE_FILE, ART_DIR
from web.auth import get_current_user
from web.routers.dependencies import check_ai_quota, increment_ai_usage
import services

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatBody(BaseModel):
    message: str


def _write_active_profile(user_id: UUID) -> None:
    """Point get_active_profile() at this web user before calling agent/service functions."""
    ART_DIR.mkdir(parents=True, exist_ok=True)
    ACTIVE_PROFILE_FILE.write_text(str(user_id))


def _get_or_create_agent(request: Request, user: User):
    from agents.chat import ChatAgent
    key = str(user.user_id)
    if key not in request.app.state.chat_agents:
        request.app.state.chat_agents[key] = ChatAgent()
    return request.app.state.chat_agents[key]


@router.get("/{job_id}/history")
def get_history(job_id: str, user: User = Depends(get_current_user)):
    jid = None if job_id == "landing" else job_id
    return services.load_chat_history(jid)


@router.post("/{job_id}/send")
async def send_message(
    job_id: str,
    body: ChatBody,
    request: Request,
    user: User = Depends(get_current_user),
    _quota: None = Depends(check_ai_quota),
):
    _write_active_profile(user.user_id)
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
