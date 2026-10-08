"""Per-session turn worker (FR-006a, research R3, R15–R17, contracts/agent-invocation.md).

One turn at a time per session: an atomic `turn_lock` on the session document. The worker takes the lowest-seq
queued message from either thread, invokes the agent with the session context and the masked history of both
threads, maps every streamed event into messages / steps / actions / live events, then moves on to the next queued
message.

Threads (FR-006c): the agent's streamed reply always lands in the writer's thread. The only way it reaches the other
thread is an `other_thread` event from its thread tools, which also leaves a relay note in the writer's thread
(the pairing rule is enforced here, not trusted). Check reruns after the application owner's confirmation are
recorded under the IAM engineer's standing `check_order` (FR-016a).
"""

import asyncio
import base64
import logging
import time
import uuid
from datetime import UTC, datetime, timedelta

from bson import ObjectId

from ..agent_client import client as agent
from ..audit import actions
from ..catalog import catalog
from ..config import settings
from ..db import db, screenshots
from ..logging import turn_id_var
from ..masking import mask_obj, mask_text
from ..metrics import record_metric
from ..sessions import repo
from ..tenants import service as tenants
from . import events, messages, suggestions

log = logging.getLogger(__name__)
_running: dict[str, asyncio.Task] = {}
LOCK_STALE = timedelta(minutes=10)
GENERIC_RELAY = {"iam_engineer": "Posted a message in the IAM engineer's thread.",
                 "application_owner": "Posted a message in the application owner's thread."}


def kick(session_id: ObjectId) -> None:
    """Start the worker for a session if it isn't running in this process."""
    key = str(session_id)
    task = _running.get(key)
    if task and not task.done():
        return
    _running[key] = asyncio.create_task(_drain(session_id))


async def _acquire(session_id: ObjectId, turn_id: str) -> bool:
    now = datetime.now(UTC)
    result = await db().sessions.update_one(
        {"_id": session_id, "$or": [{"turn_lock": None}, {"turn_lock.since": {"$lt": now - LOCK_STALE}}]},
        {"$set": {"turn_lock": {"turn_id": turn_id, "since": now}}},
    )
    return result.modified_count == 1


async def _release(session_id: ObjectId, turn_id: str) -> None:
    await db().sessions.update_one({"_id": session_id, "turn_lock.turn_id": turn_id}, {"$set": {"turn_lock": None}})


async def _drain(session_id: ObjectId) -> None:
    while True:
        message = await messages.next_queued(session_id)
        if not message:
            return
        turn_id = f"t_{uuid.uuid4().hex[:16]}"
        if not await _acquire(session_id, turn_id):
            return  # another worker owns the session; it will drain the queue
        try:
            await _run_turn(session_id, message, turn_id)
        except Exception:  # noqa: BLE001
            log.exception("turn crashed", extra={"turn_id": turn_id})
            await _fail(session_id, message, turn_id, "The turn failed unexpectedly.")
        finally:
            await _release(session_id, turn_id)


async def _context(session: dict, tenant: dict) -> dict:
    steps = {k: v["state"] for k, v in session["steps"].items()}
    return {
        "id": str(session["_id"]),
        "connector_type": session["connector_type"],
        "tenant": {"name": tenant["name"], "api_host": tenant["api_host"],
                   "credential_provider": tenant["credential_provider"], "external_id": tenant.get("external_id")},
        "details": session["details"],
        "steps": steps,
        "source": session.get("source"),
    }


async def _history(session_id: ObjectId, current_id: ObjectId) -> list[dict]:
    """Everything said so far in **both** threads, in queue order, including agent replies to earlier turns (they
    get a higher seq than messages that were already queued), minus the current message and messages still waiting
    behind it. Each entry is tagged with its thread and kind (FR-006a)."""
    limit = settings().history_messages
    query = {"session_id": session_id, "_id": {"$ne": current_id}, "queue_state": {"$nin": ["queued", "processing"]}}
    items = [m async for m in db().messages.find(query, sort=[("seq", -1)], limit=limit)]
    items.reverse()
    older = await db().messages.count_documents(query)
    out = []
    if older > len(items):
        out.append({"seq": 0, "thread": "iam_engineer", "kind": "message", "speaker": "system",
                    "text": f"({older - len(items)} earlier messages not shown)"})
    out += [{"seq": m["seq"], "thread": m.get("thread") or m["speaker"], "kind": m.get("kind", "message"),
             "speaker": m["speaker"], "text": m["text"]} for m in items]
    return out


async def _images(message: dict) -> list[dict]:
    out = []
    for att_id in message.get("attachment_ids") or []:
        att = await db().attachments.find_one({"_id": att_id, "secret_check": "passed"})
        if not att or not att.get("gridfs_id"):
            continue
        stream = await screenshots().open_download_stream(att["gridfs_id"])
        data = await stream.read()
        out.append({"media_type": att["content_type"], "data_b64": base64.b64encode(data).decode()})
    return out


def _public_check_order(session: dict) -> dict | None:
    order = session.get("check_order")
    return {"user_id": str(order["user_id"]), "display_name": order["display_name"]} if order else None


async def _emit_message(session_id: ObjectId, doc: dict, names: dict[str, str]) -> None:
    await events.emit(session_id, "message.created", await messages.public(doc, names))


async def refresh_suggestions(session_id: ObjectId, agent_items: dict[str, list] | None = None) -> None:
    """Merge the agent's suggestions (if any) with the playbook defaults for each thread and tell each
    participant about their own list only (FR-006e, SC-011)."""
    session = await repo.get(session_id)
    if not session:
        return
    defaults = catalog.suggestion_defaults(session["connector_type"])
    for thread, user_key in (("iam_engineer", "iam_engineer_id"), ("application_owner", "application_owner_id")):
        items = suggestions.for_thread(session, thread, (agent_items or {}).get(thread) or [], defaults)
        public = [{"text": i["text"], "kind": i["kind"]} for i in items]
        event_id = session.get("event_seq", 0)
        await repo.set_suggestions(session_id, thread, items, event_id)
        if session.get(user_key):
            await events.emit(session_id, "suggestions.updated",
                              {"thread": thread, "items": public, "for_event_id": event_id},
                              visible_to=str(session[user_key]))


async def _run_turn(session_id: ObjectId, message: dict, turn_id: str) -> None:
    token = turn_id_var.set(turn_id)
    try:
        session = await repo.get(session_id)
        tenant = await tenants.get(session["tenant_id"]) if session else None
        if not session or not tenant:
            await _fail(session_id, message, turn_id, "The session or its tenant no longer exists.")
            return
        await messages.set_queue_state(message["_id"], "processing", turn_id)
        await events.emit(session_id, "message.queue", {"message_id": str(message["_id"]), "queue_state": "processing"})
        user = await db().users.find_one({"_id": message["speaker_user_id"]})
        thread = message.get("thread") or message["speaker"]
        other = messages.other_thread(thread)
        role = user["role"]
        # The standing check order (research R16) survives questions: only a new SailPoint change ordered by the IAM
        # engineer (create, configure, delete) replaces it, below; running the checks again renews it.
        check_order = session.get("check_order")
        payload = {
            "mode": "turn",
            "turn_id": turn_id,
            "lang": "en",
            "ordered_by": {"user_id": str(user["_id"]), "role": role, "display_name": user["display_name"]},
            "session": await _context(session, tenant),
            "history": await _history(session_id, message["_id"]),
            "message": {"seq": message["seq"], "thread": thread, "speaker": message["speaker"],
                        "text": message["text"]},
            "images": await _images(message),
            "waiting_on": session.get("waiting_on"),
            "waiting_reason": session.get("waiting_reason"),
            "check_order": _public_check_order(session),
            "suggestion_defaults": {
                r: [i["text"] for i in (catalog.suggestion_defaults(session["connector_type"]).get(r) or {}).get("any", [])]
                for r in messages.THREADS},
        }
        names = {str(user["_id"]): user["display_name"]}
        started = time.monotonic()
        first_delta = True
        reply_text = ""
        app_steps: list[dict] = []
        agent_suggestions: dict[str, list] = {}
        reply_id = str(uuid.uuid4())
        wait_set = False
        async for ev in agent.invoke(payload, f"onb-{session_id}"):
            kind = ev.get("type")
            if kind == "delta":
                if first_delta:
                    record_metric("first_token_ms", (time.monotonic() - started) * 1000, turn_id=turn_id)
                    first_delta = False
                await events.emit(session_id, "agent.delta", {"turn_id": turn_id, "message_id": reply_id,
                                                              "thread": thread,
                                                              "text_delta": mask_text(ev.get("text", ""))})
            elif kind == "progress":
                text = ev.get("text")
                await events.emit(session_id, "agent.progress",
                                  {"turn_id": turn_id, "thread": thread, "text": mask_text(text) if text else None})
            elif kind == "set_step":
                try:
                    change = await repo.set_step(session_id, ev["step"], ev["state"])
                except repo.TransitionError as exc:
                    log.warning("rejected step change: %s", exc)
                    change = None
                if change:
                    await events.emit(session_id, "step.changed", change)
            elif kind == "action":
                if role == "iam_engineer":
                    ordered_by, trigger = user["_id"], "order"
                    if ev.get("action") in actions.CHECKS:
                        check_order = await repo.set_check_order(session_id, user_id=user["_id"],
                                                                 display_name=user["display_name"], turn_id=turn_id)
                    elif check_order:
                        await repo.clear_check_order(session_id)  # a new change order replaces the standing one
                        check_order = None
                elif check_order and ev.get("action") in actions.CHECKS:
                    ordered_by, trigger = check_order["user_id"], "application_owner_confirmation"
                else:
                    log.warning("dropped action %s from an application owner turn without a standing order",
                                ev.get("action"))
                    continue
                record = await actions.add(session_id, tenant["_id"], ordered_by=ordered_by, turn_id=turn_id,
                                           event=ev, trigger=trigger)
                await events.emit(session_id, "action.recorded", await actions.public(record))
            elif kind == "source":
                source = {"id": ev["id"], "name": ev["name"]} if ev.get("id") else None  # empty = deleted
                await repo.set_source(session_id, source)
                await events.emit(session_id, "session.updated", {"source": source})
            elif kind == "application_step":
                app_steps.append({"text": mask_text(ev.get("text", "")), "read_only": bool(ev.get("read_only")),
                                  "index": ev.get("index")})
            elif kind == "other_thread":
                # FR-006c: a message for the other participant goes in their thread, with a relay note in the
                # writer's thread. Without text it is a one-line note for the other thread (news for both).
                relay_note = (ev.get("relay_note") or "").strip()
                text = (ev.get("text") or "").strip()
                if text:
                    posted = await messages.add(session_id, speaker="agent", thread=other, text=text, turn_id=turn_id,
                                                relayed_from=ev.get("relayed_from") or None)
                    await _emit_message(session_id, posted, names)
                    note = await messages.add(session_id, speaker="agent", thread=thread, kind="relay_note",
                                              text=relay_note or GENERIC_RELAY[other], turn_id=turn_id,
                                              relay_ref=posted["_id"])
                    await _emit_message(session_id, note, names)
                elif relay_note:
                    note = await messages.add(session_id, speaker="agent", thread=other, kind="relay_note",
                                              text=relay_note, turn_id=turn_id)
                    await _emit_message(session_id, note, names)
            elif kind == "waiting":
                on = ev.get("on") or None
                if on in messages.THREADS or on is None:
                    wait_set = True
                    reason = await repo.set_waiting(session_id, on, ev.get("reason"))
                    await events.emit(session_id, "thread.waiting", {"waiting_on": on, "reason": reason})
            elif kind == "suggestions":
                if ev.get("thread") in messages.THREADS:
                    agent_suggestions.setdefault(ev["thread"], []).extend(ev.get("items") or [])
            elif kind == "tenant_status":
                await tenants.set_status(tenant["_id"], ev.get("status", "credential_rejected"))
            elif kind == "final":
                reply_text = ev.get("text", "")
            elif kind == "error":
                await _fail(session_id, message, turn_id, ev.get("message") or "The agent reported an error.")
                return
        if not reply_text:
            await _fail(session_id, message, turn_id, "The agent returned no reply.")
            return
        reply = await messages.add(session_id, speaker="agent", thread=thread, text=reply_text, turn_id=turn_id,
                                   meta={"application_steps": app_steps} if app_steps else None)
        await events.emit(session_id, "agent.message", await messages.public(reply, names) | {"replaces": reply_id})
        await events.emit(session_id, "agent.progress", {"turn_id": turn_id, "thread": thread, "text": None})
        if not wait_set and session.get("waiting_on") == thread:
            # The awaited participant answered and the agent set no new wait: the wait is over (research R20).
            await repo.set_waiting(session_id, None)
            await events.emit(session_id, "thread.waiting", {"waiting_on": None, "reason": None})
        after = await repo.get(session_id)
        if after and after.get("check_order") and repo.all_checks_passed(after):
            await repo.clear_check_order(session_id)
        await messages.set_queue_state(message["_id"], "answered")
        await events.emit(session_id, "message.queue", {"message_id": str(message["_id"]), "queue_state": "answered"})
        await refresh_suggestions(session_id, agent_suggestions)
    finally:
        turn_id_var.reset(token)


async def _fail(session_id: ObjectId, message: dict, turn_id: str, reason: str) -> None:
    """Re-queue once, then answer with an error reply in the writer's thread (contracts/live-events.md `turn.failed`)."""
    await events.emit(session_id, "turn.failed", {"turn_id": turn_id, "reason": reason})
    attempts = (message.get("meta") or {}).get("attempts", 0) + 1
    if attempts < 2:
        await db().messages.update_one({"_id": message["_id"]},
                                       {"$set": {"queue_state": "queued", "meta.attempts": attempts}})
        await events.emit(session_id, "message.queue", {"message_id": str(message["_id"]), "queue_state": "queued"})
        return
    reply = await messages.add(session_id, speaker="agent", thread=message.get("thread") or message["speaker"],
                               turn_id=turn_id, text=f"I couldn't finish that: {reason} Please try again in a moment.")
    await events.emit(session_id, "agent.message", mask_obj(await messages.public(reply, {})))
    await messages.set_queue_state(message["_id"], "answered")
    await events.emit(session_id, "message.queue", {"message_id": str(message["_id"]), "queue_state": "answered"})
