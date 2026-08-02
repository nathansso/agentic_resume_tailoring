"""Standing user preferences (issue #129).

The inspect / correct / retract surface, plus the propose-decide pair that
captures preferences from chat. Same two non-negotiables the JD profile carries
in #121, for the same reason — this is LLM-extracted structure whose errors have
no visible symptom, and this one *suppresses resume content* when wrong:

  - **Every extraction is correctable.** A passing remark read as an absolute
    rule silently deletes an item from every future resume until someone can fix
    it, so `PATCH` exists before any of this is useful.
  - **Nothing is ever deleted.** `DELETE` retracts — it flips `status` and keeps
    the row. Negation must not expire (#133), and the transition history is what
    #51 Phase 2 learns preference weights from.

**Proposing never writes.** `/propose` reads the transcript and returns
candidates; `/decide` is the only path into the table, and only on an explicit
user action. The split is #21's, and it matters more here: preferences are
*inferred*, so a silent write path would let the assistant record a constraint
the user never agreed to and then honor it invisibly on every later run.

Preferences are user-scoped rather than job-scoped, so every route resolves the
owner from the session and the two id-bearing routes re-check ownership in
`services` before touching a row (issue #73).
"""
import asyncio
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from database.db import engine
from database.models import User
from database.user_utils import set_request_user
from web.auth import get_current_user
from web.routers.dependencies import check_ai_quota, increment_ai_usage
import services

router = APIRouter(prefix="/api/preferences", tags=["preferences"])


class ProposeBody(BaseModel):
    """Which conversation to read. `job_id` absent means the landing chat."""
    job_id: Optional[str] = None


class PreferenceDecisionBody(BaseModel):
    """One proposal from /propose, plus what the user chose to do."""
    action: str  # "accept" | "dismiss"
    proposal: dict


class PreferenceEditBody(BaseModel):
    text: Optional[str] = None
    polarity: Optional[str] = None
    strength: Optional[int] = None
    scope_type: Optional[str] = None
    scope_value: Optional[str] = None
    target_term: Optional[str] = None


def _parse_id(preference_id: str) -> UUID:
    try:
        return UUID(preference_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=404, detail="Preference not found")


@router.get("/")
async def list_preferences(
    include_inactive: bool = False,
    user: User = Depends(get_current_user),
):
    """This user's preferences. `include_inactive` also returns superseded and
    retracted rows, which is what makes the history inspectable rather than just
    the current state."""
    set_request_user(user.user_id)
    return {
        "preferences": services.load_preferences(
            user.user_id, include_inactive=include_inactive),
    }


@router.post("/propose")
async def propose_preferences(
    body: ProposeBody,
    user: User = Depends(get_current_user),
    _quota: None = Depends(check_ai_quota),
):
    """Preference candidates from a conversation. Writes nothing.

    Each carries `decision`: 'add' (new), 'supersede' (reverses the preference
    in `supersedes_id`), or 'no_op' (already held). `no_op` is filtered here —
    there is nothing for the user to decide about a preference they already
    hold, and showing it invites them to accept a duplicate.
    """
    set_request_user(user.user_id)
    job_id = (body.job_id or "").strip() or None
    # No separate ownership check: load_chat_history is user-scoped, so another
    # user's job_id returns this caller's own (empty) history rather than that
    # user's transcript (issue #73).
    history = services.load_chat_history(job_id, user_id=user.user_id)
    proposals = await asyncio.to_thread(
        services.propose_preferences, user.user_id, job_id, history)
    with Session(engine) as session:
        increment_ai_usage(user.user_id, session)
    return {"proposals": [p for p in proposals if p.get("decision") != "no_op"]}


@router.post("/decide")
async def decide_preference(
    body: PreferenceDecisionBody,
    user: User = Depends(get_current_user),
):
    """Accept or dismiss one proposal. Accepting is the only write path."""
    set_request_user(user.user_id)
    action = (body.action or "").strip().lower()
    if action not in ("accept", "dismiss"):
        raise HTTPException(status_code=422, detail="action must be 'accept' or 'dismiss'")
    if action == "dismiss":
        return {"saved": False, "message": "Dismissed — nothing saved."}
    message = await asyncio.to_thread(
        services.apply_preference_decision, user.user_id, body.proposal)
    return {"saved": True, "message": message}


@router.patch("/{preference_id}")
async def edit_preference(
    preference_id: str,
    body: PreferenceEditBody,
    user: User = Depends(get_current_user),
):
    """Correct an extraction. Stamps `edited` so no later extraction overwrites it."""
    set_request_user(user.user_id)
    edits = {k: v for k, v in body.model_dump().items() if v is not None}
    updated = services.update_preference(user.user_id, _parse_id(preference_id), edits)
    if updated is None:
        raise HTTPException(status_code=404, detail="Preference not found")
    return updated


@router.delete("/{preference_id}")
async def retract_preference(
    preference_id: str,
    user: User = Depends(get_current_user),
):
    """Withdraw a preference. Retracts — the row is kept, never deleted."""
    set_request_user(user.user_id)
    retracted = services.retract_preference(user.user_id, _parse_id(preference_id))
    if retracted is None:
        raise HTTPException(status_code=404, detail="Preference not found")
    return retracted
