"""T093: threads, relay notes, the waiting marker and the standing check order through the API
(FR-006–FR-006c, FR-016a, SC-010; research R15, R16)."""

import asyncio
import contextlib

from bson import ObjectId

from onboarding_api.db import db

from ..conftest import make_client, new_user, sign_in
from .test_foundation import AWS_DETAILS, _tenant, _wait_answered


async def _session(fake_store, isc, name: str):  # type: ignore[no-untyped-def]
    tenant_id = await _tenant(fake_store, isc, name)
    iam, iam_pw = await new_user("iam_engineer")
    owner, owner_pw = await new_user("application_owner")
    owner_doc = await db().users.find_one({"username": owner})
    a, b = await make_client(), await make_client()
    await sign_in(a, iam, iam_pw)
    await sign_in(b, owner, owner_pw)
    sid = (await a.post("/onboarding/api/sessions", json={
        "connector_type": "aws-saas", "tenant_id": tenant_id, "details": AWS_DETAILS,
        "application_owner_id": str(owner_doc["_id"])})).json()["id"]
    return a, b, sid, iam


@contextlib.asynccontextmanager
async def _both(a, b):  # type: ignore[no-untyped-def]
    """The clients were opened by sign_in; this only closes them afterwards."""
    try:
        yield
    finally:
        await a.aclose()
        await b.aclose()


async def _settled(sid: str, timeout: float = 5.0) -> None:
    """After the answered mark the worker still refreshes suggestions and releases the lock: wait for that too."""
    await _wait_answered(ObjectId(sid))
    for _ in range(int(timeout / 0.05)):
        s = await db().sessions.find_one({"_id": ObjectId(sid)}, projection={"turn_lock": 1})
        queued = await db().messages.count_documents({"session_id": ObjectId(sid),
                                                      "queue_state": {"$in": ["queued", "processing"]}})
        if s and not s.get("turn_lock") and not queued:
            return
        await asyncio.sleep(0.05)
    raise AssertionError("turn did not settle")


async def _messages(c, sid: str) -> list[dict]:  # type: ignore[no-untyped-def]
    return (await c.get(f"/onboarding/api/sessions/{sid}/messages")).json()


async def test_a_message_always_lands_in_the_callers_thread(fake_store, isc, fake_agent) -> None:  # type: ignore[no-untyped-def]
    a, b, sid, _ = await _session(fake_store, isc, "threads-1")
    async with _both(a, b):
        fake_agent.script += [[{"type": "final", "text": "hi iam"}], [{"type": "final", "text": "hi owner"}]]
        # An extra `thread` field in the body is ignored: there is no way to post into the other thread.
        r = await a.post(f"/onboarding/api/sessions/{sid}/messages", json={"text": "hello", "thread": "application_owner"})
        assert r.status_code == 202 and r.json()["thread"] == "iam_engineer" and r.json()["kind"] == "message"
        await _settled(sid)
        r = await b.post(f"/onboarding/api/sessions/{sid}/messages", json={"text": "hello", "thread": "iam_engineer"})
        assert r.json()["thread"] == "application_owner"
        await _settled(sid)
        msgs = await _messages(a, sid)
        assert [(m["speaker"], m["thread"]) for m in msgs] == [
            ("iam_engineer", "iam_engineer"), ("agent", "iam_engineer"),
            ("application_owner", "application_owner"), ("agent", "application_owner")]
        # the agent saw both threads, tagged
        history = fake_agent.calls[-1]["history"]
        assert {h["thread"] for h in history} == {"iam_engineer"}
        assert fake_agent.calls[-1]["message"]["thread"] == "application_owner"
        assert all("addressed_to" not in m for m in msgs)


async def test_other_thread_event_posts_a_message_and_a_paired_relay_note(fake_store, isc, fake_agent) -> None:  # type: ignore[no-untyped-def]
    a, b, sid, _ = await _session(fake_store, isc, "threads-2")
    async with _both(a, b):
        fake_agent.script.append([
            {"type": "other_thread", "text": "Please run `aws iam get-role` and paste the output.",
             "relay_note": "Asked the AWS owner to run the read-only get-role check."},
            {"type": "other_thread", "text": "Also: which region are you in?"},          # no relay note: generic
            {"type": "other_thread", "relay_note": "The connection check failed; see the IAM engineer's thread."},
            {"type": "final", "text": "The connection check failed on the AWS side."},
        ])
        await a.post(f"/onboarding/api/sessions/{sid}/messages", json={"text": "Rerun the connection check"})
        await _settled(sid)
        msgs = await _messages(b, sid)
        owner_thread = [m for m in msgs if m["thread"] == "application_owner"]
        iam_thread = [m for m in msgs if m["thread"] == "iam_engineer"]
        assert [(m["kind"], m["text"][:12]) for m in owner_thread] == [
            ("message", "Please run `"), ("message", "Also: which "), ("relay_note", "The connecti")]
        notes = [m for m in iam_thread if m["kind"] == "relay_note"]
        assert [n["text"] for n in notes] == ["Asked the AWS owner to run the read-only get-role check.",
                                              "Posted a message in the application owner's thread."]
        assert notes[0]["relay_ref"] == owner_thread[0]["id"] and notes[1]["relay_ref"] == owner_thread[1]["id"]
        assert iam_thread[-1]["text"].startswith("The connection check failed") and iam_thread[-1]["kind"] == "message"
        created = [e async for e in db().events.find({"session_id": ObjectId(sid), "type": "message.created"})]
        assert len(created) == 1 + 2 + 2 + 1  # the order + (message, note) + (message, generic note) + owner note
        assert all(e["visible_to"] == "both" for e in created)


async def test_waiting_is_information_only(fake_store, isc, fake_agent) -> None:  # type: ignore[no-untyped-def]
    a, b, sid, _ = await _session(fake_store, isc, "threads-3")
    async with _both(a, b):
        fake_agent.script += [[{"type": "waiting", "on": "application_owner"}, {"type": "final", "text": "Waiting."}],
                              [{"type": "final", "text": "Answered at once."}]]
        await a.post(f"/onboarding/api/sessions/{sid}/messages", json={"text": "Get the owner started"})
        await _settled(sid)
        detail = (await a.get(f"/onboarding/api/sessions/{sid}")).json()
        assert detail["waiting_on"] == "application_owner"
        assert fake_agent.calls[-1]["waiting_on"] is None  # the first turn started before it was set
        # The IAM engineer writes again while the agent waits for the owner: answered, not held.
        await a.post(f"/onboarding/api/sessions/{sid}/messages", json={"text": "What is left to do?"})
        await _settled(sid)
        msgs = await _messages(a, sid)
        assert msgs[-1]["text"] == "Answered at once." and msgs[-2]["queue_state"] == "answered"
        assert fake_agent.calls[-1]["waiting_on"] == "application_owner"
        waiting = [e async for e in db().events.find({"session_id": ObjectId(sid), "type": "thread.waiting"})]
        assert waiting and waiting[-1]["payload"] == {"waiting_on": "application_owner", "reason": None}


async def test_waiting_reason_is_cleaned_shown_and_cleared(fake_store, isc, fake_agent) -> None:  # type: ignore[no-untyped-def]
    """T123: the waiting banner's reason (FR-006g, research R20)."""
    a, b, sid, _ = await _session(fake_store, isc, "threads-wait")
    async with _both(a, b):
        long_reason = "run step 4\nand paste the output " + "x" * 300
        fake_agent.script += [
            [{"type": "waiting", "on": "application_owner", "reason": "use key AKIAIOSFODNN7EXAMPLE then confirm"},
             {"type": "final", "text": "Asked the owner."}],
            [{"type": "waiting", "on": "application_owner", "reason": long_reason},
             {"type": "final", "text": "Asked again."}],
            [{"type": "final", "text": "Thanks, I have what I need."}],  # the owner answers: the wait ends
        ]
        await a.post(f"/onboarding/api/sessions/{sid}/messages", json={"text": "Get the owner started"})
        await _settled(sid)
        detail = (await a.get(f"/onboarding/api/sessions/{sid}")).json()
        assert detail["waiting_on"] == "application_owner"
        assert "AKIAIOSFODNN7EXAMPLE" not in detail["waiting_reason"] and "[masked]" in detail["waiting_reason"]

        await a.post(f"/onboarding/api/sessions/{sid}/messages", json={"text": "Ask again"})
        await _settled(sid)
        detail = (await b.get(f"/onboarding/api/sessions/{sid}")).json()
        reason = detail["waiting_reason"]
        assert "\n" not in reason and len(reason) == 120 and reason.startswith("run step 4 and paste the output")
        assert fake_agent.calls[-1]["waiting_reason"].startswith("use key")  # the agent sees the stored reason

        await b.post(f"/onboarding/api/sessions/{sid}/messages", json={"text": "Done, here is the output"})
        await _settled(sid)
        detail = (await a.get(f"/onboarding/api/sessions/{sid}")).json()
        assert detail["waiting_on"] is None and detail["waiting_reason"] is None
        waiting = [e["payload"] async for e in db().events.find({"session_id": ObjectId(sid), "type": "thread.waiting"})]
        assert waiting[-1] == {"waiting_on": None, "reason": None}
        assert waiting[1]["reason"] == reason


async def test_clearing_the_wait_drops_the_reason(fake_store, isc, fake_agent) -> None:  # type: ignore[no-untyped-def]
    a, b, sid, _ = await _session(fake_store, isc, "threads-wait-2")
    async with _both(a, b):
        fake_agent.script += [
            [{"type": "waiting", "on": "application_owner", "reason": "run step 1"}, {"type": "final", "text": "ok"}],
            [{"type": "waiting", "on": None, "reason": "ignored"}, {"type": "final", "text": "Not waiting now."}],
        ]
        await a.post(f"/onboarding/api/sessions/{sid}/messages", json={"text": "Start"})
        await _settled(sid)
        await a.post(f"/onboarding/api/sessions/{sid}/messages", json={"text": "Never mind"})
        await _settled(sid)
        detail = (await a.get(f"/onboarding/api/sessions/{sid}")).json()
        assert detail["waiting_on"] is None and detail["waiting_reason"] is None


async def test_standing_check_order_lets_the_owner_confirmation_rerun_checks(fake_store, isc, fake_agent) -> None:  # type: ignore[no-untyped-def]
    a, b, sid, iam = await _session(fake_store, isc, "threads-4")
    async with _both(a, b):
        # Without a standing order, an owner turn's action is dropped (the agent must not have offered the tool).
        fake_agent.script.append([
            {"type": "action", "action": "connection_check", "result": "ok"}, {"type": "final", "text": "no"}])
        await b.post(f"/onboarding/api/sessions/{sid}/messages", json={"text": "run the check"})
        await _settled(sid)
        assert (await a.get(f"/onboarding/api/sessions/{sid}/actions")).json() == []
        assert fake_agent.calls[-1]["check_order"] is None

        # The IAM engineer orders the checks; the connection check fails: the order now stands.
        fake_agent.script.append([
            {"type": "source", "id": "2c91808a", "name": "AWS - Acme Org"},
            {"type": "action", "action": "create_source", "result": "ok"},
            {"type": "set_step", "step": "connection_check", "state": "failed"},
            {"type": "action", "action": "connection_check", "result": "failed", "error": "AssumeRole denied"},
            {"type": "waiting", "on": "application_owner"},
            {"type": "final", "text": "**Side: AWS** the trust rejects SailPoint."}])
        await a.post(f"/onboarding/api/sessions/{sid}/messages", json={"text": "Create the connector and run the checks"})
        await _settled(sid)
        detail = (await a.get(f"/onboarding/api/sessions/{sid}")).json()
        assert detail["check_order"]["display_name"] == iam.title()

        # A question from the IAM engineer does not replace the standing order (only a new change order does).
        fake_agent.script.append([{"type": "final", "text": "The AWS owner is fixing the trust."}])
        await a.post(f"/onboarding/api/sessions/{sid}/messages", json={"text": "What is left to do?"})
        await _settled(sid)
        assert (await a.get(f"/onboarding/api/sessions/{sid}")).json()["check_order"] is not None

        # The owner confirms: the agent reruns the checks; records name the IAM engineer with the confirmation trigger.
        fake_agent.script.append([
            {"type": "set_step", "step": "connection_check", "state": "passed"},
            {"type": "action", "action": "connection_check", "result": "ok"},
            {"type": "set_step", "step": "aggregation", "state": "passed"},
            {"type": "action", "action": "aggregate", "result": "ok"},
            {"type": "set_step", "step": "test_connection", "state": "passed"},
            {"type": "action", "action": "test_connection", "result": "ok"},
            {"type": "action", "action": "configure_source", "result": "ok"},   # not a check: dropped
            {"type": "other_thread", "text": "All three checks passed.", "relay_note": "Told the IAM engineer."},
            {"type": "final", "text": "Thanks, the checks pass now."}])
        await b.post(f"/onboarding/api/sessions/{sid}/messages", json={"text": "Done, I updated the trust"})
        await _settled(sid)
        assert fake_agent.calls[-1]["check_order"]["display_name"] == iam.title()
        actions = (await a.get(f"/onboarding/api/sessions/{sid}/actions")).json()
        reruns = [x for x in actions if x["trigger"] == "application_owner_confirmation"]
        assert [x["action"] for x in reruns] == ["connection_check", "aggregate", "test_connection"]
        assert all(x["ordered_by"]["display_name"] == iam.title() for x in actions)
        assert not any(x["action"] == "configure_source" and x["trigger"] != "order" for x in actions)
        detail = (await a.get(f"/onboarding/api/sessions/{sid}")).json()
        assert detail["check_order"] is None  # cleared: all checks passed
        assert detail["steps"]["test_connection"] == "passed"


async def test_suggestions_endpoint_is_per_thread(fake_store, isc, fake_agent) -> None:  # type: ignore[no-untyped-def]
    a, b, sid, _ = await _session(fake_store, isc, "threads-5")
    async with _both(a, b):
        before = (await b.get(f"/onboarding/api/sessions/{sid}/suggestions")).json()
        assert before["thread"] == "application_owner" and 3 <= len(before["items"]) <= 5
        assert all(i["kind"] != "order" for i in before["items"])
        iam_before = (await a.get(f"/onboarding/api/sessions/{sid}/suggestions")).json()
        assert any(i["text"] == "Create the connector and run the checks" for i in iam_before["items"])
        fake_agent.script.append([
            {"type": "suggestions", "thread": "application_owner", "items": [
                {"text": "Here is the output:", "kind": "answer"}, {"text": "Create the connector", "kind": "order"}]},
            {"type": "waiting", "on": "application_owner"},
            {"type": "final", "text": "Run step 1 and paste the output."}])
        await b.post(f"/onboarding/api/sessions/{sid}/messages", json={"text": "What first?"})
        await _settled(sid)
        after = (await b.get(f"/onboarding/api/sessions/{sid}/suggestions")).json()
        assert after["items"][0] == {"text": "Here is the output:", "kind": "answer"}
        assert all(i["kind"] != "order" for i in after["items"]) and 3 <= len(after["items"]) <= 5
        updates = [e async for e in db().events.find({"session_id": ObjectId(sid), "type": "suggestions.updated"})]
        assert {e["payload"]["thread"] for e in updates} == {"iam_engineer", "application_owner"}
        assert all(e["visible_to"] != "both" for e in updates)
