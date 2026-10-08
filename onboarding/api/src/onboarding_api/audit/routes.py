"""SailPoint action records with who ordered each and their details (FR-020, FR-020a; research R24). The session's
IAM engineer only: the application owner follows results through the plan and relay notes (403)."""

from fastapi import APIRouter, Depends, status

from ..auth.deps import CurrentUser, current_user, error
from ..chat.access import participant_session
from . import actions

router = APIRouter(tags=["audit"])


async def _iam_session(session_id: str, user: CurrentUser) -> dict:
    session, role = await participant_session(session_id, user)
    if role != "iam_engineer":
        raise error("forbidden_role", "SailPoint action details are for the IAM engineer.", status.HTTP_403_FORBIDDEN)
    return session


@router.get("/sessions/{session_id}/actions")
async def list_actions(session_id: str, user: CurrentUser = Depends(current_user)) -> list[dict]:  # noqa: B008
    session = await _iam_session(session_id, user)
    return await actions.list_for(session["_id"])


@router.get("/sessions/{session_id}/actions/{action_id}")
async def get_action(session_id: str, action_id: str, user: CurrentUser = Depends(current_user)) -> dict:  # noqa: B008
    session = await _iam_session(session_id, user)
    action = await actions.get(session["_id"], action_id)
    if not action:
        raise error("not_found", "No such action.", status.HTTP_404_NOT_FOUND)
    return action
