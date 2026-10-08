"""Phase 2 checkpoint: an admin signs in, registers a tenant (credential only in the credential store), creates a
session; a message reaches the agent and the streamed reply, step changes and action records land (FR-001–FR-008,
FR-020, FR-025, FR-026)."""

import asyncio

import pytest

from onboarding_api.db import db
from onboarding_api.tenants import identity

from ..conftest import EXTERNAL_ID, PAT_ID, PAT_SECRET, make_client, new_user, sign_in

pytestmark = pytest.mark.usefixtures("setup_db")

AWS_DETAILS = {
    "source_name": "AWS - Acme Org",
    "source_owner": "w.rakkiatngam",
    "management_account_id": "111122223333",
    "accounts": "111122223333, 444455556666",
    "region": "ap-southeast-1",
    "agentcore_regions": ["ap-southeast-1"],
}


async def _wait_answered(session_id, timeout: float = 5.0) -> None:  # type: ignore[no-untyped-def]
    for _ in range(int(timeout / 0.05)):
        if not await db().messages.find_one({"session_id": session_id, "queue_state": {"$in": ["queued", "processing"]}}):
            return
        await asyncio.sleep(0.05)
    raise AssertionError("turn did not finish")


async def test_lockout_after_five_failures() -> None:
    name, _ = await new_user("application_owner")
    async with await make_client() as c:
        for attempt in range(1, 5):
            r = await c.post("/onboarding/api/auth/login", json={"username": name, "password": "wrong password!"})
            assert r.status_code == 401
            assert f"{5 - attempt} attempt" in r.json()["message"]
        r = await c.post("/onboarding/api/auth/login", json={"username": name, "password": "wrong password!"})
        assert r.status_code == 423
        assert r.json()["code"] == "account_locked"
        r = await c.post("/onboarding/api/auth/login", json={"username": name, "password": "correct horse battery"})
        assert r.status_code == 423
    assert await db().audit.count_documents({"kind": "locked"}) >= 1


async def test_admin_flag_only_for_iam_engineers() -> None:
    admin, pw = await new_user("iam_engineer", is_admin=True)
    async with await make_client() as c:
        await sign_in(c, admin, pw)
        r = await c.post("/onboarding/api/admin/users", json={
            "username": "owner.admin", "display_name": "Owner", "role": "application_owner", "is_admin": True,
            "initial_password": "long enough password"})
        assert r.status_code == 422
        r = await c.post("/onboarding/api/admin/users", json={
            "username": "owner.ok", "display_name": "Owner", "role": "application_owner",
            "initial_password": "long enough password"})
        assert r.status_code == 201
        assert "password_hash" not in r.json()


async def test_tenant_secret_never_stored_or_returned(fake_store, isc) -> None:  # type: ignore[no-untyped-def]
    admin, pw = await new_user("iam_engineer", is_admin=True)
    async with await make_client() as c:
        await sign_in(c, admin, pw)
        swapped = await c.post("/onboarding/api/admin/tenants", json={
            "name": "swap", "api_host": "acme-demo.api.identitynow-demo.com",
            "client_id": PAT_SECRET, "client_secret": PAT_ID})
        assert swapped.status_code == 422
        assert "swapped" in swapped.json()["message"]
        r = await c.post("/onboarding/api/admin/tenants", json={
            "name": "acme-demo-1", "api_host": "acme-demo.identitynow-demo.com",
            "client_id": PAT_ID, "client_secret": PAT_SECRET})
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["status"] == "usable"
        assert body["api_host"] == "acme-demo.api.identitynow-demo.com"
        assert body["credential_hint"] == PAT_ID[-4:]
        assert body["external_id"] == EXTERNAL_ID
        assert PAT_SECRET not in r.text
    stored = await db().tenants.find_one({"name": "acme-demo-1"})
    assert PAT_SECRET not in str(stored)
    assert fake_store.saved[stored["credential_provider"]] == (PAT_ID, PAT_SECRET)


async def test_refused_credential_store_is_a_clear_502(fake_store, isc, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    def refuse(*_):  # type: ignore[no-untyped-def]
        raise identity.CredentialStoreError("AgentCore Identity refused the credential: AccessDeniedException")

    monkeypatch.setattr(fake_store, "put", refuse)
    admin, pw = await new_user("iam_engineer", is_admin=True)
    async with await make_client() as c:
        await sign_in(c, admin, pw)
        r = await c.post("/onboarding/api/admin/tenants", json={
            "name": "acme-refused", "api_host": "acme-demo.api.identitynow-demo.com",
            "client_id": PAT_ID, "client_secret": PAT_SECRET})
        assert r.status_code == 502, r.text
        assert r.json()["code"] == "credential_store_failed"
        assert "AccessDeniedException" in r.json()["message"]
        assert PAT_SECRET not in r.text
        assert all(t["name"] != "acme-refused" for t in (await c.get("/onboarding/api/admin/tenants")).json())


async def _tenant(fake_store, isc, name: str) -> str:  # type: ignore[no-untyped-def]
    admin, pw = await new_user("iam_engineer", is_admin=True)
    async with await make_client() as c:
        await sign_in(c, admin, pw)
        r = await c.post("/onboarding/api/admin/tenants", json={
            "name": name, "api_host": "acme-demo.api.identitynow-demo.com",
            "client_id": PAT_ID, "client_secret": PAT_SECRET})
        return r.json()["id"]


async def test_session_turn_end_to_end(fake_store, isc, fake_agent) -> None:  # type: ignore[no-untyped-def]
    tenant_id = await _tenant(fake_store, isc, "acme-demo-2")
    iam, iam_pw = await new_user("iam_engineer")
    owner, owner_pw = await new_user("application_owner")
    owner_doc = await db().users.find_one({"username": owner})
    async with await make_client() as a, await make_client() as b:
        await sign_in(a, iam, iam_pw)
        await sign_in(b, owner, owner_pw)

        planned = await a.post("/onboarding/api/sessions", json={
            "connector_type": "okta", "tenant_id": tenant_id, "details": {}})
        assert planned.status_code == 409 and planned.json()["code"] == "connector_planned"

        forbidden = await b.post("/onboarding/api/sessions", json={
            "connector_type": "aws-saas", "tenant_id": tenant_id, "details": AWS_DETAILS})
        assert forbidden.status_code == 403

        bad = await a.post("/onboarding/api/sessions", json={
            "connector_type": "aws-saas", "tenant_id": tenant_id, "details": AWS_DETAILS | {"accounts": "12345"}})
        assert bad.status_code == 422 and "accounts" in bad.json()["message"]

        r = await a.post("/onboarding/api/sessions", json={
            "connector_type": "aws-saas", "tenant_id": tenant_id, "details": AWS_DETAILS,
            "application_owner_id": str(owner_doc["_id"])})
        assert r.status_code == 201, r.text
        session = r.json()
        sid = session["id"]
        assert session["details"]["role_name"] == "SailPointISCRole-acme-demo-2"
        assert session["details"]["accounts"] == ["111122223333", "444455556666"]

        fake_agent.script.append([
            {"type": "progress", "text": "creating the source in SailPoint…"},
            {"type": "delta", "text": "On it. "},
            {"type": "set_step", "step": "source_created", "state": "passed"},
            {"type": "source", "id": "2c91808a", "name": "AWS - Acme Org"},
            {"type": "action", "action": "create_source", "source": {"id": "2c91808a", "name": "AWS - Acme Org"},
             "result": "ok", "request_summary": {"name": "AWS - Acme Org"}, "task_ids": []},
            {"type": "set_step", "step": "source_created", "state": "not_started"},  # illegal: must be rejected
            {"type": "final", "text": "Created AWS - Acme Org. The key AKIAIOSFODNN7EXAMPLE is masked."},
        ])
        r = await a.post(f"/onboarding/api/sessions/{sid}/messages",
                         json={"text": "Create the connector. My key is AKIAIOSFODNN7EXAMPLE"})
        assert r.status_code == 202
        assert "AKIAIOSFODNN7EXAMPLE" not in r.json()["text"]

        from bson import ObjectId
        await _wait_answered(ObjectId(sid))

        payload = fake_agent.calls[-1]
        assert payload["ordered_by"]["role"] == "iam_engineer"
        assert "AKIAIOSFODNN7EXAMPLE" not in str(payload)
        assert "client_secret" not in str(payload) and PAT_SECRET not in str(payload)

        msgs = (await b.get(f"/onboarding/api/sessions/{sid}/messages")).json()
        assert [m["speaker"] for m in msgs] == ["iam_engineer", "agent"]
        assert msgs[0]["queue_state"] == "answered"
        assert msgs[1]["thread"] == "iam_engineer" and msgs[1]["kind"] == "message"
        assert "AKIAIOSFODNN7EXAMPLE" not in msgs[1]["text"]

        actions = (await b.get(f"/onboarding/api/sessions/{sid}/actions")).json()
        assert len(actions) == 1
        assert actions[0]["ordered_by"]["display_name"] == iam.title()

        detail = (await a.get(f"/onboarding/api/sessions/{sid}")).json()
        assert detail["steps"]["source_created"] == "passed"
        assert detail["source"] == {"id": "2c91808a", "name": "AWS - Acme Org"}

        types = [e["type"] async for e in db().events.find({"session_id": ObjectId(sid)}, sort=[("event_id", 1)])]
        assert types[:2] == ["message.created", "message.queue"]
        for expected in ("agent.progress", "agent.delta", "step.changed", "session.updated", "action.recorded",
                         "agent.message"):
            assert expected in types
        assert types.count("step.changed") == 1
        # the turn ends with the answered mark, then each participant's refreshed suggestions (FR-006e)
        assert types[-3:] == ["message.queue", "suggestions.updated", "suggestions.updated"]

        stranger, spw = await new_user("application_owner")
        async with await make_client() as c:
            await sign_in(c, stranger, spw)
            assert (await c.get(f"/onboarding/api/sessions/{sid}")).status_code == 404


async def test_messages_are_answered_in_arrival_order(fake_store, isc, fake_agent) -> None:  # type: ignore[no-untyped-def]
    tenant_id = await _tenant(fake_store, isc, "acme-demo-3")
    iam, iam_pw = await new_user("iam_engineer")
    owner, owner_pw = await new_user("application_owner")
    owner_doc = await db().users.find_one({"username": owner})
    async with await make_client() as a, await make_client() as b:
        await sign_in(a, iam, iam_pw)
        await sign_in(b, owner, owner_pw)
        sid = (await a.post("/onboarding/api/sessions", json={
            "connector_type": "aws-saas", "tenant_id": tenant_id, "details": AWS_DETAILS,
            "application_owner_id": str(owner_doc["_id"])})).json()["id"]
        fake_agent.script += [[{"type": "final", "text": "first"}], [{"type": "final", "text": "second"}]]
        r1, r2 = await asyncio.gather(
            a.post(f"/onboarding/api/sessions/{sid}/messages", json={"text": "from the IAM engineer"}),
            b.post(f"/onboarding/api/sessions/{sid}/messages", json={"text": "from the owner"}))
        assert r1.status_code == r2.status_code == 202
        from bson import ObjectId
        await _wait_answered(ObjectId(sid))
        msgs = (await a.get(f"/onboarding/api/sessions/{sid}/messages")).json()
        seqs = [m["seq"] for m in msgs]
        assert seqs == sorted(seqs)
        speakers = [m["speaker"] for m in msgs]
        assert speakers.count("agent") == 2
        # one turn at a time, in arrival order: the agent saw seq 1 before seq 2, and the replies follow that order
        turns = [c["message"]["seq"] for c in fake_agent.calls if c.get("mode") == "turn"][-2:]
        assert turns == sorted(turns)
        assert [m["text"] for m in msgs if m["speaker"] == "agent"] == ["first", "second"]
        # the second turn saw the first exchange in its history
        assert any(h["text"] == "first" for h in fake_agent.calls[-1]["history"])


async def test_held_screenshot_is_visible_only_to_uploader(fake_store, isc, fake_agent) -> None:  # type: ignore[no-untyped-def]
    tenant_id = await _tenant(fake_store, isc, "acme-demo-4")
    iam, iam_pw = await new_user("iam_engineer")
    owner, owner_pw = await new_user("application_owner")
    owner_doc = await db().users.find_one({"username": owner})
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    async with await make_client() as a, await make_client() as b:
        await sign_in(a, iam, iam_pw)
        await sign_in(b, owner, owner_pw)
        sid = (await a.post("/onboarding/api/sessions", json={
            "connector_type": "aws-saas", "tenant_id": tenant_id, "details": AWS_DETAILS,
            "application_owner_id": str(owner_doc["_id"])})).json()["id"]
        ok = await b.post(f"/onboarding/api/sessions/{sid}/attachments", files={"file": ("ok.png", png, "image/png")})
        held = await b.post(f"/onboarding/api/sessions/{sid}/attachments",
                            files={"file": ("x.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 3 + b"\x48\x44\x49" + b"SECRET",
                                            "image/png")})
        assert ok.status_code == held.status_code == 201
        await asyncio.sleep(0.3)
        ok_id, held_id = ok.json()["id"], held.json()["id"]
        assert (await a.get(f"/onboarding/api/sessions/{sid}/attachments/{ok_id}")).status_code == 200
        from bson import ObjectId
        held_doc = await db().attachments.find_one({"_id": ObjectId(held_id)})
        assert held_doc["secret_check"] == "held"
        assert held_doc["gridfs_id"] is None
        assert (await a.get(f"/onboarding/api/sessions/{sid}/attachments/{held_id}")).status_code == 404
        assert (await b.get(f"/onboarding/api/sessions/{sid}/attachments/{held_id}")).status_code == 200
        held_events = [e async for e in db().events.find({"session_id": ObjectId(sid), "type": "attachment.held"})]
        assert held_events and all(e["visible_to"] == str(owner_doc["_id"]) for e in held_events)
        sent = await b.post(f"/onboarding/api/sessions/{sid}/messages", json={"text": "see", "attachment_ids": [held_id]})
        assert sent.status_code == 422
        assert (await b.delete(f"/onboarding/api/sessions/{sid}/attachments/{held_id}")).status_code == 204
        big = await b.post(f"/onboarding/api/sessions/{sid}/attachments",
                           files={"file": ("big.png", png + b"\x00" * (10 * 1024 * 1024), "image/png")})
        assert big.status_code == 413
