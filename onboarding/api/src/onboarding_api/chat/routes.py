"""POST/GET /sessions/{id}/messages (FR-006, FR-006a). Text is masked before it is stored or broadcast (FR-026)."""

from bson import ObjectId
from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field

from ..audit import audit
from ..auth.deps import CurrentUser, current_user, error
from ..catalog import catalog
from ..db import db
from ..masking import contains_entra_secret
from ..sessions import repo
from . import events, messages, suggestions, turns
from .access import participant_session

router = APIRouter(tags=["chat"])
SECRET_EXPOSED_NOTE = ("A client secret was masked before it was saved, shown or sent to the agent. Treat it as exposed: "
                       "delete it in Entra and create a new one.")


class MessageIn(BaseModel):
    text: str = Field(max_length=messages.MAX_TEXT)
    attachment_ids: list[str] = Field(default_factory=list, max_length=3)


async def names_for(session: dict) -> dict[str, str]:
    """Display names of everyone who has held a place in the session, so messages keep their author's name after a
    handover (FR-033)."""
    ids = [i for i in (session["iam_engineer_id"], session.get("application_owner_id")) if i]
    ids += [h["from_user_id"] for h in session.get("handovers") or [] if h.get("from_user_id")]
    return {str(u["_id"]): u["display_name"] async for u in db().users.find({"_id": {"$in": ids}})}


@router.get("/sessions/{session_id}/messages")
async def list_messages(session_id: str, after_seq: int = Query(0, ge=0),
                        user: CurrentUser = Depends(current_user)) -> list[dict]:  # noqa: B008
    session, _ = await participant_session(session_id, user)
    names = await names_for(session)
    return [await messages.public(m, names) for m in await messages.history(session["_id"], after_seq)]


@router.post("/sessions/{session_id}/messages", status_code=status.HTTP_202_ACCEPTED)
async def send_message(session_id: str, body: MessageIn,
                       user: CurrentUser = Depends(current_user)) -> dict:  # noqa: B008
    session, role = await participant_session(session_id, user)
    if session["status"] != "open":
        raise error("session_finished", "This session is finished.", status.HTTP_409_CONFLICT)
    attachment_ids = []
    for raw in body.attachment_ids:
        att = await db().attachments.find_one({"_id": ObjectId(raw), "session_id": session["_id"],
                                               "uploader_id": user.id, "secret_check": "passed"})
        if not att:
            raise error("validation_failed", "A screenshot isn't ready to send yet, or was held.",
                        status.HTTP_422_UNPROCESSABLE_CONTENT)
        attachment_ids.append(att["_id"])
    if not body.text.strip() and not attachment_ids:
        raise error("validation_failed", "Write a message or attach a screenshot.", status.HTTP_422_UNPROCESSABLE_CONTENT)
    # Spec 002 (FR-123, research R17): an Entra client secret pasted into the chat is masked like any secret, and the
    # pasted one counts as exposed: a system note says so at once, without a model call.
    exposed = bool((catalog.get(session["connector_type"]) or {}).get("secret")) and contains_entra_secret(body.text)
    try:
        message = await messages.add(session["_id"], speaker=role, speaker_user_id=user.id, text=body.text,
                                     attachment_ids=attachment_ids, queued=True,
                                     meta={"secret_exposed": True} if exposed else None)
    except messages.MessageError as exc:
        raise error("validation_failed", str(exc), status.HTTP_422_UNPROCESSABLE_CONTENT) from None
    names = await names_for(session)
    public = await messages.public(message, names)
    note = None
    if exposed:  # right after the masked message, before the agent's reply
        note = await messages.add(session["_id"], speaker="agent", thread=role, kind="system_note", tone="danger",
                                  code="secret_exposed", text=SECRET_EXPOSED_NOTE)
        await audit.record("secret_exposed_in_chat", user.id, target=str(session["_id"]))
        if role == "application_owner":
            await repo.set_hint(session["_id"], "application_owner", "waiting_for_secret")
    reply = await turns.create_reply(session["_id"], message)  # FR-006h: the reply's status at once
    await events.emit(session["_id"], "message.created", public)
    if note:
        await events.emit(session["_id"], "message.created", await messages.public(note, names))
    await events.emit(session["_id"], "message.created", await messages.public(reply, names))
    await events.emit(session["_id"], "message.queue", {"message_id": public["id"], "queue_state": "queued"})
    turns.kick(session["_id"])
    return public


@router.get("/sessions/{session_id}/suggestions")
async def list_suggestions(session_id: str, user: CurrentUser = Depends(current_user)) -> dict:  # noqa: B008
    """Suggested messages for the caller's own thread (FR-006e): what the last turn stored, or the defaults for the
    current state when no turn has run yet. Never the other participant's list."""
    session, role = await participant_session(session_id, user)
    stored = (session.get("suggestions") or {}).get(role) or []
    if not stored:
        stored = suggestions.for_thread(session, role, [], catalog.suggestion_defaults(session["connector_type"]))
    return {"thread": role, "for_event_id": (session.get("suggestions") or {}).get("for_event_id", 0),
            "items": [{"text": i["text"], "kind": i["kind"]} for i in stored]}

