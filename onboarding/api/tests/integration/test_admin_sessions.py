"""T163: admin reopen and handover (FR-031-FR-033, US8, SC-016; research R23)."""

from datetime import UTC, datetime

import pytest
from bson import ObjectId

from onboarding_api.chat import turns
from onboarding_api.db import db

from ..conftest import make_client, new_user, sign_in
from .test_threads import _both, _session, _settled

pytestmark = pytest.mark.usefixtures("setup_db")


async def _admin():  # type: ignore[no-untyped-def]
    name, pw = await new_user("iam_engineer", is_admin=True)
    c = await make_client()
    await sign_in(c, name, pw)
    return c


async def test_reopen(fake_store, isc, fake_agent) -> None:  # type: ignore[no-untyped-def]
    a, b, sid, _ = await _session(fake_store, isc, "admin-reopen")
    admin = await _admin()
    async with _both(a, b):
        assert (await a.get("/onboarding/api/admin/sessions")).status_code == 403  # not an admin
        assert (await admin.post(f"/onboarding/api/admin/sessions/{sid}/reopen")).status_code == 409  # still open
        await a.post(f"/onboarding/api/sessions/{sid}/messages", json={"text": "hi"})
        await _settled(sid)
        assert (await a.post(f"/onboarding/api/sessions/{sid}/finish")).status_code == 200
        assert (await b.post(f"/onboarding/api/sessions/{sid}/messages", json={"text": "x"})).status_code == 409
        listed = next(s for s in (await admin.get("/onboarding/api/admin/sessions")).json() if s["id"] == sid)
        assert listed["status"] == "finished" and listed["expires_at"] and listed["plan_total"] > 0
        assert listed["iam_engineer"]["display_name"] and listed["application_owner"]["status"] == "active"

        assert (await admin.post(f"/onboarding/api/admin/sessions/{sid}/reopen")).json() == {"reopened": True}
        s = await db().sessions.find_one({"_id": ObjectId(sid)})
        assert s["status"] == "open" and s["expires_at"] is None and s["finished_at"] is None and s["reopened_at"]
        for coll in ("messages", "events"):
            assert await db()[coll].count_documents({"session_id": ObjectId(sid), "expires_at": {"$ne": None}}) == 0
        notes = [m for m in (await a.get(f"/onboarding/api/sessions/{sid}/messages")).json() if m["kind"] == "system_note"]
        assert {n["thread"] for n in notes} == {"iam_engineer", "application_owner"} and "reopened" in notes[0]["text"]
        assert await db().audit.count_documents({"kind": "session_reopened", "target": sid}) == 1
        assert (await b.post(f"/onboarding/api/sessions/{sid}/messages", json={"text": "back"})).status_code == 202
        await _settled(sid)
    await admin.aclose()


async def test_handover(fake_store, isc, fake_agent) -> None:  # type: ignore[no-untyped-def]
    a, b, sid, iam = await _session(fake_store, isc, "admin-handover")
    admin = await _admin()
    async with _both(a, b):
        await b.post(f"/onboarding/api/sessions/{sid}/messages", json={"text": "my first question"})
        await _settled(sid)
        old_owner_id = (await db().sessions.find_one({"_id": ObjectId(sid)}))["application_owner_id"]
        new_owner, new_pw = await new_user("application_owner")
        disabled, _ = await new_user("application_owner")
        other_iam, _ = await new_user("iam_engineer")
        ids = {u["username"]: str(u["_id"]) async for u in db().users.find(
            {"username": {"$in": [new_owner, disabled, other_iam, iam]}})}
        await db().users.update_one({"username": disabled}, {"$set": {"status": "disabled"}})

        url = f"/onboarding/api/admin/sessions/{sid}/handover"
        for place, user, why in (("application_owner", disabled, "not active"),
                                 ("application_owner", other_iam, "not an application owner"),
                                 ("application_owner", iam, "not an application owner"),
                                 ("iam_engineer", iam, "already holds this place")):
            r = await admin.post(url, json={"place": place, "user_id": ids[user]})
            assert r.status_code == 422 and why in r.json()["message"], (place, user, r.json())

        r = await admin.post(url, json={"place": "application_owner", "user_id": ids[new_owner]})
        assert r.json() == {"applied": True}
        assert (await b.get(f"/onboarding/api/sessions/{sid}")).status_code == 404  # the previous owner is out
        c = await make_client()
        await sign_in(c, new_owner, new_pw)
        msgs = (await c.get(f"/onboarding/api/sessions/{sid}/messages")).json()
        first = next(m for m in msgs if m["text"] == "my first question")
        assert first["speaker_name"] and first["speaker_name"] != new_owner.title()  # old author's name kept
        assert any(m["kind"] == "system_note" and "handed the application owner's place" in m["text"] for m in msgs)
        s = await db().sessions.find_one({"_id": ObjectId(sid)})
        assert s["handovers"][0]["from_user_id"] == old_owner_id and str(s["handovers"][0]["to_user_id"]) == ids[new_owner]
        revoked = await db().events.find_one({"session_id": ObjectId(sid), "type": "access.revoked"})
        assert revoked["visible_to"] == str(old_owner_id)
        assert await db().audit.count_documents({"kind": "session_handover", "target": sid}) == 1

        # The IAM engineer's place, asked for while a turn runs: pending until the turn ends; check order cleared.
        await db().sessions.update_one({"_id": ObjectId(sid)}, {"$set": {
            "turn_lock": {"turn_id": "t_x", "since": datetime.now(UTC)},
            "check_order": {"user_id": ObjectId(), "display_name": "x", "turn_id": "t", "at": datetime.now(UTC)}}})
        r = await admin.post(url, json={"place": "iam_engineer", "user_id": ids[other_iam]})
        assert r.json() == {"applied": False}
        assert (await a.get(f"/onboarding/api/sessions/{sid}")).status_code == 200  # not yet
        await db().sessions.update_one({"_id": ObjectId(sid)}, {"$set": {"turn_lock": None}})
        await turns._after_turn(ObjectId(sid))
        s = await db().sessions.find_one({"_id": ObjectId(sid)})
        assert str(s["iam_engineer_id"]) == ids[other_iam] and s["check_order"] is None and s["pending_handover"] is None
        assert (await a.get(f"/onboarding/api/sessions/{sid}")).status_code == 404
        await c.aclose()
    await admin.aclose()
