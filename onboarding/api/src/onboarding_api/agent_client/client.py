"""AgentCore runtime client (contracts/agent-invocation.md, research R2).

The API is the only caller: SigV4 InvokeAgentRuntime with the narrowly scoped `onboarding-api` IAM user. No credential
is ever placed in the request; the agent fetches its own ISC token through AgentCore Identity.
"""

import asyncio
import json
import logging
import threading
from collections.abc import AsyncIterator
from typing import Any

import boto3
from botocore.config import Config

from ..config import settings

log = logging.getLogger(__name__)
_client = None


def _boto():  # type: ignore[no-untyped-def]
    global _client
    if _client is None:
        _client = boto3.client("bedrock-agentcore", region_name=settings().aws_region,
                               config=Config(read_timeout=300, retries={"max_attempts": 2}))
    return _client


def _iter_lines(body: Any):  # type: ignore[no-untyped-def]
    buf = b""
    for chunk in body.iter_chunks(chunk_size=1024) if hasattr(body, "iter_chunks") else iter(lambda: body.read(1024), b""):
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            yield line.decode("utf-8", "replace").rstrip("\r")
    if buf:
        yield buf.decode("utf-8", "replace")


def _parse(line: str) -> dict | None:
    if not line or line.startswith(":"):
        return None
    if line.startswith("data:"):
        line = line[5:].strip()
    try:
        value = json.loads(line)
    except json.JSONDecodeError:
        return None
    # The AgentCore SDK may wrap a streamed str; our agent yields dicts, possibly JSON-encoded twice.
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return None
    return value if isinstance(value, dict) and "type" in value else None


async def _invoke_http(payload: dict[str, Any], runtime_session_id: str) -> AsyncIterator[dict]:
    """Local agent over HTTP (AGENT_ENDPOINT): same request and event stream as AgentCore."""
    import httpx

    url = settings().agent_endpoint.rstrip("/") + "/invocations"
    headers = {"X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": runtime_session_id, "Accept": "text/event-stream"}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=10)) as http, \
                http.stream("POST", url, json=payload, headers=headers) as response:
            async for line in response.aiter_lines():
                event = _parse(line)
                if event:
                    yield event
    except httpx.HTTPError as exc:
        log.warning("local agent invoke failed: %s", type(exc).__name__)
        yield {"type": "error", "code": "agent_unavailable", "message": "The agent could not be reached."}


async def invoke(payload: dict[str, Any], runtime_session_id: str) -> AsyncIterator[dict]:
    """Stream agent events. boto3 is blocking, so the HTTP stream is read on a thread and handed over a queue."""
    if settings().agent_endpoint:
        async for event in _invoke_http(payload, runtime_session_id):
            yield event
        return
    # The agent asks AgentCore Identity for a workload token on this identity (it gets none from the runtime context,
    # since the API invokes it without an end user), then exchanges it for the tenant's SailPoint token.
    payload = {**payload, "workload_name": settings().agent_workload_name}
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    done = object()

    def worker() -> None:
        try:
            response = _boto().invoke_agent_runtime(
                agentRuntimeArn=settings().agent_runtime_arn,
                runtimeSessionId=runtime_session_id.ljust(33, "0"),
                payload=json.dumps(payload).encode(),
                contentType="application/json",
                accept="text/event-stream",
            )
            for line in _iter_lines(response["response"]):
                event = _parse(line)
                if event:
                    loop.call_soon_threadsafe(queue.put_nowait, event)
        except Exception as exc:  # noqa: BLE001 — surfaced as an error event
            log.warning("agent invoke failed: %s", type(exc).__name__)
            loop.call_soon_threadsafe(queue.put_nowait, {"type": "error", "code": "agent_unavailable",
                                                         "message": "The agent could not be reached."})
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, done)

    threading.Thread(target=worker, daemon=True).start()
    while True:
        item = await queue.get()
        if item is done:
            return
        yield item


async def tenant_check(tenant: dict) -> dict:
    """Ask the agent to fetch a token through AgentCore Identity and read GET /beta/tenant (no secret in the API)."""
    payload = {"mode": "tenant_check", "tenant": {"api_host": tenant["api_host"],
                                                  "credential_provider": tenant["credential_provider"]}}
    result = {"status": "unchecked", "external_id": None}
    async for event in invoke(payload, f"onb-tenant-{tenant['_id']}"):
        if event.get("type") == "tenant_check":
            result = {"status": event.get("status", "unchecked"), "external_id": event.get("external_id")}
    return result
