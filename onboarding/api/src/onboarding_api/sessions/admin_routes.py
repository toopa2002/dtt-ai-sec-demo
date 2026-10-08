"""/admin/sessions — list every session, reopen a finished one, hand a place over (FR-002, FR-032, FR-033; US8)."""

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, status
from pydantic import BaseModel

from ..auth.deps import CurrentUser, error, require_admin
from . import admin

router = APIRouter(prefix="/admin/sessions", tags=["admin"])
STATUS = {"not_found": status.HTTP_404_NOT_FOUND, "not_finished": status.HTTP_409_CONFLICT,
          "validation_failed": status.HTTP_422_UNPROCESSABLE_CONTENT}


class HandoverIn(BaseModel):
    place: str
    user_id: str


def _oid(value: str) -> ObjectId:
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        raise error("not_found", "No such session or user.", status.HTTP_404_NOT_FOUND) from None


@router.get("")
async def list_sessions(_: CurrentUser = Depends(require_admin)) -> list[dict]:  # noqa: B008
    return await admin.list_all()


@router.post("/{session_id}/reopen")
async def reopen(session_id: str, user: CurrentUser = Depends(require_admin)) -> dict:  # noqa: B008
    try:
        await admin.reopen(_oid(session_id), user.id)
    except admin.AdminSessionError as exc:
        raise error(exc.code, str(exc), STATUS[exc.code]) from None
    return {"reopened": True}


@router.post("/{session_id}/handover")
async def handover(session_id: str, body: HandoverIn, user: CurrentUser = Depends(require_admin)) -> dict:  # noqa: B008
    try:
        applied = await admin.request_handover(_oid(session_id), body.place, _oid(body.user_id), user.id)
    except admin.AdminSessionError as exc:
        raise error(exc.code, str(exc), STATUS[exc.code]) from None
    return {"applied": applied}
