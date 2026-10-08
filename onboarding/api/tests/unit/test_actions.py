"""T141: SailPoint action records (FR-020, FR-020a, research R24): the one-line outcome, the running → final upsert,
masking, and old records without details."""

import pytest
from bson import ObjectId

from onboarding_api.audit import actions
from onboarding_api.db import db

pytestmark = pytest.mark.usefixtures("setup_db")


def test_outcome_lines() -> None:
    assert actions.outcome({"result": "running", "action": "aggregate"}) == "running"
    assert actions.outcome({"result": "ok", "action": "connection_check",
                            "response": {"counts": {"accounts": 3}}}) == "passed · 3 accounts read"
    assert actions.outcome({"result": "ok", "action": "aggregate",
                            "response": {"counts": {"accounts": 1284, "entitlements": 40}}}) == \
        "completed · 1,284 accounts · 40 entitlements"
    assert actions.outcome({"result": "failed", "action": "connection_check",
                            "response": {"error": "not authorized to perform sts:AssumeRole\nmore detail"}}) == \
        "failed · not authorized to perform sts:AssumeRole"


async def test_running_then_final_is_one_record_and_masked() -> None:
    sid, tid, uid = ObjectId(), ObjectId(), ObjectId()
    first, new = await actions.record(sid, tid, ordered_by=uid, turn_id="t_1", event={
        "action": "aggregate", "action_ref": "a1", "result": "running", "request": {"accounts": 2},
        "started_at": "2026-10-08T03:00:00Z"})
    assert new and first["outcome"] == "running"
    final, new = await actions.record(sid, tid, ordered_by=uid, turn_id="t_1", event={
        "action": "aggregate", "action_ref": "a1", "result": "ok", "duration_ms": 4200,
        "request": {"accounts": 2, "token": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.c2lnbmF0dXJl"},
        "response": {"task_ids": ["acct-1"], "task_states": {"acct-1": "SUCCESS"}, "counts": {"accounts": 12}}})
    assert not new and final["_id"] == first["_id"]
    assert await db().actions.count_documents({"session_id": sid}) == 1
    assert final["result"] == "ok" and final["duration_ms"] == 4200 and final["outcome"].startswith("completed · 12")
    assert "eyJhbGciOiJIUzI1NiJ9" not in str(final["request"])
    failed, _ = await actions.record(sid, tid, ordered_by=uid, turn_id="t_1", event={
        "action": "connection_check", "action_ref": "a2", "result": "failed",
        "response": {"error": "denied for AKIAABCDEFGHIJKLMNOP"}})
    noted = await actions.set_diagnosis("t_1", "Cause on the AWS side: the trust. Key AKIAABCDEFGHIJKLMNOP " + "x" * 2000)
    assert noted and noted["_id"] == failed["_id"] and len(noted["diagnosis"]) <= actions.DIAGNOSIS_MAX
    assert "AKIAABCDEFGHIJKLMNOP" not in noted["diagnosis"] + str(noted["response"])


async def test_old_records_are_marked_not_recorded() -> None:
    uid = ObjectId()
    await db().users.insert_one({"_id": uid, "username": "old.iam", "display_name": "Old Iam"})
    old = {"_id": ObjectId(), "session_id": ObjectId(), "action": "connection_check", "ordered_by": uid,
           "result": "failed", "error": "denied", "task_ids": [], "at": __import__("datetime").datetime.now()}
    pub = await actions.public(old)
    assert pub["request_missing"] and pub["response_missing"] and pub["outcome"] == "failed · denied"
