"""Long-running aggregations followed without a turn (spec 002 FR-139, research R6, data-model.md Followup).

An agent tool waits in its turn only up to the playbook's `wait_in_turn_seconds`; then it reports the action as
`running` with `follow: true` and the session API takes over. Every minute the loop asks the agent, in the model-free
`task_check` mode (Constitution IV), for the tasks' status. After `pending_after_minutes` the plan step shows
*pending · N min*. When the tasks end, the action is completed, a system note with the result and counts goes to the
IAM engineer's thread, the plan step and proof counts are set, and, if it succeeded and proof steps remain, that note
is queued as a turn run as the IAM engineer who holds the standing check order (Principle III). Nobody needs to be
watching. Followups are kept on the session document, so an API restart picks them up again.
"""

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from bson import ObjectId

from ..agent_client import client as agent
from ..audit import actions
from ..catalog import catalog
from ..db import db
from ..sessions import plan as plans
from ..sessions import repo
from ..tenants import service as tenants
from . import events, messages

log = logging.getLogger(__name__)
MAX_FOLLOWING = 3
INTERVAL_SECONDS = 60
LABEL = {"aggregate_accounts": "Account aggregation", "aggregate_entitlements": "Entitlement aggregation",
         "aggregate_datasets": "AI agent aggregation", "aggregate": "Aggregation"}
_task: asyncio.Task | None = None


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


async def start(session_id: ObjectId, ev: dict, thread: str, turn_id: str | None = None) -> None:
    session = await db().sessions.find_one({"_id": session_id}, projection={"followups": 1, "check_order": 1})
    following = [f for f in (session or {}).get("followups") or [] if f.get("state") == "following"]
    if len(following) >= MAX_FOLLOWING or any(f.get("action_ref") == ev.get("action_ref")
                                              and f.get("turn_id") == turn_id for f in following):
        return
    order = (session or {}).get("check_order") or {}
    now = _now()
    await db().sessions.update_one({"_id": session_id}, {"$push": {"followups": {
        "action_ref": ev.get("action_ref"), "turn_id": turn_id, "kind": ev.get("action"),
        "task_ids": [t for t in ev.get("task_ids") or [] if t], "plan_step": ev.get("plan_step"),
        "next_step": ev.get("next_step"), "source_id": (ev.get("source") or {}).get("id"),
        "ordered_by": {"user_id": order.get("user_id"), "display_name": order.get("display_name")},
        "started_at": now, "last_checked_at": now, "state": "following", "thread": thread, "pending_noted": False}}})
    await events.emit(session_id, "followup.updated", {"plan_step": ev.get("plan_step"), "kind": ev.get("action"),
                                                       "state": "following", "started_at": now, "minutes": 0})


def _pending_after(connector_type: str) -> int:
    return int((catalog.checks(connector_type).get("aggregation") or {}).get("pending_after_minutes", 30))


def _sp_attribute(connector_type: str, session: dict) -> str | None:
    if "service_principals" not in ((session.get("details") or {}).get("capabilities") or []):
        return None
    schema = (catalog.checks(connector_type).get("schema") or {}).get("service_principals") or {}
    return schema.get("type_attribute")


async def _set_step(session_id: ObjectId, step_id: str | None, state: str | None = None, reason: str | None = None,
                    pending_since: datetime | None = None, clear_pending: bool = False) -> None:
    if not step_id:
        return
    session = await repo.get(session_id) or {}
    plan = [dict(s) for s in session.get("plan") or []]
    step = next((s for s in plan if s["id"] == step_id), None)
    if not step:
        return
    if pending_since and not step.get("pending_since"):
        step["pending_since"] = pending_since
    if clear_pending:
        step.pop("pending_since", None)
    if state:
        try:
            plan = plans.apply_ops(plan, [{"op": "set_state", "step_id": step_id, "state": state,
                                           **({"reason": reason} if reason else {})}])
        except plans.PlanError as exc:
            log.warning("follow-up plan update rejected: %s", exc)
    from .turns import _store_plan

    await _store_plan(session_id, plan)


async def _note(session_id: ObjectId, text: str, tone: str, code: str, queue_as: Any = None,
                trigger: str | None = None) -> None:
    note = await messages.add(session_id, speaker="agent", thread="iam_engineer", kind="system_note", text=text,
                              tone=tone, code=code, speaker_user_id=queue_as, queued=bool(queue_as),
                              meta={"trigger": trigger} if trigger else None)
    await events.emit(session_id, "message.created", await messages.public(note, {}))
    if queue_as:
        from . import turns

        await events.emit(session_id, "message.created",
                          await messages.public(await turns.create_reply(session_id, note), {}))
        turns.kick(session_id)


def _counts_text(counts: dict) -> str:
    parts = []
    if counts.get("users") is not None and counts.get("service_principals") is not None:
        parts.append(f"{counts['users']:,} users and {counts['service_principals']:,} service principals")
    elif counts.get("accounts") is not None:
        parts.append(f"{counts['accounts']:,} accounts")
    if counts.get("entitlements") is not None:
        parts.append(f"{counts['entitlements']:,} entitlements")
    return " and ".join(parts)


async def _finish(session: dict, follow: dict, result: dict, minutes: int) -> None:
    session_id = session["_id"]
    tasks = result.get("tasks") or []
    ok = all(t.get("completion_status") in ("SUCCESS", "WARNING") for t in tasks)
    counts = result.get("counts") or {}
    label = LABEL.get(follow.get("kind") or "", "Aggregation")
    await db().sessions.update_one({"_id": session_id, "followups.action_ref": follow.get("action_ref"),
                                    "followups.turn_id": follow.get("turn_id")},
                                   {"$set": {"followups.$.state": "done" if ok else "failed"}})
    # complete the running action record (same turn_id + action_ref)
    if follow.get("turn_id") and follow.get("action_ref"):
        existing = await db().actions.find_one({"turn_id": follow["turn_id"], "action_ref": follow["action_ref"]})
        if existing:
            event = {"action": existing["action"], "action_ref": follow["action_ref"], "source": existing.get("source"),
                     "result": "ok" if ok else "failed",
                     "request": existing.get("request"),
                     "response": {"task_ids": follow.get("task_ids"),
                                  "task_states": {t["id"]: str(t.get("completion_status")) for t in tasks},
                                  "counts": counts,
                                  "error": None if ok else "; ".join(" ".join(map(str, t.get("messages") or []))
                                                                     for t in tasks)[:800]},
                     "summary": _counts_text(counts)[:80] if ok else f"failed after {minutes} min"}
            record, _ = await actions.record(session_id, existing["tenant_id"], ordered_by=existing["ordered_by"],
                                             turn_id=follow["turn_id"], event=event,
                                             trigger=existing.get("trigger", "order"))
            await events.emit(session_id, "action.updated", await actions.public(record),
                              visible_to=events.for_role("iam_engineer"))
    proof = {k: v for k, v in counts.items() if k in ("users", "service_principals", "entitlements")}
    if ok and proof:
        stored = await repo.set_proof(session_id, proof)
        await events.emit(session_id, "proof.updated", stored, visible_to=events.for_role("iam_engineer"))
    await _set_step(session_id, follow.get("plan_step"), "done" if ok else "failed",
                    None if ok else f"failed after {minutes} min", clear_pending=True)
    await events.emit(session_id, "followup.updated", {"plan_step": follow.get("plan_step"), "kind": follow.get("kind"),
                                                       "state": "done" if ok else "failed",
                                                       "started_at": follow.get("started_at"), "minutes": minutes})
    order_user = (follow.get("ordered_by") or {}).get("user_id")
    if ok:
        text = f"{label} finished after {minutes} min" + (f": {_counts_text(counts)}." if counts else ".")
        if follow.get("next_step") and order_user:
            text += f" Continuing with the next step ({follow['next_step']}), as ordered."
            await _note(session_id, text, "success", "followup_finished", queue_as=order_user, trigger="followup")
        else:
            await _note(session_id, text, "success", "followup_finished")
    else:
        why = "; ".join(" ".join(map(str, t.get("messages") or [])) for t in tasks)[:300] or "no details"
        await _note(session_id, f"{label} failed after {minutes} min: {why}", "danger", "followup_finished")


async def check_once(now: datetime | None = None) -> int:
    """One pass over every followed task; returns how many followups were checked."""
    now = now or _now()
    checked = 0
    async for session in db().sessions.find({"followups.state": "following"}):
        tenant = await tenants.get(session["tenant_id"])
        if not tenant:
            continue
        connector = catalog.get(session["connector_type"]) or {}
        for follow in [f for f in session.get("followups") or [] if f.get("state") == "following"]:
            checked += 1
            minutes = int((now - _aware(follow["started_at"])).total_seconds() // 60)
            payload = {"mode": "task_check", "isc_api": connector.get("isc_api"), "task_ids": follow["task_ids"],
                       "tenant": {"api_host": tenant["api_host"], "credential_provider": tenant["credential_provider"]},
                       "counts": {"source_id": follow.get("source_id") or (session.get("source") or {}).get("id"),
                                  "accounts": follow.get("kind") in ("aggregate_accounts", "aggregate"),
                                  "entitlements": follow.get("kind") in ("aggregate_entitlements", "aggregate"),
                                  "sp_attribute": _sp_attribute(session["connector_type"], session)}}
            result: dict = {}
            try:
                async for event in agent.invoke(payload, f"onb-{session['_id']}"):
                    if event.get("type") == "task_check":
                        result = event
            except Exception:  # noqa: BLE001 — try again next minute
                log.exception("task check failed")
                continue
            await db().sessions.update_one({"_id": session["_id"], "followups.action_ref": follow.get("action_ref"),
                                            "followups.turn_id": follow.get("turn_id")},
                                           {"$set": {"followups.$.last_checked_at": now}})
            tasks = result.get("tasks") or []
            if tasks and all(t.get("completion_status") for t in tasks):
                await _finish(session, follow, result, minutes)
                continue
            if minutes >= _pending_after(session["connector_type"]):
                await _set_step(session["_id"], follow.get("plan_step"), pending_since=_aware(follow["started_at"]))
                if not follow.get("pending_noted"):
                    await db().sessions.update_one(
                        {"_id": session["_id"], "followups.action_ref": follow.get("action_ref"),
                         "followups.turn_id": follow.get("turn_id")}, {"$set": {"followups.$.pending_noted": True}})
                    label = LABEL.get(follow.get("kind") or "", "Aggregation")
                    await _note(session["_id"], f"{label} is pending · {minutes} min. It's still running in SailPoint; "
                                "I check it every minute and will post the result here, even if nobody is watching.",
                                "info", "followup_pending")
            await events.emit(session["_id"], "followup.updated", {
                "plan_step": follow.get("plan_step"), "kind": follow.get("kind"), "state": "following",
                "started_at": follow.get("started_at"), "minutes": minutes})
    return checked


async def _loop() -> None:
    while True:
        try:
            await check_once()
        except Exception:  # noqa: BLE001
            log.exception("follow-up pass failed")
        await asyncio.sleep(INTERVAL_SECONDS)


def start_loop() -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_loop())


async def stop_loop() -> None:
    global _task
    if _task:
        _task.cancel()
        _task = None
