"""GET /sessions/{id}/actions — SailPoint action records with who ordered each (FR-020)."""

from fastapi import APIRouter, Depends

from ..auth.deps import CurrentUser, current_user
from ..chat.access import participant_session
from . import actions

router = APIRouter(tags=["audit"])


@router.get("/sessions/{session_id}/actions")
async def list_actions(session_id: str, user: CurrentUser = Depends(current_user)) -> list[dict]:  # noqa: B008
    session, _ = await participant_session(session_id, user)
    return await actions.list_for(session["_id"])
