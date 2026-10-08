"""Messages (data-model.md `messages`): stored after masking, queued in arrival order (FR-006a, FR-026).

Every message belongs to one of the session's two threads (FR-006). A participant only ever speaks in their own
thread, which the API sets from the speaker's role: there is no way to post into the other thread. The agent's
messages carry the thread they were posted to; a `relay_note` is the agent's one-line note in one thread about what
it asked or passed on in the other (FR-006c). A `system_note` is the API's line about a reopen or handover (FR-033).

Every participant message gets its agent reply at once (FR-006h, research R21): an agent message with `reply_to`,
`reply_state` (received → working → answered | failed), `ahead` and a system-written `status_text`; the turn later
writes its answer into that same message.
"""

from datetime import UTC, datetime
from typing import Any

from bson import ObjectId

from ..db import db
from ..masking import mask
from ..sessions import repo

MAX_TEXT = 8000
SPEAKERS = ("iam_engineer", "application_owner", "agent")
THREADS = ("iam_engineer", "application_owner")
KINDS = ("message", "relay_note", "system_note")
REPLY_STATES = ("received", "working", "answered", "failed")


class MessageError(ValueError):
    pass


def other_thread(thread: str) -> str:
    return "application_owner" if thread == "iam_engineer" else "iam_engineer"


async def add(session_id: ObjectId, *, speaker: str, text: str, thread: str | None = None, kind: str = "message",
              speaker_user_id: ObjectId | None = None, relay_ref: ObjectId | None = None,
              relayed_from: str | None = None, attachment_ids: list[ObjectId] | None = None,
              turn_id: str | None = None, queued: bool = False, meta: dict[str, Any] | None = None,
              reply_to: ObjectId | None = None, reply_state: str | None = None, ahead: int | None = None,
              status_text: str | None = None) -> dict:
    if speaker not in SPEAKERS:
        raise MessageError("unknown speaker")
    if speaker != "agent":
        if thread is not None and thread != speaker:
            raise MessageError("a participant only ever speaks in their own thread")
        thread = speaker
    if thread not in THREADS:
        raise MessageError("thread must be iam_engineer or application_owner")
    if kind not in KINDS:
        raise MessageError("kind must be message, relay_note or system_note")
    if kind != "message" and speaker != "agent":
        raise MessageError("only the agent writes relay and system notes")
    if reply_state is not None and (speaker != "agent" or reply_state not in REPLY_STATES):
        raise MessageError("reply_state is for agent replies: received, working, answered or failed")
    if relayed_from is not None and relayed_from not in THREADS:
        raise MessageError("relayed_from must be iam_engineer or application_owner")
    text = (text or "").strip()
    if len(text) > MAX_TEXT:
        raise MessageError(f"messages are limited to {MAX_TEXT} characters")
    masked_text, masked = mask(text)
    seq = await repo.next_counter(session_id, "msg_seq")
    doc = {
        "session_id": session_id,
        "seq": seq,
        "thread": thread,
        "kind": kind,
        "speaker": speaker,
        "speaker_user_id": speaker_user_id,
        "relay_ref": relay_ref,
        "relayed_from": relayed_from,
        "text": masked_text,
        "masked": masked,
        "attachment_ids": attachment_ids or [],
        "queue_state": "queued" if queued else None,
        "turn_id": turn_id,
        "meta": meta or {},
        "reply_to": reply_to,
        "reply_state": reply_state,
        "ahead": ahead,
        "status_text": mask(status_text)[0] if status_text else None,
        "created_at": datetime.now(UTC),
        "expires_at": None,
    }
    result = await db().messages.insert_one(doc)
    doc["_id"] = result.inserted_id
    return doc


async def set_queue_state(message_id: ObjectId, state: str, turn_id: str | None = None) -> None:
    update: dict[str, Any] = {"queue_state": state}
    if turn_id:
        update["turn_id"] = turn_id
    await db().messages.update_one({"_id": message_id}, {"$set": update})


async def set_reply(message_id: ObjectId, **fields: Any) -> dict | None:
    """Update only the given reply fields (`reply_state`, `ahead`, `status_text`, `text`), masking text."""
    allowed = {"reply_state", "ahead", "status_text", "text", "turn_id"}
    update = {k: v for k, v in fields.items() if k in allowed}
    if update.get("reply_state") is not None and update["reply_state"] not in REPLY_STATES:
        raise MessageError("unknown reply_state")
    for key in ("text", "status_text"):
        if update.get(key):
            update[key] = mask(update[key][:MAX_TEXT])[0]
    await db().messages.update_one({"_id": message_id}, {"$set": update})
    return await db().messages.find_one({"_id": message_id})


async def reply_for(message_id: ObjectId) -> dict | None:
    return await db().messages.find_one({"reply_to": message_id})


async def next_queued(session_id: ObjectId) -> dict | None:
    return await db().messages.find_one({"session_id": session_id, "queue_state": "queued"}, sort=[("seq", 1)])


async def history(session_id: ObjectId, after_seq: int = 0, limit: int | None = None) -> list[dict]:
    cursor = db().messages.find({"session_id": session_id, "seq": {"$gt": after_seq}}, sort=[("seq", 1)])
    items = [m async for m in cursor]
    return items[-limit:] if limit else items


async def public(m: dict, names: dict[str, str]) -> dict[str, Any]:
    atts = []
    if m.get("attachment_ids"):
        async for a in db().attachments.find({"_id": {"$in": m["attachment_ids"]}}):
            atts.append({"id": str(a["_id"]), "secret_check": a["secret_check"],
                         "url": f"attachments/{a['_id']}" if a["secret_check"] == "passed" else None})
    return {
        "id": str(m["_id"]),
        "seq": m["seq"],
        "thread": m.get("thread") or m["speaker"],
        "kind": m.get("kind", "message"),
        "speaker": m["speaker"],
        "speaker_name": "Agent" if m["speaker"] == "agent" else names.get(str(m.get("speaker_user_id")), ""),
        "speaker_user_id": str(m["speaker_user_id"]) if m.get("speaker_user_id") else None,
        "relay_ref": str(m["relay_ref"]) if m.get("relay_ref") else None,
        "relayed_from": m.get("relayed_from"),
        "text": m["text"],
        "masked": m.get("masked", False),
        "queue_state": m.get("queue_state"),
        "reply_to": str(m["reply_to"]) if m.get("reply_to") else None,
        "reply_state": m.get("reply_state"),
        "ahead": m.get("ahead"),
        "status_text": m.get("status_text"),
        "attachments": atts,
        "application_steps": m.get("meta", {}).get("application_steps", []),
        "created_at": m["created_at"],
    }
