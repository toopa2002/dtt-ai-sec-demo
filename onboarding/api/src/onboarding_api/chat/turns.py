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
from ..metrics import record_metric, record_usage
from ..secrets import service as secrets
from ..sessions import plan as plans
from ..sessions import repo
from ..tenants import service as tenants
from . import events, messages, status, suggestions

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
            await _after_turn(session_id)


async def queue_status(session_id: ObjectId, message: dict) -> tuple[int, str]:
    """How many messages are before this one in the session queue, and the status text for its reply (FR-006h)."""
    ahead_docs = [m async for m in db().messages.find(
        {"session_id": session_id, "queue_state": {"$in": ["queued", "processing"]}, "seq": {"$lt": message["seq"]}},
        sort=[("seq", 1)], projection={"speaker_user_id": 1, "queue_state": 1})]
    first = next((m for m in ahead_docs if m.get("queue_state") == "processing"), ahead_docs[0] if ahead_docs else None)
    name, same = None, False
    if first:
        same = first.get("speaker_user_id") == message.get("speaker_user_id")
        user = await db().users.find_one({"_id": first.get("speaker_user_id")}, projection={"display_name": 1})
        name = (user or {}).get("display_name")
    return len(ahead_docs), status.received_text(len(ahead_docs), name, same)


async def create_reply(session_id: ObjectId, message: dict) -> dict:
    """The agent's reply, created with the participant's message: `received` with how many are ahead (research R21)."""
    ahead, text = await queue_status(session_id, message)
    return await messages.add(session_id, speaker="agent", thread=message.get("thread") or message["speaker"],
                              text="", reply_to=message["_id"], reply_state="received", ahead=ahead,
                              status_text=text)


async def _reply_status(session_id: ObjectId, reply: dict) -> None:
    await events.emit(session_id, "reply.status", {"message_id": str(reply["_id"]), "reply_state": reply["reply_state"],
                                                   "ahead": reply.get("ahead"), "status_text": reply.get("status_text")})


async def refresh_waiting_replies(session_id: ObjectId) -> None:
    """Recount `ahead` for every reply still `received` and tell both screens about the ones that changed."""
    async for message in db().messages.find({"session_id": session_id, "queue_state": "queued"}, sort=[("seq", 1)]):
        reply = await messages.reply_for(message["_id"])
        if not reply or reply.get("reply_state") != "received":
            continue
        ahead, text = await queue_status(session_id, message)
        if ahead != reply.get("ahead") or text != reply.get("status_text"):
            updated = await messages.set_reply(reply["_id"], ahead=ahead, status_text=text)
            if updated:
                await _reply_status(session_id, updated)


async def _store_plan(session_id: ObjectId, plan: list[dict]) -> None:
    """Save the plan, re-derive the milestones and tell both screens (FR-008a-c)."""
    for change in await repo.set_plan(session_id, plan):
        await events.emit(session_id, "step.changed", change)
    done, total, next_step = plans.progress(plan)
    await events.emit(session_id, "plan.updated", {"plan": plans.public(plan), "done": done, "total": total,
                                                   "next_step_id": next_step})


async def _after_turn(session_id: ObjectId) -> None:
    """A handover asked for during the turn takes effect now that the answer is complete (research R23)."""
    from ..sessions import admin

    try:
        await admin.apply_pending_handover(session_id)
    except Exception:  # noqa: BLE001
        log.exception("pending handover failed")


async def _context(session: dict, tenant: dict) -> dict:
    steps = {k: v["state"] for k, v in session["steps"].items()}
    return {
        "id": str(session["_id"]),
        "connector_type": session["connector_type"],
        "tenant": {"name": tenant["name"], "api_host": tenant["api_host"],
                   "credential_provider": tenant["credential_provider"], "external_id": tenant.get("external_id")},
        "details": session["details"],
        "steps": steps,
        "plan": [{k: v for k, v in step.items() if k != "changed_at"} for step in plans.public(session.get("plan") or [])],
        "source": session.get("source"),
        **({"mode": session.get("mode", "new")} if session.get("mode") else {}),
        **({"application_secret": secrets.turn_context(session)} if session.get("application_secret") else {}),
    }


PROOF_KEYS = ("users", "service_principals", "entitlements", "ai_agents")
TENANT_LIMITATION_WAIT = "start it in ISC as the agent described, then say \"done\""


async def _after_action(session_id: ObjectId, session: dict, ev: dict, thread: str, turn_id: str) -> None:
    """Spec 002: what an Entra action means for the session beyond its record (contracts/agent-invocation.md)."""
    action, result = ev.get("action"), ev.get("result")
    if result == "ok" and ev.get("secret_applied"):
        await secrets.mark_applied(session_id)
    if action == "test_connection" and result == "ok":
        current = await repo.get(session_id) or {}
        source = current.get("source") or {}
        if source and (ev.get("source") or {}).get("id") in (None, source.get("id")):
            await secrets.delete_vault_copy(session_id, source.get("id"))
    counts = ((ev.get("response") or {}).get("counts") or {})
    proof = {k: int(v) for k, v in counts.items() if k in PROOF_KEYS and isinstance(v, int | float)}
    if action == "aggregate_datasets":
        if result == "tenant_limitation":
            proof["ai_agents_state"] = "tenant_limitation"
        elif result == "ok":
            proof["ai_agents_state"] = "counted"
    if proof and result in ("ok", "tenant_limitation"):
        stored = await repo.set_proof(session_id, proof)
        await events.emit(session_id, "proof.updated", stored, visible_to=events.for_role("iam_engineer"))
    if result == "tenant_limitation":
        current = await repo.get(session_id) or {}
        step = ev.get("plan_step")
        if step and any(st["id"] == step for st in current.get("plan") or []):
            try:
                await _store_plan(session_id, plans.apply_ops(current.get("plan") or [], [
                    {"op": "set_state", "step_id": step, "state": "blocked",
                     "reason": "start it in ISC (tenant limitation)"}]))
            except plans.PlanError as exc:
                log.warning("tenant limitation plan update rejected: %s", exc)
        reason = await repo.set_waiting(session_id, "iam_engineer", ev.get("waiting_reason") or TENANT_LIMITATION_WAIT)
        await events.emit(session_id, "thread.waiting", {"waiting_on": "iam_engineer", "reason": reason})
        await repo.set_hint(session_id, "iam_engineer", "tenant_limitation")
    elif action == "aggregate_datasets" and result == "ok":
        await repo.set_hint(session_id, "iam_engineer", None)
    if ev.get("follow") and result == "running":
        from . import followups

        await followups.start(session_id, ev, thread, turn_id)


async def after_secret_submitted(session_id: ObjectId, user) -> None:  # type: ignore[no-untyped-def]
    """FR-122: a new secret takes effect in ISC. Under the IAM engineer's standing check order the owner's submission
    starts a turn that applies it and reruns the checks (research R2); otherwise the IAM engineer is told."""
    session = await repo.get(session_id)
    if not session:
        return
    await repo.set_hint(session_id, "application_owner", None)
    if not session.get("source"):
        return  # first secret: the IAM engineer's order to create the source will use it
    if session.get("check_order"):
        note = await messages.add(session_id, speaker="agent", thread="application_owner", kind="system_note",
                                  text="New secret received. Applying it in SailPoint and rerunning the checks.",
                                  speaker_user_id=user.id, queued=True, meta={"trigger": "secret_submitted"},
                                  tone="info")
        await _emit_message(session_id, note, {})
        await _emit_message(session_id, await create_reply(session_id, note), {})
        kick(session_id)
        return
    note = await messages.add(session_id, speaker="agent", thread="iam_engineer", kind="system_note",
                              text="A new secret is waiting; order 'apply the new secret' when you're ready.",
                              tone="info")
    await _emit_message(session_id, note, {})


async def _history(session_id: ObjectId, current_id: ObjectId) -> list[dict]:
    """Everything said so far in **both** threads, in queue order, including agent replies to earlier turns (they
    get a higher seq than messages that were already queued), minus the current message and messages still waiting
    behind it. Each entry is tagged with its thread and kind (FR-006a)."""
    limit = settings().history_messages
    query = {"session_id": session_id, "_id": {"$ne": current_id}, "queue_state": {"$nin": ["queued", "processing"]},
             "reply_state": {"$nin": ["received", "working"]}}
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
    for thread in messages.THREADS:
        items = suggestions.for_thread(session, thread, (agent_items or {}).get(thread) or [], defaults)
        public = [{"text": i["text"], "kind": i["kind"]} for i in items]
        event_id = session.get("event_seq", 0)
        await repo.set_suggestions(session_id, thread, items, event_id)
        await events.emit(session_id, "suggestions.updated",
                          {"thread": thread, "items": public, "for_event_id": event_id},
                          visible_to=events.for_role(thread))


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
        reply_doc = await messages.reply_for(message["_id"]) or await create_reply(session_id, message)
        reply_doc = await messages.set_reply(reply_doc["_id"], reply_state="working", ahead=0, turn_id=turn_id,
                                             status_text=status.working_text(None)) or reply_doc
        await _reply_status(session_id, reply_doc)
        await refresh_waiting_replies(session_id)
        user = await db().users.find_one({"_id": message["speaker_user_id"]})
        thread = message.get("thread") or message["speaker"]
        other = messages.other_thread(thread)
        role = user["role"]
        # A queued system note (spec 002: follow-up continuation, secret submitted) runs as the user it names.
        meta_trigger = (message.get("meta") or {}).get("trigger")
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
            "message": {"seq": message["seq"], "thread": thread,
                        "speaker": "system" if message["speaker"] == "agent" else message["speaker"],
                        "text": message["text"]},
            "images": await _images(message),
            "waiting_on": session.get("waiting_on"),
            "waiting_reason": session.get("waiting_reason"),
            "check_order": _public_check_order(session),
            **({"trigger": meta_trigger} if meta_trigger else {}),
            **({"secret_waiting": True}
               if (session.get("application_secret") or {}).get("state") == "received" else {}),
            **({"secret_exposed": True} if (message.get("meta") or {}).get("secret_exposed") else {}),
            "suggestion_defaults": {
                r: [i["text"] for i in (catalog.suggestion_defaults(session["connector_type"]).get(r) or {}).get("any", [])]
                for r in messages.THREADS},
        }
        names = {str(user["_id"]): user["display_name"]}
        started = time.monotonic()
        first_delta = True
        reply_text = ""
        agent_suggestions: dict[str, list] = {}
        reply_id = str(reply_doc["_id"])
        wait_set = False
        posted_other = False
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
                if text:
                    updated = await messages.set_reply(reply_doc["_id"], status_text=status.working_text(text))
                    if updated:
                        await _reply_status(session_id, updated)
            elif kind == "set_step":
                # A milestone from the SailPoint tools marks its plan step; the milestones follow the plan (R22).
                current = (await repo.get(session_id)) or {}
                current_plan = current.get("plan") or []
                mapping = catalog.plan_steps_by_milestone(session["connector_type"])
                old = ((current.get("steps") or {}).get(ev.get("step")) or {}).get("state", "not_started")
                try:
                    repo.check_transition(ev.get("step", ""), old, ev.get("state", ""))
                except repo.TransitionError as exc:
                    log.warning("rejected step change: %s", exc)
                    continue
                if any(st.get("milestone") == ev.get("step") for st in current_plan):
                    try:
                        new_plan = plans.mark_milestone(current_plan, ev.get("step", ""), ev.get("state", ""),
                                                        mapping.get(ev.get("step", "")))
                    except plans.PlanError as exc:
                        log.warning("rejected step change: %s", exc)
                    else:
                        await _store_plan(session_id, new_plan)
                else:  # a connector type without a plan step for this milestone: set the milestone itself
                    try:
                        change = await repo.set_step(session_id, ev["step"], ev["state"])
                    except repo.TransitionError as exc:
                        log.warning("rejected step change: %s", exc)
                        change = None
                    if change:
                        await events.emit(session_id, "step.changed", change)
            elif kind == "plan":
                current = await repo.get(session_id)
                try:
                    new_plan = plans.apply_ops((current or {}).get("plan") or [], ev.get("ops") or [])
                except plans.PlanError as exc:
                    log.warning("rejected plan ops: %s", exc)
                else:
                    await _store_plan(session_id, new_plan)
            elif kind == "action":
                if role == "iam_engineer":
                    ordered_by, trigger = user["_id"], ("followup" if meta_trigger == "followup" else "order")
                    if ev.get("action") in actions.CHECKS:
                        check_order = await repo.set_check_order(session_id, user_id=user["_id"],
                                                                 display_name=user["display_name"], turn_id=turn_id)
                    elif check_order:
                        await repo.clear_check_order(session_id)  # a new change order replaces the standing one
                        check_order = None
                elif check_order and ev.get("action") in actions.CHECKS:
                    ordered_by, trigger = check_order["user_id"], (
                        "secret_submitted" if meta_trigger == "secret_submitted" else "application_owner_confirmation")
                elif check_order and ev.get("action") == "apply_application_secret" \
                        and meta_trigger == "secret_submitted":
                    ordered_by, trigger = check_order["user_id"], "secret_submitted"
                else:
                    log.warning("dropped action %s from an application owner turn without a standing order",
                                ev.get("action"))
                    continue
                record, new = await actions.record(session_id, tenant["_id"], ordered_by=ordered_by, turn_id=turn_id,
                                                   event=ev, trigger=trigger, order_message_id=message["_id"])
                await events.emit(session_id, "action.recorded" if new else "action.updated",
                                  await actions.public(record), visible_to=events.for_role("iam_engineer"))
                await _after_action(session_id, session, ev, thread, turn_id)
            elif kind == "diagnosis":
                noted = await actions.set_diagnosis(turn_id, ev.get("text", ""))
                if noted:
                    await events.emit(session_id, "action.updated", await actions.public(noted),
                                      visible_to=events.for_role("iam_engineer"))
            elif kind == "source":
                source = {"id": ev["id"], "name": ev["name"]} if ev.get("id") else None  # empty = deleted
                adopted = bool(source and ev.get("adopted") and role == "iam_engineer")
                await repo.set_source(session_id, source, adopted=adopted)
                await events.emit(session_id, "session.updated", {"source": source and {**source, "adopted": adopted}})
                if adopted:  # FR-105: extend-source; the steps only a new source needs are skipped
                    current = await repo.get(session_id) or {}
                    await _store_plan(session_id, plans.skip_on_extend(current.get("plan") or []))
                    await events.emit(session_id, "session.mode", {"mode": "extend", "source": source})
            elif kind == "detail":
                # Only the Application (client) ID is accepted from the agent (it read it in the owner's output).
                value = str(ev.get("value") or "").strip()
                if ev.get("name") == "client_id" and secrets.GUID.match(value):
                    await db().sessions.update_one({"_id": session_id}, {"$set": {"details.client_id": value.lower()}})
                    await events.emit(session_id, "session.updated", {"details": {"client_id": value.lower()}})
            elif kind == "secret_needed":
                reason = mask_text(" ".join(str(ev.get("reason") or "").split()))[:160]
                await repo.set_hint(session_id, "application_owner", "waiting_for_secret")
                await events.emit(session_id, "secret.needed", {"reason": reason})
                if (message.get("meta") or {}).get("secret_exposed"):
                    continue  # the exposed-secret note already says it (research R17): no second note
                why = reason or "the current one no longer works"
                note = await messages.add(session_id, speaker="agent", thread="application_owner",
                                          kind="system_note", tone="danger", code="secret_needed",
                                          text=f"A new client secret is needed: {why}. Create one in Entra and put "
                                               "its Value in the secret field.")
                await _emit_message(session_id, note, names)
            elif kind == "other_thread":
                # FR-006c: a message for the other participant goes in their thread, with a relay note in the
                # writer's thread. Without text it is a one-line note for the other thread (news for both).
                relay_note = (ev.get("relay_note") or "").strip()
                text = (ev.get("text") or "").strip()
                posted_other = posted_other or bool(text or relay_note)
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
            elif kind == "usage":
                record_usage(ev, turn_id=turn_id)  # never sent to the browser
            elif kind == "error":
                await _fail(session_id, message, turn_id, ev.get("message") or "The agent reported an error.")
                return
        if not reply_text and posted_other:
            other_user = await db().users.find_one({"_id": session.get(f"{other}_id")}, projection={"display_name": 1})
            reply_text = status.passed_on_text((other_user or {}).get("display_name") or "the other participant")
        if not reply_text:
            await _fail(session_id, message, turn_id, "The agent returned no reply.")
            return
        await messages.set_reply(reply_doc["_id"], text=reply_text, reply_state="answered", status_text=None)
        reply = await db().messages.find_one({"_id": reply_doc["_id"]})
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
        await refresh_waiting_replies(session_id)
        await refresh_suggestions(session_id, agent_suggestions)
    finally:
        turn_id_var.reset(token)


async def _fail(session_id: ObjectId, message: dict, turn_id: str, reason: str) -> None:
    """Re-queue once, then answer with an error reply in the writer's thread (contracts/live-events.md `turn.failed`)."""
    await events.emit(session_id, "turn.failed", {"turn_id": turn_id, "reason": reason})
    attempts = (message.get("meta") or {}).get("attempts", 0) + 1
    reply = await messages.reply_for(message["_id"])
    if attempts < 2:
        await db().messages.update_one({"_id": message["_id"]},
                                       {"$set": {"queue_state": "queued", "meta.attempts": attempts}})
        await events.emit(session_id, "message.queue", {"message_id": str(message["_id"]), "queue_state": "queued"})
        if reply:
            ahead, text = await queue_status(session_id, message)
            updated = await messages.set_reply(reply["_id"], reply_state="received", ahead=ahead, status_text=text)
            if updated:
                await _reply_status(session_id, updated)
        return
    text = f"I couldn't finish that: {reason} Please try again in a moment."
    if reply:
        reply = await messages.set_reply(reply["_id"], text=text, reply_state="failed", status_text=None)
    else:
        reply = await messages.add(session_id, speaker="agent", thread=message.get("thread") or message["speaker"],
                                   turn_id=turn_id, text=text, reply_state="failed")
    await _reply_status(session_id, reply)  # type: ignore[arg-type]
    await events.emit(session_id, "agent.message", mask_obj(await messages.public(reply, {})))  # type: ignore[arg-type]
    await messages.set_queue_state(message["_id"], "answered")
    await events.emit(session_id, "message.queue", {"message_id": str(message["_id"]), "queue_state": "answered"})
