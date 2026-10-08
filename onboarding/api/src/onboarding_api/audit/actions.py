"""SailPoint action records (data-model.md `actions`; FR-020, SC-006; no TTL). `ordered_by` always comes from the API's
own record of who sent the message that started the turn, never from the agent."""

from datetime import UTC, datetime

from bson import ObjectId

from ..db import db
from ..masking import mask_obj, mask_text

ACTIONS = ("create_source", "configure_source", "connection_check", "aggregate", "test_connection", "delete_source")
CHECKS = ("connection_check", "aggregate", "test_connection")
TRIGGERS = ("order", "application_owner_confirmation")


OUTCOME = {"create_source": "created", "configure_source": "configured", "connection_check": "passed",
           "aggregate": "completed", "test_connection": "passed", "delete_source": "deleted"}
DIAGNOSIS_MAX = 1000


def outcome(a: dict) -> str:
    """One line for the action list (FR-020a): "passed · 3 accounts read", "failed · <first error line>", "running"."""
    if a["result"] == "running":
        return "running"
    response = a.get("response") or {}
    if a["result"] == "failed":
        error = (response.get("error") or a.get("error") or "no error text").strip().splitlines()[0]
        return f"failed · {error[:140]}"
    counts = response.get("counts") or {}
    parts = [OUTCOME.get(a["action"], "ok")]
    if counts.get("accounts") is not None:
        parts.append(f"{counts['accounts']:,} accounts" + (" read" if a["action"] == "connection_check" else ""))
    if counts.get("entitlements") is not None:
        parts.append(f"{counts['entitlements']:,} entitlements")
    if a.get("source") and a["action"] == "create_source" and a["source"].get("id"):
        parts.append(f"id {str(a['source']['id'])[:8]}…")
    return " · ".join(parts)


def _response(event: dict) -> dict:
    """The response the tool got (masked); older agents sent only `error` and `task_ids`."""
    r = event.get("response") or {}
    counts = {k: int(v) for k, v in (r.get("counts") or {}).items() if k in ("accounts", "entitlements")
              and isinstance(v, int | float)}
    return mask_obj({
        "task_ids": [str(t) for t in r.get("task_ids") or event.get("task_ids") or []],
        "task_states": {str(k): str(v) for k, v in (r.get("task_states") or {}).items()},
        "counts": counts,
        "error": r.get("error") or event.get("error"),
    })


def _when(value: object) -> datetime | None:
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


async def record(session_id: ObjectId, tenant_id: ObjectId, *, ordered_by: ObjectId, turn_id: str, event: dict,
                 trigger: str = "order", order_message_id: ObjectId | None = None) -> tuple[dict, bool]:
    """Insert, or update by (turn_id, action_ref) when a `running` action reports its end (research R24). Returns the
    record and whether it was new. `trigger` says why the action ran: the IAM engineer's order, or a check rerun after
    the application owner confirmed a fix under the IAM engineer's standing order (FR-016a); `ordered_by` names the
    IAM engineer either way."""
    action = event.get("action")
    if action not in ACTIONS:
        action = "configure_source"
    if trigger not in TRIGGERS:
        raise ValueError("unknown trigger")
    result = event.get("result")
    result = result if result in ("ok", "failed", "running") else "failed"
    response = _response(event)
    doc = {
        "session_id": session_id,
        "tenant_id": tenant_id,
        "source": event.get("source"),
        "action": action,
        "ordered_by": ordered_by,
        "trigger": trigger,
        "turn_id": turn_id,
        "order_message_id": order_message_id,
        "request": mask_obj(event.get("request") or event.get("request_summary") or {}),
        "result": result,
        "response": response,
        "error": response.get("error"),
        "task_ids": response["task_ids"],
        "started_at": _when(event.get("started_at")),
        "duration_ms": int(event["duration_ms"]) if isinstance(event.get("duration_ms"), int | float) else None,
        "at": datetime.now(UTC),
    }
    doc["outcome"] = outcome(doc)
    ref = event.get("action_ref")
    if isinstance(ref, str) and ref:
        doc["action_ref"] = ref
        existing = await db().actions.find_one({"turn_id": turn_id, "action_ref": ref})
        if existing:
            doc.pop("order_message_id")
            await db().actions.update_one({"_id": existing["_id"]}, {"$set": doc})
            return await db().actions.find_one({"_id": existing["_id"]}), False  # type: ignore[return-value]
    inserted = await db().actions.insert_one(doc)
    doc["_id"] = inserted.inserted_id
    return doc, True


async def add(session_id: ObjectId, tenant_id: ObjectId, *, ordered_by: ObjectId, turn_id: str, event: dict,
              trigger: str = "order") -> dict:
    """Kept for callers that only need the record."""
    doc, _ = await record(session_id, tenant_id, ordered_by=ordered_by, turn_id=turn_id, event=event, trigger=trigger)
    return doc


async def set_diagnosis(turn_id: str, text: str) -> dict | None:
    """The agent's diagnosis of the turn's last failed action (research R24): ≤ 1,000 characters, masked."""
    last = await db().actions.find_one({"turn_id": turn_id, "result": "failed"}, sort=[("at", -1)])
    if not last:
        return None
    await db().actions.update_one({"_id": last["_id"]},
                                  {"$set": {"diagnosis": mask_text(" ".join(str(text).split()))[:DIAGNOSIS_MAX]}})
    return await db().actions.find_one({"_id": last["_id"]})


async def public(a: dict) -> dict:
    user = await db().users.find_one({"_id": a["ordered_by"]}, projection={"display_name": 1, "username": 1})
    return {
        "id": str(a["_id"]),
        "action": a["action"],
        "source": a.get("source"),
        "ordered_by": {"id": str(a["ordered_by"]),
                       "display_name": (user or {}).get("display_name") or (user or {}).get("username", "")},
        "trigger": a.get("trigger", "order"),
        "order_message_id": str(a["order_message_id"]) if a.get("order_message_id") else None,
        "result": a["result"],
        "outcome": a.get("outcome") or outcome(a),
        "request": a.get("request") or a.get("request_summary") or {},
        "request_missing": not (a.get("request") or a.get("request_summary")),
        "response": a.get("response") or {"task_ids": a.get("task_ids", []), "error": a.get("error")},
        "response_missing": "response" not in a,
        "diagnosis": a.get("diagnosis"),
        "error": a.get("error"),
        "task_ids": a.get("task_ids", []),
        "started_at": a.get("started_at"),
        "duration_ms": a.get("duration_ms"),
        "at": a["at"],
    }


async def get(session_id: ObjectId, action_id: str) -> dict | None:
    try:
        oid = ObjectId(action_id)
    except Exception:  # noqa: BLE001
        return None
    a = await db().actions.find_one({"_id": oid, "session_id": session_id})
    return await public(a) if a else None


async def list_for(session_id: ObjectId) -> list[dict]:
    return [await public(a) async for a in db().actions.find({"session_id": session_id}, sort=[("at", 1)])]
