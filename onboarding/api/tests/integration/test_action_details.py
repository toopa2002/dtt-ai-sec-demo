"""T159: SailPoint action details (FR-020, FR-020a, SC-017; research R24): IAM engineer only, request/response/
diagnosis/context, running → final as one record, and the events never reach the application owner."""

import asyncio

import pytest
from bson import ObjectId

from onboarding_api.chat import events
from onboarding_api.db import db

from .test_threads import _both, _session, _settled

pytestmark = pytest.mark.usefixtures("setup_db")


async def test_action_details_are_for_the_iam_engineer(fake_store, isc, fake_agent) -> None:  # type: ignore[no-untyped-def]
    a, b, sid, _ = await _session(fake_store, isc, "actions-1")
    async with _both(a, b):
        fake_agent.script.append([
            {"type": "action", "action_ref": "a1", "action": "aggregate", "result": "running",
             "request": {"accounts": True}, "started_at": "2026-10-08T03:00:00+00:00"},
            {"type": "action", "action_ref": "a1", "action": "aggregate", "result": "ok", "duration_ms": 5100,
             "request": {"accounts": True},
             "response": {"task_ids": ["acct-1"], "task_states": {"acct-1": "SUCCESS"}, "counts": {"accounts": 12}}},
            {"type": "action", "action_ref": "a2", "action": "test_connection", "result": "failed",
             "response": {"error": "req.input is null\nmore"}},
            {"type": "diagnosis", "text": "Test Connection needs a first aggregation; nothing is wrong."},
            {"type": "final", "text": "Done."}])
        order = (await a.post(f"/onboarding/api/sessions/{sid}/messages", json={"text": "Run the checks"})).json()
        await _settled(sid)

        assert (await b.get(f"/onboarding/api/sessions/{sid}/actions")).status_code == 403
        listed = (await a.get(f"/onboarding/api/sessions/{sid}/actions")).json()
        assert [x["action"] for x in listed] == ["aggregate", "test_connection"]  # running + final = one record
        agg, test = listed
        assert agg["result"] == "ok" and agg["outcome"] == "completed · 12 accounts" and agg["duration_ms"] == 5100
        assert test["outcome"] == "failed · req.input is null"
        detail = (await a.get(f"/onboarding/api/sessions/{sid}/actions/{test['id']}")).json()
        assert detail["diagnosis"].startswith("Test Connection needs") and detail["order_message_id"] == order["id"]
        assert detail["request_missing"] is True and detail["response_missing"] is False
        assert (await b.get(f"/onboarding/api/sessions/{sid}/actions/{test['id']}")).status_code == 403
        assert (await a.get(f"/onboarding/api/sessions/{sid}/actions/{'0' * 24}")).status_code == 404

        evs = [e async for e in db().events.find({"session_id": ObjectId(sid), "type": {"$regex": "^action\\."}})]
        assert [e["type"] for e in evs] == ["action.recorded", "action.updated", "action.recorded", "action.updated"]
        assert all(e["visible_to"] == "role:iam_engineer" for e in evs)
        # The owner's long-poll and the IAM engineer's: the action events reach only the IAM engineer.
        owner_events = (await b.get(f"/onboarding/api/sessions/{sid}/events/poll", params={"after": 0})).json()["events"]
        iam_events = (await a.get(f"/onboarding/api/sessions/{sid}/events/poll", params={"after": 0})).json()["events"]
        assert not any(e["type"].startswith("action.") for e in owner_events)
        assert sum(e["type"].startswith("action.") for e in iam_events) == 4


def test_role_visibility() -> None:
    ev = {"visible_to": "role:iam_engineer"}
    uid = ObjectId()
    assert events.visible(ev, uid, "iam_engineer") and not events.visible(ev, uid, "application_owner")
    assert events.visible({"visible_to": str(uid)}, uid, "application_owner")
    assert events.visible({"visible_to": "both"}, uid, None)
    _ = asyncio  # keep the import for the async tests above
