"""Admin reopen and handover of sessions (FR-031-FR-033, US8, SC-016; research R23).

Reopen reverses finish: the session is open again and its history is no longer due for deletion. A handover gives the
IAM engineer's or the application owner's place to another active user with that role. Both go through the session's
one participant check (`repo.participant_role`), so the previous holder loses every path at once; the live stream
sends them `access.revoked` and closes. A handover asked for while a turn holds `turn_lock` is stored as
`pending_handover` and applied by the turn worker when the turn ends, so no answer is cut off or shown to the wrong
person. Every reopen and handover posts a system note in both threads and is audited with the admin.
"""

from datetime import UTC, datetime
from typing import Any

from bson import ObjectId

from ..audit import audit
from ..db import db
from . import plan as plans
from . import repo

PLACES = ("iam_engineer", "application_owner")
PLACE_LABEL = {"iam_engineer": "IAM engineer", "application_owner": "application owner"}


class AdminSessionError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


async def _name(user_id: ObjectId | None) -> str:
    user = await db().users.find_one({"_id": user_id}, projection={"display_name": 1, "username": 1}) if user_id else None
    return (user or {}).get("display_name") or (user or {}).get("username") or "nobody"


async def _system_note(session_id: ObjectId, text: str) -> None:
    from ..chat import events, messages

    for thread in PLACES:
        note = await messages.add(session_id, speaker="agent", thread=thread, kind="system_note", text=text)
        await events.emit(session_id, "message.created", await messages.public(note, {}))


async def list_all() -> list[dict[str, Any]]:
    """Every session, open and finished, for the admin's Sessions table (US8 #1)."""
    tenants = {t["_id"]: t["name"] async for t in db().tenants.find({}, projection={"name": 1})}
    users = {u["_id"]: u async for u in db().users.find({}, projection={"display_name": 1, "username": 1, "status": 1,
                                                                         "role": 1})}

    def person(user_id: ObjectId | None) -> dict | None:
        u = users.get(user_id) if user_id else None
        return {"id": str(user_id), "display_name": u.get("display_name") or u["username"], "username": u["username"],
                "status": u.get("status", "active")} if u else None

    out = []
    async for s in db().sessions.find({}, sort=[("created_at", -1)]):
        last = await db().events.find_one({"session_id": s["_id"]}, sort=[("event_id", -1)], projection={"created_at": 1})
        done, total, _ = plans.progress(s.get("plan") or [])
        out.append({
            "id": str(s["_id"]), "title": s["title"], "connector_type": s["connector_type"], "status": s["status"],
            "steps": {k: v["state"] for k, v in s["steps"].items()}, "created_at": s["created_at"],
            "finished_at": s.get("finished_at"), "expires_at": s.get("expires_at"), "reopened_at": s.get("reopened_at"),
            "tenant_name": tenants.get(s["tenant_id"], ""),
            "iam_engineer": person(s["iam_engineer_id"]), "application_owner": person(s.get("application_owner_id")),
            "plan_done": done, "plan_total": total,
            "last_activity": (last or {}).get("created_at") or s["created_at"],
            "pending_handover": bool(s.get("pending_handover")),
        })
    return out


async def reopen(session_id: ObjectId, admin_id: ObjectId) -> dict:
    """FR-032: open again, no longer due for deletion; the plan and history stay as they were."""
    from ..chat import events

    session = await repo.get(session_id)
    if not session:
        raise AdminSessionError("not_found", "No such session.")
    if session["status"] != "finished":
        raise AdminSessionError("not_finished", "Only a finished session can be reopened.")
    now = datetime.now(UTC)
    await db().sessions.update_one({"_id": session_id}, {"$set": {"status": "open", "finished_at": None,
                                                                  "expires_at": None, "reopened_at": now}})
    for coll in ("messages", "events", "attachments"):
        await db()[coll].update_many({"session_id": session_id}, {"$set": {"expires_at": None}})
    await _system_note(session_id, f"{await _name(admin_id)} (admin) reopened this session.")
    await audit.record("session_reopened", admin_id, target=str(session_id), detail={"session_id": str(session_id)})
    await events.emit(session_id, "session.updated", {"status": "open", "reopened_at": now})
    return await repo.get(session_id)  # type: ignore[return-value]


async def request_handover(session_id: ObjectId, place: str, user_id: ObjectId, admin_id: ObjectId) -> bool:
    """FR-033: check the target, then hand the place over now, or after the running turn (returns False then)."""
    session = await repo.get(session_id)
    if not session:
        raise AdminSessionError("not_found", "No such session.")
    if place not in PLACES:
        raise AdminSessionError("validation_failed", "place must be iam_engineer or application_owner.")
    target = await db().users.find_one({"_id": user_id})
    if not target:
        raise AdminSessionError("validation_failed", "No such user.")
    if target.get("status", "active") != "active":
        raise AdminSessionError("validation_failed", f"{target['username']} is not active.")
    if target["role"] != place:
        raise AdminSessionError("validation_failed",
                                f"{target['username']} is not an {PLACE_LABEL[place]} account.")
    other = "application_owner" if place == "iam_engineer" else "iam_engineer"
    if session.get(f"{other}_id") == user_id:
        raise AdminSessionError("validation_failed",
                                f"{target['username']} already holds the {PLACE_LABEL[other]}'s place in this session.")
    if session.get(f"{place}_id") == user_id:
        raise AdminSessionError("validation_failed", f"{target['username']} already holds this place.")
    pending = {"place": place, "to_user_id": user_id, "admin_id": admin_id, "requested_at": datetime.now(UTC)}
    if session.get("turn_lock"):
        await db().sessions.update_one({"_id": session_id}, {"$set": {"pending_handover": pending}})
        return False
    await _apply(session, pending)
    return True


async def apply_pending_handover(session_id: ObjectId) -> None:
    """Called by the turn worker once a turn has ended (research R23)."""
    session = await repo.get(session_id)
    if session and session.get("pending_handover") and not session.get("turn_lock"):
        await _apply(session, session["pending_handover"])


async def _apply(session: dict, pending: dict) -> None:
    from ..chat import events

    place, to_user, admin_id = pending["place"], pending["to_user_id"], pending["admin_id"]
    sid = session["_id"]
    previous = session.get(f"{place}_id")
    update: dict[str, Any] = {f"{place}_id": to_user, "pending_handover": None}
    if place == "iam_engineer":
        update["check_order"] = None  # the new IAM engineer orders the checks themselves
    entry = {"place": place, "from_user_id": previous, "to_user_id": to_user, "admin_id": admin_id,
             "at": datetime.now(UTC)}
    await db().sessions.update_one({"_id": sid}, {"$set": update, "$push": {"handovers": entry}})
    admin_name, prev_name, new_name = await _name(admin_id), await _name(previous), await _name(to_user)
    await _system_note(sid, f"{admin_name} (admin) handed the {PLACE_LABEL[place]}'s place from {prev_name} to "
                            f"{new_name}.")
    await audit.record("session_handover", admin_id, target=str(sid),
                       detail={"session_id": str(sid), "place": place, "from_user_id": str(previous),
                               "to_user_id": str(to_user)})
    await events.emit(sid, "participant.changed", {"place": place, "user": {"id": str(to_user),
                                                                            "display_name": new_name}})
    if previous:
        await events.emit(sid, "access.revoked", {"session_id": str(sid)}, visible_to=str(previous))
