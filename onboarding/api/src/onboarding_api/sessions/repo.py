"""Onboarding sessions (data-model.md `sessions`)."""

from datetime import UTC, datetime, timedelta
from typing import Any

from bson import ObjectId

from ..config import settings
from ..db import db
from ..masking import mask_text
from . import plan as plans

STEPS = ("application_ready", "source_created", "configured", "connection_check", "aggregation", "test_connection")
STATES = ("not_started", "in_progress", "passed", "failed")
_ALLOWED = {
    ("not_started", "in_progress"), ("not_started", "passed"), ("not_started", "failed"),
    ("in_progress", "passed"), ("in_progress", "failed"),
    ("failed", "in_progress"), ("failed", "passed"),  # rerun after a fix
    ("passed", "in_progress"), ("passed", "failed"),  # the agent reruns a check after a fix
}


class TransitionError(ValueError):
    pass


def check_transition(step: str, old: str, new: str) -> None:
    if step not in STEPS:
        raise TransitionError(f"unknown step {step}")
    if new not in STATES:
        raise TransitionError(f"unknown state {new}")
    if old != new and (old, new) not in _ALLOWED:
        raise TransitionError(f"{step}: {old} -> {new} is not allowed")


async def create(*, title: str, connector_type: str, tenant_id: ObjectId, details: dict[str, Any],
                 iam_engineer_id: ObjectId, application_owner_id: ObjectId | None,
                 plan: list[dict[str, Any]] | None = None) -> dict:
    now = datetime.now(UTC)
    doc = {
        "title": title,
        "connector_type": connector_type,
        "tenant_id": tenant_id,
        "details": details,
        "iam_engineer_id": iam_engineer_id,
        "application_owner_id": application_owner_id,
        "steps": {s: {"state": "not_started", "changed_at": now} for s in STEPS},
        "plan": plan or [],
        "source": None,
        "turn_lock": None,
        "waiting_on": None,
        "waiting_reason": None,
        "check_order": None,
        "suggestions": {"iam_engineer": [], "application_owner": [], "for_event_id": 0},
        "msg_seq": 0,
        "event_seq": 0,
        "status": "open",
        "finished_at": None,
        "expires_at": None,
        "created_at": now,
    }
    result = await db().sessions.insert_one(doc)
    doc["_id"] = result.inserted_id
    return doc


async def get(session_id: str | ObjectId) -> dict | None:
    try:
        oid = ObjectId(session_id)
    except Exception:  # noqa: BLE001
        return None
    return await db().sessions.find_one({"_id": oid})


def participant_role(session: dict, user_id: ObjectId) -> str | None:
    if session["iam_engineer_id"] == user_id:
        return "iam_engineer"
    if session.get("application_owner_id") == user_id:
        return "application_owner"
    return None


async def list_for(user_id: ObjectId, status: str | None = None) -> list[dict]:
    query: dict[str, Any] = {"$or": [{"iam_engineer_id": user_id}, {"application_owner_id": user_id}]}
    if status:
        query["status"] = status
    return [s async for s in db().sessions.find(query, sort=[("created_at", -1)])]


async def set_step(session_id: ObjectId, step: str, state: str) -> dict | None:
    """Validate and apply a step change; returns {step, state, changed_at} or None when unchanged."""
    session = await get(session_id)
    if not session:
        return None
    old = session["steps"][step]["state"] if step in session["steps"] else "not_started"
    check_transition(step, old, state)
    if old == state:
        return None
    now = datetime.now(UTC)
    await db().sessions.update_one({"_id": session_id},
                                   {"$set": {f"steps.{step}": {"state": state, "changed_at": now}}})
    return {"step": step, "state": state, "changed_at": now}


async def set_plan(session_id: ObjectId, plan: list[dict[str, Any]]) -> list[dict]:
    """Store the plan and re-derive the milestones from it (FR-008c); returns the milestone changes."""
    session = await get(session_id)
    if not session:
        return []
    now = datetime.now(UTC)
    changes = []
    update: dict[str, Any] = {"plan": plan}
    for step, state in plans.derive_milestones(plan).items():
        old = (session["steps"].get(step) or {}).get("state", "not_started")
        if old != state:
            update[f"steps.{step}"] = {"state": state, "changed_at": now}
            changes.append({"step": step, "state": state, "changed_at": now})
    await db().sessions.update_one({"_id": session_id}, {"$set": update})
    return changes


async def set_source(session_id: ObjectId, source: dict | None) -> None:
    value = {"id": source["id"], "name": source["name"]} if source else None
    await db().sessions.update_one({"_id": session_id}, {"$set": {"source": value}})


async def next_counter(session_id: ObjectId, field: str) -> int:
    doc = await db().sessions.find_one_and_update({"_id": session_id}, {"$inc": {field: 1}},
                                                  projection={field: 1}, return_document=True)
    return int(doc[field])


async def finish(session_id: ObjectId) -> datetime:
    now = datetime.now(UTC)
    expires = now + timedelta(days=settings().retention_days)
    await db().sessions.update_one({"_id": session_id},
                                   {"$set": {"status": "finished", "finished_at": now, "expires_at": expires}})
    for coll in ("messages", "events", "attachments"):
        await db()[coll].update_many({"session_id": session_id}, {"$set": {"expires_at": expires}})
    return expires


# --- threads (research R15), standing check order (R16) and suggestions (R17)

THREADS = ("iam_engineer", "application_owner")


WAITING_REASON_MAX = 120


def clean_waiting_reason(reason: str | None) -> str | None:
    """The waiting banner's reason (FR-006g, research R20): one line, masked, at most 120 characters."""
    text = mask_text(" ".join((reason or "").split()))
    return text[:WAITING_REASON_MAX].rstrip() or None


async def set_waiting(session_id: ObjectId, on: str | None, reason: str | None = None) -> str | None:
    """Who the agent waits for, and that person's next step: information only, it never blocks or reorders the
    queue (FR-006a). The reason is cleared with the wait. Returns the stored reason."""
    if on is not None and on not in THREADS:
        raise ValueError("waiting_on must be iam_engineer, application_owner or null")
    stored = clean_waiting_reason(reason) if on else None
    await db().sessions.update_one({"_id": session_id}, {"$set": {"waiting_on": on, "waiting_reason": stored}})
    return stored


async def set_check_order(session_id: ObjectId, *, user_id: ObjectId, display_name: str, turn_id: str) -> dict:
    """The IAM engineer's order to run the checks stands until they pass (FR-016a)."""
    order = {"user_id": user_id, "display_name": display_name, "turn_id": turn_id, "at": datetime.now(UTC)}
    await db().sessions.update_one({"_id": session_id}, {"$set": {"check_order": order}})
    return order


async def clear_check_order(session_id: ObjectId) -> None:
    await db().sessions.update_one({"_id": session_id}, {"$set": {"check_order": None}})


async def set_suggestions(session_id: ObjectId, thread: str, items: list[dict], event_id: int) -> None:
    if thread not in THREADS:
        raise ValueError("unknown thread")
    await db().sessions.update_one({"_id": session_id},
                                   {"$set": {f"suggestions.{thread}": items, "suggestions.for_event_id": event_id}})


def all_checks_passed(session: dict) -> bool:
    return all(session["steps"].get(k, {}).get("state") == "passed"
               for k in ("connection_check", "aggregation", "test_connection"))

