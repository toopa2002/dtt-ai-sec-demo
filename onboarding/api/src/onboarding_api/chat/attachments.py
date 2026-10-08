"""Screenshots (FR-007, FR-026a). The secret check runs before anyone else can see an image: a held image is never
written to GridFS and is visible only to its uploader, in memory, for at most 10 minutes."""

import asyncio
import base64
import time
from datetime import UTC, datetime

from bson import ObjectId
from fastapi import APIRouter, Depends, UploadFile, status
from fastapi.responses import Response

from ..agent_client import client as agent
from ..auth.deps import CurrentUser, current_user, error
from ..db import db, screenshots
from . import events
from .access import participant_session

router = APIRouter(tags=["attachments"])
MAX_BYTES = 10 * 1024 * 1024
TYPES = {"image/png", "image/jpeg", "image/webp"}
HELD_SECONDS = 600
_pending: dict[str, tuple[bytes, float]] = {}  # attachment id -> (bytes, stored at); uploader-only


def _sniff(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _sweep() -> None:
    now = time.monotonic()
    for key, (_, at) in list(_pending.items()):
        if now - at > HELD_SECONDS:
            _pending.pop(key, None)


async def _check(session: dict, attachment_id: ObjectId, data: bytes, content_type: str, uploader: str) -> None:
    verdict, reason = "held", "The image could not be checked. Please try again."
    payload = {"mode": "secret_check", "session": {"id": str(session["_id"])},
               "images": [{"media_type": content_type, "data_b64": base64.b64encode(data).decode()}]}
    async for ev in agent.invoke(payload, f"onb-{session['_id']}"):
        if ev.get("type") == "secret_check":
            verdict, reason = ev.get("result", "held"), ev.get("reason", "")
    if verdict == "passed":
        gridfs_id = await screenshots().upload_from_stream(f"{attachment_id}", data,
                                                           metadata={"content_type": content_type})
        await db().attachments.update_one({"_id": attachment_id},
                                          {"$set": {"secret_check": "passed", "gridfs_id": gridfs_id}})
        _pending.pop(str(attachment_id), None)
        await events.emit(session["_id"], "attachment.checked",
                          {"attachment_id": str(attachment_id), "secret_check": "passed"})
    else:
        await db().attachments.update_one({"_id": attachment_id}, {"$set": {"secret_check": "held"}})
        await events.emit(session["_id"], "attachment.held", {"attachment_id": str(attachment_id),
                                                              "reason": reason or "It looks like it shows a secret."},
                          visible_to=uploader)


@router.post("/sessions/{session_id}/attachments", status_code=status.HTTP_201_CREATED)
async def upload(session_id: str, file: UploadFile, user: CurrentUser = Depends(current_user)) -> dict:  # noqa: B008
    session, _ = await participant_session(session_id, user)
    data = await file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise error("attachment_too_large", "Screenshots are limited to 10 MB.", status.HTTP_413_CONTENT_TOO_LARGE)
    content_type = _sniff(data)
    if content_type not in TYPES:
        raise error("validation_failed", "Upload a PNG, JPEG or WebP image.", status.HTTP_422_UNPROCESSABLE_CONTENT)
    _sweep()
    doc = {"session_id": session["_id"], "uploader_id": user.id, "gridfs_id": None, "content_type": content_type,
           "secret_check": "pending", "created_at": datetime.now(UTC), "expires_at": None}
    attachment_id = (await db().attachments.insert_one(doc)).inserted_id
    _pending[str(attachment_id)] = (data, time.monotonic())
    asyncio.create_task(_check(session, attachment_id, data, content_type, str(user.id)))
    return {"id": str(attachment_id), "secret_check": "pending", "url": None}


async def _visible(session_id: str, attachment_id: str, user: CurrentUser) -> tuple[dict, dict]:
    session, _ = await participant_session(session_id, user)
    att = await db().attachments.find_one({"_id": ObjectId(attachment_id), "session_id": session["_id"]}) \
        if ObjectId.is_valid(attachment_id) else None
    if not att or (att["secret_check"] != "passed" and att["uploader_id"] != user.id):
        raise error("not_found", "No such screenshot.", status.HTTP_404_NOT_FOUND)
    return session, att


@router.get("/sessions/{session_id}/attachments/{attachment_id}")
async def download(session_id: str, attachment_id: str, user: CurrentUser = Depends(current_user)):  # noqa: B008
    _, att = await _visible(session_id, attachment_id, user)
    if att["secret_check"] == "passed" and att.get("gridfs_id"):
        stream = await screenshots().open_download_stream(att["gridfs_id"])
        return Response(await stream.read(), media_type=att["content_type"], headers={"Cache-Control": "private"})
    _sweep()
    held = _pending.get(attachment_id)
    if not held:
        raise error("not_found", "This screenshot was discarded.", status.HTTP_404_NOT_FOUND)
    return Response(held[0], media_type=att["content_type"], headers={"Cache-Control": "no-store"})


@router.delete("/sessions/{session_id}/attachments/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def dismiss(session_id: str, attachment_id: str, user: CurrentUser = Depends(current_user)) -> None:  # noqa: B008
    _, att = await _visible(session_id, attachment_id, user)
    if att["uploader_id"] != user.id or att["secret_check"] == "passed":
        raise error("forbidden_role", "Only a held screenshot can be dismissed, by its uploader.",
                    status.HTTP_403_FORBIDDEN)
    _pending.pop(attachment_id, None)
    await db().attachments.delete_one({"_id": att["_id"]})
