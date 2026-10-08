"""SailPoint action records (data-model.md `actions`; FR-020, SC-006; no TTL). `ordered_by` always comes from the API's
own record of who sent the message that started the turn, never from the agent."""

from datetime import UTC, datetime

from bson import ObjectId

from ..db import db
from ..masking import mask_obj, mask_text

ACTIONS = ("create_source", "configure_source", "connection_check", "aggregate", "test_connection", "delete_source")
CHECKS = ("connection_check", "aggregate", "test_connection")
TRIGGERS = ("order", "application_owner_confirmation")


async def add(session_id: ObjectId, tenant_id: ObjectId, *, ordered_by: ObjectId, turn_id: str, event: dict,
              trigger: str = "order") -> dict:
    """`trigger` says why the action ran: the IAM engineer's order, or a check rerun after the application owner
    confirmed a fix under the IAM engineer's standing order (FR-016a); `ordered_by` names the IAM engineer either way."""
    action = event.get("action")
    if action not in ACTIONS:
        action = "configure_source"
    if trigger not in TRIGGERS:
        raise ValueError("unknown trigger")
    doc = {
        "session_id": session_id,
        "tenant_id": tenant_id,
        "source": event.get("source"),
        "action": action,
        "ordered_by": ordered_by,
        "trigger": trigger,
        "turn_id": turn_id,
        "request_summary": mask_obj(event.get("request_summary") or {}),
        "result": "ok" if event.get("result") == "ok" else "failed",
        "error": mask_text(event["error"]) if event.get("error") else None,
        "task_ids": [str(t) for t in event.get("task_ids") or []],
        "at": datetime.now(UTC),
    }
    result = await db().actions.insert_one(doc)
    doc["_id"] = result.inserted_id
    return doc


async def public(a: dict) -> dict:
    user = await db().users.find_one({"_id": a["ordered_by"]}, projection={"display_name": 1, "username": 1})
    return {
        "id": str(a["_id"]),
        "action": a["action"],
        "source": a.get("source"),
        "ordered_by": {"id": str(a["ordered_by"]),
                       "display_name": (user or {}).get("display_name") or (user or {}).get("username", "")},
        "trigger": a.get("trigger", "order"),
        "result": a["result"],
        "error": a.get("error"),
        "task_ids": a.get("task_ids", []),
        "at": a["at"],
    }


async def list_for(session_id: ObjectId) -> list[dict]:
    return [await public(a) async for a in db().actions.find({"session_id": session_id}, sort=[("at", 1)])]
