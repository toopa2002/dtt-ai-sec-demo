"""Two-person smoke run against a running stack (dev.sh, or the cluster through ONB_URL) with the ISC stub.

  uv run --project onboarding/api python onboarding/deploy/scripts/smoke.py [--scenario happy|trust|confirm] [--seed FILE]
      [--messages N]   (with --seed: N history messages per thread, for the scrolling checks; no model calls)

Creates (idempotently) an admin, an IAM engineer and an application owner directly in MongoDB, registers the stub
tenant, opens an AWS SaaS session, then:
  happy: the owner asks for the steps; the IAM engineer orders the connector -> all checks pass, 5 action records.
  trust: the trust is broken in the stub; the connection check fails; the agent must name the AWS side in the IAM
         engineer's thread and ask the owner in the owner's thread (relay note); after the stub trust is fixed and
         the IAM engineer asks for a rerun, the check passes.
  confirm: like trust, but the owner confirms the fix in their thread and the checks rerun under the IAM engineer's
         standing order (FR-016a): action records name the IAM engineer with the confirmation trigger.
Prints a summary and exits non-zero on any failed expectation.

Cost (Constitution IV): run against `dev.sh` with AGENT_MODEL=fake (the scripted model) it costs nothing; against a
real agent it prints the expected Bedrock calls and cost before the first message.
"""

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path

import httpx

URL = os.environ.get("ONB_URL", "http://127.0.0.1:8080/onboarding/api")
STUB = os.environ.get("ONB_STUB", "http://127.0.0.1:8099")
os.environ.setdefault("MONGO_URI", "mongodb://127.0.0.1:27018/?replicaSet=rs0&directConnection=true")
os.environ.setdefault("MONGO_DB", os.environ.get("ONB_DEV_DB", "onboarding_dev"))
PASSWORD = "smoke-password-123"
AGENT = os.environ.get("ONB_AGENT", "http://127.0.0.1:8092")
CALLS = {"happy": 15, "trust": 25, "confirm": 30}  # model calls per scenario on the real model
USD_PER_CALL = 0.004  # Claude Haiku 4.5 with prompt caching (research R27)
FAILURES: list[str] = []


def expect(cond: bool, what: str) -> None:
    print(("  ✔ " if cond else "  ✘ ") + what)
    if not cond:
        FAILURES.append(what)


async def ensure_user(name: str, role: str, admin: bool = False) -> None:
    from onboarding_api.auth import users

    if not await users.get_by_username(name):
        await users.create_user(name, name.replace(".", " ").title(), role, PASSWORD, admin)


async def client(name: str) -> httpx.AsyncClient:
    c = httpx.AsyncClient(base_url=URL, timeout=120)
    r = await c.post("/auth/login", json={"username": name, "password": PASSWORD})
    r.raise_for_status()
    c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    return c


async def wait_idle(c: httpx.AsyncClient, sid: str, timeout: float = 240) -> list[dict]:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        msgs = (await c.get(f"/sessions/{sid}/messages")).json()
        if msgs and not any(m["queue_state"] in ("queued", "processing") for m in msgs):
            return msgs
        await asyncio.sleep(2)
    raise TimeoutError("agent did not answer in time")


async def say(c: httpx.AsyncClient, sid: str, text: str, thread: str) -> dict:
    """Send in the caller's thread and return the agent's reply there (the last agent message in that thread)."""
    started = time.monotonic()
    r = await c.post(f"/sessions/{sid}/messages", json={"text": text})
    r.raise_for_status()
    msgs = await wait_idle(c, sid)
    reply = [m for m in msgs if m["speaker"] == "agent" and m["thread"] == thread and m["kind"] == "message"][-1]
    print(f"\n  [{time.monotonic() - started:.1f}s] agent in the {thread} thread:\n    "
          + reply["text"][:900].replace("\n", "\n    "))
    asked = max(m["seq"] for m in msgs if m["speaker"] != "agent")
    for m in msgs:
        if m["speaker"] == "agent" and m["seq"] > asked and (m["kind"] == "relay_note" or m["thread"] != thread):
            print(f"    ↔ {m['kind']} in the {m['thread']} thread: {m['text'][:160]}")
    return reply


async def seed_history(session_id: str, per_thread: int) -> None:
    """Long threads without model calls (quickstart §3a step 8, SC-012): alternate the participant and the agent,
    every tenth message a 40-line command output."""
    from bson import ObjectId

    from onboarding_api.auth import users
    from onboarding_api.chat import messages

    sid = ObjectId(session_id)
    speakers = {"iam_engineer": await users.get_by_username("smoke.iam"),
                "application_owner": await users.get_by_username("smoke.owner")}
    output = "\n".join(f"arn:aws:iam::111122223333:role/SailPointISCRole-acme-demo  line {i:02d}" for i in range(40))
    for n in range(per_thread):
        for thread, user in speakers.items():
            text = f"```\n{output}\n```" if n % 10 == 9 else f"Seeded message {n + 1} in the {thread} thread."
            if n % 2:
                await messages.add(sid, speaker="agent", thread=thread, text=text)
            else:
                await messages.add(sid, speaker=thread, speaker_user_id=user["_id"], text=text)


async def main(scenario: str, seed: str = "", history: int = 0) -> int:
    from onboarding_api import db

    await db.ensure_indexes()
    await ensure_user("smoke.admin", "iam_engineer", admin=True)
    await ensure_user("smoke.iam", "iam_engineer")
    await ensure_user("smoke.owner", "application_owner")
    async with httpx.AsyncClient() as s:
        await s.post(f"{STUB}/_stub/reset")
        if scenario in ("trust", "confirm"):
            await s.post(f"{STUB}/_stub/trust_broken")

    admin, iam, owner = await client("smoke.admin"), await client("smoke.iam"), await client("smoke.owner")
    tenants = (await admin.get("/admin/tenants")).json()
    tenant = next((t for t in tenants if t["name"] == "acme-demo"), None)
    if not tenant:
        secret = "3f6c1d0e9b8a7f6e5d4c3b2a19087f6e" * 2  # stub tenant: any 64-hex secret
        r = await admin.post("/admin/tenants", json={"name": "acme-demo", "api_host": "acme-demo.api.identitynow-demo.com",
                                                     "client_id": "a1b2c3d4e5f60718293a4b5c6d7e8f90",
                                                     "client_secret": secret})
        r.raise_for_status()
        tenant = r.json()
    owner_id = (await owner.get("/auth/session")).json()["id"]
    name = f"AWS - Acme Org {uuid.uuid4().hex[:4]}"
    r = await iam.post("/sessions", json={"connector_type": "aws-saas", "tenant_id": tenant["id"],
                                          "application_owner_id": owner_id,
                                          "details": {"source_name": name, "source_owner": "smoke.iam",
                                                      "management_account_id": "111122223333",
                                                      "accounts": "111122223333, 444455556666",
                                                      "region": "ap-southeast-1", "agentcore_regions": "ap-southeast-1"}})
    r.raise_for_status()
    sid = r.json()["id"]
    print(f"session {sid} ({scenario})")
    if seed:  # setup only, for the Playwright run (onboarding/web/e2e)
        if history:
            await seed_history(sid, history)
        Path(seed).write_text(json.dumps({"session_id": sid, "password": PASSWORD, "stub": STUB,
                                          "iam": "smoke.iam", "owner": "smoke.owner", "admin": "smoke.admin"}))
        for c in (admin, iam, owner):
            await c.aclose()
        await db.close()
        return 0

    try:
        async with httpx.AsyncClient(timeout=5) as s:
            model = (await s.get(f"{AGENT}/model")).json().get("model", "bedrock")
    except (httpx.HTTPError, ValueError):
        model = "unknown (assume real)"
    if model != "fake":
        calls = CALLS[scenario]
        print(f"! Agent model: {model}. This run makes about {calls} Claude Haiku calls on Bedrock, "
              f"about ${calls * USD_PER_CALL:.2f}. Use dev.sh with AGENT_MODEL=fake for a free run.")
    print("\n— application owner asks for the steps")
    reply = await say(owner, sid, "What do I need to set up in AWS first?", "application_owner")
    expect(reply["thread"] == "application_owner", "reply is in the application owner's thread")
    expect("```" in reply["text"], "reply contains copy-ready commands")
    expect(any(v in reply["text"] for v in ("5c0d9a3e", "SailPointISCRole", "111122223333")),
           "commands carry the session values")

    print("\n— application owner tries to order a SailPoint change")
    reply = await say(owner, sid, "Please create the SailPoint connector now.", "application_owner")
    actions = (await iam.get(f"/sessions/{sid}/actions")).json()
    expect(actions == [], "no SailPoint change from the application owner's message (FR-016)")

    print("\n— IAM engineer orders the connector")
    reply = await say(iam, sid, "Create the connector with the session details, then run the connection check, "
                                "aggregation and Test Connection.", "iam_engineer")
    session = (await iam.get(f"/sessions/{sid}")).json()
    actions = (await iam.get(f"/sessions/{sid}/actions")).json()
    print("\n  steps:", session["steps"])
    print("  actions:", [(a["action"], a["result"]) for a in actions])
    expect(session["steps"]["source_created"] == "passed", "source created")
    expect(all(a["ordered_by"]["display_name"] == "Smoke Iam" for a in actions), "every action names the IAM engineer")
    if scenario == "happy":
        for step in ("configured", "connection_check", "aggregation", "test_connection", "application_ready"):
            expect(session["steps"][step] == "passed", f"{step} passed")
    else:
        expect(session["steps"]["connection_check"] == "failed", "connection check failed (trust broken)")
        text = reply["text"].lower()
        expect("aws" in text and ("trust" in text or "assumerole" in text), "agent names the AWS-side trust as the cause")
        msgs = (await iam.get(f"/sessions/{sid}/messages")).json()
        # Relays are posted during the turn, before the final reply: count everything after the IAM engineer's order.
        order_seq = max(m["seq"] for m in msgs if m["speaker"] == "iam_engineer")
        after_order = [m for m in msgs if m["speaker"] == "agent" and m["seq"] > order_seq]
        expect(any(m["thread"] == "application_owner" and m["kind"] == "message" for m in after_order),
               "the owner was asked in the owner's thread")
        expect(any(m["thread"] == "iam_engineer" and m["kind"] == "relay_note" for m in after_order),
               "a relay note was left in the IAM engineer's thread (SC-010)")
        async with httpx.AsyncClient() as s:
            await s.post(f"{STUB}/_stub/fix_trust")
        if scenario == "confirm":
            print("\n— owner confirms the fix in their thread (no new order from the IAM engineer)")
            await say(owner, sid, "I have finished all the AWS setup steps, including the role trust. "
                                  "The role is ready.", "application_owner")
            actions = (await iam.get(f"/sessions/{sid}/actions")).json()
            reruns = [a for a in actions if a["trigger"] == "application_owner_confirmation"]
            print("  reruns:", [(a["action"], a["result"], a["ordered_by"]["display_name"]) for a in reruns])
            expect(len(reruns) >= 1, "the checks reran on the owner's confirmation (FR-016a)")
            expect(all(a["ordered_by"]["display_name"] == "Smoke Iam" for a in reruns), "reruns name the IAM engineer")
            msgs = (await iam.get(f"/sessions/{sid}/messages")).json()
            confirm_seq = max(m["seq"] for m in msgs if m["speaker"] == "application_owner")
            expect(any(m["speaker"] == "agent" and m["thread"] == "iam_engineer" and m["kind"] == "message"
                       and m["seq"] > confirm_seq for m in msgs),
                   "the results were reported in the IAM engineer's thread")
        else:
            print("\n— (owner fixed the trust) IAM engineer asks for a rerun")
            await say(iam, sid, "The AWS owner fixed the trust. Rerun the connection check, then aggregation and "
                                "Test Connection.", "iam_engineer")
        session = (await iam.get(f"/sessions/{sid}")).json()
        print("\n  steps:", session["steps"])
        for step in ("connection_check", "aggregation", "test_connection"):
            expect(session["steps"][step] == "passed", f"{step} passed after the fix")

    for c in (admin, iam, owner):
        await c.aclose()
    await db.close()
    print("\nRESULT:", "PASS" if not FAILURES else f"FAIL ({len(FAILURES)}): " + "; ".join(FAILURES))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", choices=["happy", "trust", "confirm"], default="happy")
    parser.add_argument("--seed", default="", help="only create users, tenant and session; write them to this JSON file")
    parser.add_argument("--messages", type=int, default=0, help="with --seed: history messages per thread")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.scenario, args.seed, args.messages)))
