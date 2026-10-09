"""Spec 002 through the session API with a scripted agent (no model, no SailPoint):
T024 (the secret field: validation, owner only, metadata only, state transitions, expiry warning), T031 (a
directory-only session: plan by capability, vault copy deleted after Test Connection, proof counts for the IAM engineer
only, SC-105), T047 (a secret pasted in the chat), T059 (long aggregations followed without a turn), T068/T073/T084
(capabilities in the plan, the tenant limitation, provisioning warning acceptance) and T087 (extend-source)."""

import asyncio
import json
from datetime import UTC, date, datetime, timedelta

import pytest
from bson import ObjectId

from onboarding_api.chat import followups
from onboarding_api.db import db
from onboarding_api.secrets import service, store

from ..conftest import make_client, new_user, sign_in
from .test_foundation import _tenant
from .test_threads import _both, _settled

SECRET = "Xy78Q~abcdefghijklmnopqrstuvwxyz0123456"
GUID = "1b7c2e94-8d3a-4f51-b6e0-2a9c7d4e1f38"
EXPIRES = (date.today() + timedelta(days=365)).isoformat()
ENTRA = {"source_name": "Entra ID - Contoso", "source_owner": "w.rakkiatngam",
         "tenant_domain": "contoso-demo.onmicrosoft.com", "capabilities": ["directory"]}


class MemoryStore(store.ApiKeyStore):
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.deleted: list[str] = []

    def put(self, name: str, value: str) -> None:
        self.values[name] = value

    def delete(self, name: str) -> None:
        self.deleted.append(name)
        self.values.pop(name, None)


@pytest.fixture
def vault(monkeypatch: pytest.MonkeyPatch) -> MemoryStore:
    mem = MemoryStore()
    monkeypatch.setattr(store, "store", lambda: mem)
    return mem


async def _entra(fake_store, isc, name: str, details: dict | None = None, accept: list[str] | None = None):  # type: ignore[no-untyped-def]
    tenant_id = await _tenant(fake_store, isc, name)
    iam, iam_pw = await new_user("iam_engineer")
    owner, owner_pw = await new_user("application_owner")
    owner_doc = await db().users.find_one({"username": owner})
    a, b = await make_client(), await make_client()
    await sign_in(a, iam, iam_pw)
    await sign_in(b, owner, owner_pw)
    r = await a.post("/onboarding/api/sessions", json={
        "connector_type": "entra-id", "tenant_id": tenant_id, "details": {**ENTRA, **(details or {})},
        "application_owner_id": str(owner_doc["_id"]), "accept_warnings": accept or []})
    return a, b, r


async def _submit(b, sid: str, value: str = SECRET, expires: str | None = EXPIRES):  # type: ignore[no-untyped-def]
    return await b.put(f"/onboarding/api/sessions/{sid}/application-secret",
                       json={"value": value, "expires_on": expires})


async def _dump(sid: ObjectId) -> str:
    """Everything stored for the session, to search for the secret (SC-102)."""
    out = []
    for coll in ("sessions", "messages", "events", "actions", "audit"):
        query = {"_id": sid} if coll == "sessions" else {"target": str(sid)} if coll == "audit" else {"session_id": sid}
        out += [json.dumps(d, default=str) async for d in db()[coll].find(query)]
    return "\n".join(out)


# ---------------------------------------------------------------- T024: the secret field
def test_secret_validation_messages() -> None:
    today = date(2026, 10, 8)
    with pytest.raises(service.SecretError) as exc:
        service.validate(GUID, "2027-10-01", today)
    assert exc.value.errors == {"value": "this is the secret's ID, not its Value"}
    with pytest.raises(service.SecretError) as exc:
        service.validate("short", None, today)
    assert exc.value.errors["value"] == "must be 16–128 characters" and "expires_on" in exc.value.errors
    with pytest.raises(service.SecretError) as exc:
        service.validate("has a space in the middle!!", "2026-10-08", today)
    assert exc.value.errors == {"value": "must not contain spaces or line breaks",
                                "expires_on": "must be in the future and at most 2 years ahead"}
    with pytest.raises(service.SecretError):
        service.validate(SECRET, "2028-12-01", today)
    assert service.validate(SECRET, "2028-10-08", today) == date(2028, 10, 8)


def test_expires_soon() -> None:
    soon = {"application_secret": {"state": "vault_deleted", "expires_on": (date.today() + timedelta(days=20)).isoformat()}}
    later = {"application_secret": {"state": "vault_deleted", "expires_on": EXPIRES}}
    assert service.status(soon)["expires_soon"] is True and service.status(later)["expires_soon"] is False


async def test_secret_field_is_owner_only_and_never_echoed(fake_store, isc, vault) -> None:  # type: ignore[no-untyped-def]
    a, b, r = await _entra(fake_store, isc, "entra-secret-1")
    async with _both(a, b):
        assert r.status_code == 201, r.text
        sid = r.json()["id"]
        assert r.json()["application_secret"] is None
        assert (await a.get(f"/onboarding/api/sessions/{sid}/application-secret")).json() == {"state": "missing"}
        denied = await _submit(a, sid)
        assert denied.status_code == 403  # the IAM engineer can't provide it
        bad = await _submit(b, sid, GUID)
        assert bad.status_code == 422 and bad.json()["errors"] == {"value": "this is the secret's ID, not its Value"}
        ok = await _submit(b, sid)
        assert ok.status_code == 200 and ok.json()["state"] == "received" and ok.json()["expires_on"] == EXPIRES
        assert SECRET not in ok.text
        assert vault.values == {f"onboarding-entra-{sid}": SECRET}
        both = [await c.get(f"/onboarding/api/sessions/{sid}") for c in (a, b)]
        assert all(x.json()["application_secret"]["state"] == "received" for x in both)
        assert all(SECRET not in x.text for x in both)
        assert SECRET not in await _dump(ObjectId(sid))
        again = await _submit(b, sid, "Ab12Q~zyxwvutsrqponmlkjihgfedcba98765432")
        assert again.json()["replaced_at"] is not None
        kinds = [d["kind"] async for d in db().audit.find({"target": sid})]
        assert kinds.count("application_secret_received") == 1 and kinds.count("application_secret_replaced") == 1


# ---------------------------------------------------------------- T031: directory-only session
async def test_directory_session_plan_proof_and_vault_deletion(fake_store, isc, vault, fake_agent) -> None:  # type: ignore[no-untyped-def]
    a, b, r = await _entra(fake_store, isc, "entra-dir-1")
    async with _both(a, b):
        session = r.json()
        sid = session["id"]
        steps = {s["id"]: s for s in session["plan"]}
        assert steps["sp_permissions"]["state"] == "skipped" and steps["sp_permissions"]["reason"] == \
            "capability not chosen"
        assert steps["provisioning_permissions"]["state"] == "skipped"
        assert steps["directory_permissions"]["state"] == "todo"
        assert session["milestone_order"][-2:] == ["test_connection", "aggregation"]
        # SC-105: a directory-only plan has no write permission and no directory role for the administrator
        live = [s for s in session["plan"] if s["state"] != "skipped" and s["actor"] == "application_owner"]
        assert not any("write" in s["title"].lower() or "User Administrator" in s["title"] for s in live)
        await _submit(b, sid)
        source = {"id": "src1", "name": "Entra ID - Contoso"}
        fake_agent.script.append([
            {"type": "source", **source},
            {"type": "action", "action_ref": "a1", "action": "configure_source", "result": "ok", "source": source,
             "request": {"fields": {"clientSecret": "[vaulted]"}}, "secret_applied": True,
             "summary": "clientSecret: [vaulted] · 13 fields"},
            {"type": "action", "action_ref": "a2", "action": "test_connection", "result": "ok", "source": source},
            {"type": "action", "action_ref": "a3", "action": "aggregate_entitlements", "result": "ok",
             "response": {"counts": {"entitlements": 2418}}},
            {"type": "action", "action_ref": "a4", "action": "aggregate_accounts", "result": "ok",
             "response": {"counts": {"accounts": 4812, "users": 4812}}, "summary": "delta off → restored · 4,812"},
            {"type": "final", "text": "Done."}])
        await a.post(f"/onboarding/api/sessions/{sid}/messages", json={"text": "Create the connector"})
        await _settled(sid)
        secret = (await a.get(f"/onboarding/api/sessions/{sid}/application-secret")).json()
        assert secret["state"] == "vault_deleted" and secret["applied_at"] and secret["vault_deleted_at"]
        assert vault.deleted == [f"onboarding-entra-{sid}"] and vault.values == {}
        iam_view = (await a.get(f"/onboarding/api/sessions/{sid}")).json()
        assert iam_view["proof"]["users"] == 4812 and iam_view["proof"]["entitlements"] == 2418
        assert "proof" not in (await b.get(f"/onboarding/api/sessions/{sid}")).json()
        acts = (await a.get(f"/onboarding/api/sessions/{sid}/actions")).json()
        assert next(x for x in acts if x["action"] == "configure_source")["summary"].startswith("clientSecret")
        assert SECRET not in await _dump(ObjectId(sid))


# ---------------------------------------------------------------- T047: secret pasted in the chat
async def test_secret_pasted_in_chat_is_masked_and_flagged(fake_store, isc, vault, fake_agent) -> None:  # type: ignore[no-untyped-def]
    a, b, r = await _entra(fake_store, isc, "entra-chat-1")
    async with _both(a, b):
        sid = r.json()["id"]
        fake_agent.script.append([{"type": "final", "text": "Please delete that secret."}])
        posted = await b.post(f"/onboarding/api/sessions/{sid}/messages", json={"text": f"here it is: {SECRET}"})
        assert SECRET not in posted.text and "[masked]" in posted.json()["text"]
        await _settled(sid)
        msgs = (await b.get(f"/onboarding/api/sessions/{sid}/messages")).json()
        note = next(m for m in msgs if m.get("code") == "secret_exposed")
        assert note["tone"] == "danger" and note["thread"] == "application_owner"
        assert note["text"] == ("A client secret was masked before it was saved, shown or sent to the agent. Treat it "
                                "as exposed: delete it in Entra and create a new one.")
        assert note["seq"] == posted.json()["seq"] + 1
        turn = fake_agent.calls[-1]
        assert turn["secret_exposed"] is True and SECRET not in json.dumps(turn)
        assert await db().audit.find_one({"kind": "secret_exposed_in_chat", "target": sid})
        sugg = (await b.get(f"/onboarding/api/sessions/{sid}/suggestions")).json()["items"]
        assert any(i["text"] == "I've put the new secret in the field" for i in sugg)
        assert SECRET not in await _dump(ObjectId(sid))


# ---------------------------------------------------------------- T068 / T073 / T084 / T081: capabilities
async def test_capabilities_drive_the_plan_and_provisioning_needs_acceptance(fake_store, isc, vault, fake_agent) -> None:  # type: ignore[no-untyped-def]
    details = {"capabilities": ["service_principals", "ai_agents", "provisioning"],
               "foundry_subscriptions": "8b1e4c2a-0d3f-4a77-9c51-6f2e8a0b3d19", "upn_domain": "contoso.example"}
    a, b, r = await _entra(fake_store, isc, "entra-caps-1", details)
    async with _both(a, b):
        assert r.status_code == 422 and r.json()["errors"] == {"warnings_accepted": "accept the provisioning warning"}
    a, b, r = await _entra(fake_store, isc, "entra-caps-2", {"capabilities": ["ai_agents"]})
    async with _both(a, b):
        assert r.status_code == 422 and r.json()["errors"]["foundry_subscriptions"].startswith("is required")
    a, b, r = await _entra(fake_store, isc, "entra-caps-3", details, accept=["provisioning"])
    async with _both(a, b):
        assert r.status_code == 201, r.text
        s = r.json()
        assert s["details"]["capabilities"] == ["directory", "service_principals", "ai_agents", "provisioning"]
        assert s["details"]["warnings_accepted"][0]["capability"] == "provisioning"
        assert all(st["state"] == "todo" for st in s["plan"] if st.get("capability"))
        assert await db().audit.find_one({"kind": "provisioning_warning_accepted", "target": s["id"]})
        # the stored acceptance goes into the agent payload: it must serialise (the fake agent json.dumps it)
        await a.post(f"/onboarding/api/sessions/{s['id']}/messages", json={"text": "hello"})
        await _settled(s["id"])
        assert fake_agent.calls and fake_agent.calls[-1]["session"]["details"]["warnings_accepted"]


async def test_tenant_limitation_blocks_the_step_and_waits_on_the_iam_engineer(fake_store, isc, vault, fake_agent) -> None:  # type: ignore[no-untyped-def]
    details = {"capabilities": ["ai_agents"], "foundry_subscriptions": "8b1e4c2a-0d3f-4a77-9c51-6f2e8a0b3d19"}
    a, b, r = await _entra(fake_store, isc, "entra-tl-1", details)
    async with _both(a, b):
        sid = r.json()["id"]
        fake_agent.script.append([
            {"type": "action", "action_ref": "a1", "action": "aggregate_datasets", "result": "tenant_limitation",
             "plan_step": "aggregate_foundry", "summary": "tenant limitation · 404 endpoint unavailable"},
            {"type": "final", "text": "Please start it in ISC."}])
        await a.post(f"/onboarding/api/sessions/{sid}/messages", json={"text": "Aggregate the AI agents"})
        await _settled(sid)
        s = (await a.get(f"/onboarding/api/sessions/{sid}")).json()
        step = next(st for st in s["plan"] if st["id"] == "aggregate_foundry")
        assert step["state"] == "blocked" and "tenant limitation" in step["reason"]
        assert s["waiting_on"] == "iam_engineer" and s["proof"]["ai_agents_state"] == "tenant_limitation"
        act = (await a.get(f"/onboarding/api/sessions/{sid}/actions")).json()[-1]
        assert act["result"] == "tenant_limitation" and act["outcome"].startswith("tenant limitation")
        sugg = (await a.get(f"/onboarding/api/sessions/{sid}/suggestions")).json()["items"]
        assert any(i["text"] == "Done, I started it in ISC" for i in sugg)


# ---------------------------------------------------------------- T087: extend-source
async def test_adopted_source_switches_to_extend_mode(fake_store, isc, vault, fake_agent) -> None:  # type: ignore[no-untyped-def]
    a, b, r = await _entra(fake_store, isc, "entra-ext-1", {"capabilities": ["service_principals"],
                                                            "source_mode": "extend"})
    async with _both(a, b):
        sid = r.json()["id"]
        fake_agent.script.append([{"type": "source", "id": "old1", "name": "Entra ID - Contoso", "adopted": True},
                                  {"type": "final", "text": "Extending it."}])
        await a.post(f"/onboarding/api/sessions/{sid}/messages", json={"text": "Extend the existing source"})
        await _settled(sid)
        s = (await a.get(f"/onboarding/api/sessions/{sid}")).json()
        assert s["mode"] == "extend" and s["source"]["adopted"] is True
        states = {st["id"]: (st["state"], st["reason"]) for st in s["plan"]}
        for skipped in ("register_app", "provide_secret", "create_source", "check_app_name"):
            assert states[skipped] == ("skipped", "existing source")
        assert states["sp_permissions"][0] == "todo"


# ---------------------------------------------------------------- T059: long aggregations
async def test_followup_pending_then_finished_continues_as_the_iam_engineer(fake_store, isc, vault, fake_agent) -> None:  # type: ignore[no-untyped-def]
    a, b, r = await _entra(fake_store, isc, "entra-fu-1")
    async with _both(a, b):
        sid = r.json()["id"]
        source = {"id": "src9", "name": "Entra ID - Contoso"}
        fake_agent.script.append([
            {"type": "source", **source},
            {"type": "action", "action_ref": "a1", "action": "aggregate_accounts", "result": "running",
             "follow": True, "task_ids": ["ta"], "plan_step": "aggregate_accounts", "next_step": "aggregate_datasets",
             "source": source},
            {"type": "final", "text": "Still running; I'll post the result."}])
        await a.post(f"/onboarding/api/sessions/{sid}/messages", json={"text": "Run the checks"})
        await _settled(sid)
        doc = await db().sessions.find_one({"_id": ObjectId(sid)})
        follow = doc["followups"][0]
        assert follow["state"] == "following" and follow["ordered_by"]["user_id"] == doc["iam_engineer_id"]
        started = follow["started_at"].replace(tzinfo=UTC)

        # 31 minutes in, still running: pending, one note, nobody's message
        fake_agent.task_results = [{"type": "task_check", "tasks": [{"id": "ta", "completion_status": None}]}]
        assert await followups.check_once(started + timedelta(minutes=31)) >= 1
        s = (await a.get(f"/onboarding/api/sessions/{sid}")).json()
        assert next(st for st in s["plan"] if st["id"] == "aggregate_accounts")["pending_since"]
        notes = [m for m in (await a.get(f"/onboarding/api/sessions/{sid}/messages")).json()
                 if m.get("code") == "followup_pending"]
        assert len(notes) == 1 and "pending · 31 min" in notes[0]["text"]

        # 41 minutes in, done: the result note is queued as a turn run as the IAM engineer
        fake_agent.task_results = [{"type": "task_check", "tasks": [{"id": "ta", "completion_status": "SUCCESS"}],
                                    "counts": {"accounts": 4973, "users": 4812, "service_principals": 161}}]
        fake_agent.script.append([{"type": "final", "text": "Continuing."}])
        await followups.check_once(started + timedelta(minutes=41))
        await _settled(sid)
        doc = await db().sessions.find_one({"_id": ObjectId(sid)})
        assert doc["followups"][0]["state"] == "done" and doc["proof"]["users"] == 4812
        finished = await db().messages.find_one({"session_id": ObjectId(sid), "code": "followup_finished"})
        assert finished["tone"] == "success" and "after 41 min" in finished["text"]
        assert finished["speaker_user_id"] == doc["iam_engineer_id"] and finished["meta"]["trigger"] == "followup"
        turn = fake_agent.calls[-1]
        assert turn["trigger"] == "followup" and turn["ordered_by"]["role"] == "iam_engineer"
        step = next(st for st in doc["plan"] if st["id"] == "aggregate_accounts")
        assert step["state"] == "done" and not step.get("pending_since")


@pytest.fixture(autouse=True)
def _task_check(fake_agent, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """The fake agent answers task_check from `task_results` (the follow-up loop's model-free mode)."""
    fake_agent.task_results = []
    original = fake_agent.invoke

    async def invoke(payload, runtime_session_id):  # type: ignore[no-untyped-def]
        if payload.get("mode") == "task_check":
            fake_agent.calls.append(payload)
            for ev in fake_agent.task_results:
                yield ev
            return
        async for ev in original(payload, runtime_session_id):
            yield ev

    from onboarding_api.agent_client import client as agent_client

    monkeypatch.setattr(agent_client, "invoke", invoke)
    yield
    _ = asyncio, datetime
