"""AgentCore runtime entrypoint (contracts/agent-invocation.md). Streams one JSON event per chunk.

Modes: `turn` (one queued participant message), `secret_check` (screenshot filter, FR-026a), `tenant_check` (token via
AgentCore Identity + GET /beta/tenant, for the admin's "Check now"). The runtime is stateless per turn.
"""

import asyncio
import logging
import os
from collections.abc import AsyncIterator

from bedrock_agentcore import BedrockAgentCoreApp
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import fake_model, loop, playbooks
from .isc.client import IscClient, IscError, agentcore_token_fn
from .isc.tools import IscTools
from .tools.session import SessionTools

log = logging.getLogger("onboarding_agent")
app = BedrockAgentCoreApp()
REGION = os.environ.get("AWS_REGION", "ap-southeast-1")
REQUIRED = ("turn_id", "ordered_by", "session", "message")
# Constitution IV (research R28): `fake` = the scripted model, for the local dev stack against the ISC stub only;
# the AgentCore image never sets AGENT_MODEL, so production always uses Claude Haiku on Bedrock.
AGENT_MODEL = os.environ.get("AGENT_MODEL", "bedrock")
if AGENT_MODEL == "fake":
    fake_model.check_allowed(os.environ.get("ONBOARDING_ISC_BASE_URL"))
MODEL = fake_model.FakeModel() if AGENT_MODEL == "fake" else None  # None: loop builds the Bedrock client
log.warning("agent model: %s", "scripted (fake)" if MODEL else os.environ.get(
    "BEDROCK_MODEL_ID", "global.anthropic.claude-haiku-4-5-20251001-v1:0"))


async def model_info(_: Request) -> JSONResponse:
    """Which model this agent uses, so scripts can say whether a run costs money (Constitution IV)."""
    return JSONResponse({"model": "fake" if MODEL else "bedrock"})


app.add_route("/model", model_info, methods=["GET"])


def _isc(tenant: dict, workload_name: str | None = None) -> IscClient:
    static = os.environ.get("ONBOARDING_ISC_TOKEN")  # local runs against the ISC stub only; never set on AgentCore
    if static:
        async def token() -> str:
            return static

        return IscClient(tenant["api_host"], token)
    return IscClient(tenant["api_host"], agentcore_token_fn(tenant["credential_provider"], REGION, workload_name))


async def _run_turn(payload: dict, emit) -> None:  # type: ignore[no-untyped-def]
    session = payload["session"]
    pb = playbooks.for_session(session)
    isc = _isc(session["tenant"], payload.get("workload_name"))
    try:
        role = payload["ordered_by"]["role"]
        thread = payload.get("message", {}).get("thread") or role
        tools = IscTools(isc, pb, session, emit,
                         trigger="order" if role == "iam_engineer" else "application_owner_confirmation")
        await loop.run_turn(payload, pb, tools, SessionTools(emit, thread), emit, claude=MODEL)
    finally:
        await isc.close()


async def _tenant_check(payload: dict) -> dict:
    isc = _isc(payload["tenant"], payload.get("workload_name"))
    try:
        tenant = await isc.get("/beta/tenant")
    except IscError as exc:
        # Only SailPoint refusing the credential marks it rejected; an agent-side token problem (status 0) or any
        # other failure leaves the tenant unchecked.
        status = "credential_rejected" if exc.status in (400, 401, 403) else "unchecked"
        log.warning("tenant check failed: %s", exc)
        return {"type": "tenant_check", "status": status, "external_id": None}
    finally:
        await isc.close()
    ext = next((p.get("attributes", {}).get("externalId") for p in tenant.get("products") or []
                if p.get("productName") == "idn"), None) or tenant.get("externalId")
    return {"type": "tenant_check", "status": "usable", "external_id": ext}


@app.entrypoint
async def invoke(payload: dict, context=None) -> AsyncIterator[dict]:  # type: ignore[no-untyped-def]
    mode = payload.get("mode", "turn")
    if mode == "secret_check":
        try:
            yield await loop.secret_check(payload, claude=MODEL)
        except Exception as exc:  # noqa: BLE001 — fail closed: an unchecked image is held
            log.warning("secret check failed: %s", type(exc).__name__)
            yield {"type": "secret_check", "result": "held", "reason": "The image could not be checked."}
        return
    if mode == "tenant_check":
        yield await _tenant_check(payload)
        return
    missing = [k for k in REQUIRED if k not in payload]
    if missing:
        yield {"type": "error", "code": "bad_request", "message": f"missing {', '.join(missing)}"}
        return

    queue: asyncio.Queue = asyncio.Queue()
    done = object()

    async def emit(event: dict) -> None:
        await queue.put(event)

    async def runner() -> None:
        try:
            await _run_turn(payload, emit)
        except Exception as exc:  # noqa: BLE001
            log.exception("turn failed")
            await queue.put({"type": "error", "code": "agent_error", "message": f"{type(exc).__name__}"})
        finally:
            await queue.put(done)

    task = asyncio.create_task(runner())
    while True:
        event = await queue.get()
        if event is done:
            break
        yield event
    await task


if __name__ == "__main__":
    app.run(port=int(os.environ.get("PORT", "8080")))
