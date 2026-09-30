"""Weather + HR assistant running on Amazon Bedrock AgentCore Runtime.

Security model:
- The AgentCore JWT authorizer only admits Entra tokens for this agent's API
  (token #1). This code also requires the token to come from the chatbot app
  (azp) and to carry the Agent.Invoke role.
- Token #1 is exchanged on-behalf-of the user for a gateway token (#2), so the
  MCP Gateway authorizes every tool call against the *user's* roles.
- The only MCP endpoint this agent knows is GATEWAY_URL (fixed at deploy time,
  never taken from the request).

The entrypoint is an async generator: AgentCore streams every yielded event to
the chatbot as server-sent events, which drive its live traffic panel.
Events never contain tokens or secrets - only whitelisted claims and truncated
tool arguments/results.
"""

import asyncio
import base64
import json
import os
import time
from datetime import datetime, timezone

import boto3
import httpx2
import msal
from anthropic import APIStatusError, AsyncAnthropicBedrock
from bedrock_agentcore.runtime import BedrockAgentCoreApp, RequestContext
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

REGION = os.environ["AWS_REGION"]
MODEL_ID = os.environ["BEDROCK_MODEL_ID"]
TENANT_ID = os.environ["TENANT_ID"]
AGENT_API_CLIENT_ID = os.environ["AGENT_API_CLIENT_ID"]
CHAT_CLIENT_ID = os.environ["CHAT_CLIENT_ID"]
GATEWAY_API_CLIENT_ID = os.environ["GATEWAY_API_CLIENT_ID"]
GATEWAY_URL = os.environ["GATEWAY_URL"].rstrip("/")
MCP_ADAPTERS = [a.strip() for a in os.environ.get("MCP_ADAPTERS", "weather,hr-directory").split(",") if a.strip()]
OBO_SECRET_PARAM = os.environ.get("OBO_SECRET_PARAM", "/mcpdemo/agent-obo-secret")
MAX_TURNS = 5
MAX_TOKENS = 1024
DETAIL_CHARS = 300
CLAIMS_SHOWN = ("aud", "azp", "roles", "scp", "preferred_username")

# ngrok's free tier shows an HTML warning page to browser-like clients unless this header is set.
GATEWAY_HEADERS = {"ngrok-skip-browser-warning": "true"}

app = BedrockAgentCoreApp()
claude = AsyncAnthropicBedrock(aws_region=REGION)

_msal_app: msal.ConfidentialClientApplication | None = None


class Tracer:
    """Builds the hop events shown in the chatbot's traffic panel (one tracer per request)."""

    def __init__(self) -> None:
        self._count = 0

    def hop(self, src: str, dst: str, label: str, target: str | None = None, hop_id: str | None = None) -> "Hop":
        self._count += 1
        return Hop(hop_id or f"h{self._count}", src, dst, label, target)


class Hop:
    def __init__(self, hop_id: str, src: str, dst: str, label: str, target: str | None) -> None:
        self.event = {"type": "hop", "id": hop_id, "from": src, "to": dst, "target": target, "label": label}
        self._t0 = time.perf_counter()

    def start(self) -> dict:
        return {**self.event, "status": "start"}

    def end(self, status: str, **detail) -> dict:
        ms = int((time.perf_counter() - self._t0) * 1000)
        return {**self.event, "status": status, "ms": ms, "detail": detail}


def _short(value) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= DETAIL_CHARS else text[:DETAIL_CHARS] + "…"


def _obo_client() -> msal.ConfidentialClientApplication:
    global _msal_app
    if _msal_app is None:
        secret = boto3.client("ssm", region_name=REGION).get_parameter(Name=OBO_SECRET_PARAM, WithDecryption=True)
        _msal_app = msal.ConfidentialClientApplication(
            AGENT_API_CLIENT_ID,
            authority=f"https://login.microsoftonline.com/{TENANT_ID}",
            client_credential=secret["Parameter"]["Value"],
            token_cache=msal.TokenCache(),
        )
    return _msal_app


def _claims(jwt: str) -> dict:
    # Signature, issuer and audience were already validated by the
    # AgentCore JWT authorizer; this only reads the claims.
    payload = jwt.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


def _claims_summary(claims: dict) -> dict:
    return {k: claims[k] for k in CLAIMS_SHOWN if k in claims}


def _bearer(context: RequestContext) -> str | None:
    headers = {k.lower(): v for k, v in (context.request_headers or {}).items()}
    auth = headers.get("authorization", "")
    return auth[7:] if auth.lower().startswith("bearer ") else None


def _gateway_token(user_token: str) -> tuple[str | None, str | None]:
    """Exchange the user's agent token for a gateway token (on-behalf-of)."""
    result = _obo_client().acquire_token_on_behalf_of(user_token, [f"api://{GATEWAY_API_CLIENT_ID}/access_as_user"])
    if "access_token" in result:
        return result["access_token"], None
    return None, result.get("error_description") or result.get("error", "unknown error")


async def _adapter_access(http: httpx2.AsyncClient, adapter: str) -> tuple[str, int | None]:
    """Ask the gateway whether this user may use the adapter (same check as the MCP route).

    Returns ("allowed" | "denied" | "unavailable", HTTP status). Anything other than
    200/401/403 (adapter not registered, gateway or tunnel down) is an outage, not an
    access decision.
    """
    try:
        resp = await http.get(f"{GATEWAY_URL}/adapters/{adapter}", timeout=15)
    except httpx2.HTTPError:
        return "unavailable", None
    if resp.status_code == 200:
        return "allowed", 200
    return ("denied" if resp.status_code in (401, 403) else "unavailable"), resp.status_code


async def _run(tracer: Tracer, http: httpx2.AsyncClient, prompt: str, allowed: list[str], denied: list[str],
               unavailable: list[str]):
    """Agent loop. Yields hop events, then a final ("result", answer, tools_used) tuple."""
    tools_used: list[str] = []
    system = (
        f"You are a helpful company assistant. Today is {datetime.now(timezone.utc):%A %d %B %Y} (UTC). "
        "Answer using the tools available to you. Never invent employee information that did not come from a tool. "
        "Keep answers short."
    )
    if denied:
        system += (
            " The user is NOT authorized to use these services: " + ", ".join(denied) + ". "
            "If the question needs one of them, say the user lacks access to that service and answer the rest."
        )
    if unavailable:
        system += (
            " These services are temporarily unavailable (a technical problem, not a permission issue): "
            + ", ".join(unavailable) + ". If the question needs one of them, say so and answer the rest."
        )

    sessions: dict[str, ClientSession] = {}
    tools: list[dict] = []
    tool_map: dict[str, tuple[str, str]] = {}
    stack = []
    try:
        for adapter in allowed:
            hop = tracer.hop("agent", "gateway", "MCP initialize + tools/list", target=adapter)
            yield hop.start()
            transport = streamable_http_client(f"{GATEWAY_URL}/adapters/{adapter}/mcp", http_client=http)
            # Some mcp releases yield (read, write), others (read, write, get_session_id).
            streams = await transport.__aenter__()
            read, write = streams[0], streams[1]
            stack.append(transport)
            session = ClientSession(read, write)
            await session.__aenter__()
            stack.append(session)
            await session.initialize()
            sessions[adapter] = session
            prefix = adapter.replace("-", "_")
            names = []
            for tool in (await session.list_tools()).tools:
                name = f"{prefix}__{tool.name}"
                tool_map[name] = (adapter, tool.name)
                tools.append({"name": name, "description": tool.description or "", "input_schema": tool.input_schema})
                names.append(tool.name)
            yield hop.end("ok", tools=names)

        messages: list[dict] = [{"role": "user", "content": prompt}]
        for turn in range(1, MAX_TURNS + 1):
            hop = tracer.hop("agent", "bedrock", f"messages.create (turn {turn})")
            yield hop.start()
            kwargs = {"tools": tools} if tools else {}
            try:
                response = await claude.messages.create(
                    model=MODEL_ID, max_tokens=MAX_TOKENS, system=system, messages=messages, **kwargs
                )
            except APIStatusError as exc:
                yield hop.end("error", http_status=exc.status_code, error=_short(str(exc)))
                raise
            requested = [b.name for b in response.content if b.type == "tool_use"]
            yield hop.end("ok", model=MODEL_ID, stop_reason=response.stop_reason, tools_requested=requested,
                          input_tokens=response.usage.input_tokens, output_tokens=response.usage.output_tokens)

            if response.stop_reason == "refusal":
                yield ("result", "Sorry, I can't help with that request.", tools_used)
                return
            if response.stop_reason != "tool_use":
                yield ("result", "".join(b.text for b in response.content if b.type == "text"), tools_used)
                return

            messages.append({"role": "assistant", "content": response.content})
            results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                adapter, tool_name = tool_map[block.name]
                tools_used.append(f"{adapter}/{tool_name}")
                hop = tracer.hop("agent", "gateway", f"tools/call {tool_name}", target=adapter)
                yield hop.start()
                try:
                    result = await sessions[adapter].call_tool(tool_name, block.input)
                    text = "\n".join(c.text for c in result.content if getattr(c, "type", "") == "text")
                    results.append({"type": "tool_result", "tool_use_id": block.id, "content": text, "is_error": bool(result.is_error)})
                    # What the MCP server itself received from the gateway (see servers/*/src/main.py).
                    server_saw = (result.meta or {}).get("hop")
                    yield hop.end("error" if result.is_error else "ok", arguments=_short(block.input),
                                  result=_short(text), server_saw=server_saw)
                except Exception as exc:  # gateway denial, timeout, server error
                    results.append({"type": "tool_result", "tool_use_id": block.id, "content": f"Tool call failed: {exc}", "is_error": True})
                    yield hop.end("error", arguments=_short(block.input), error=_short(str(exc)))
            messages.append({"role": "user", "content": results})
        yield ("result", "I couldn't finish that request within the step limit.", tools_used)
    finally:
        for ctx in reversed(stack):
            try:
                await ctx.__aexit__(None, None, None)
            except Exception:
                pass


@app.entrypoint
async def invoke(payload: dict, context: RequestContext):
    started = time.perf_counter()
    tracer = Tracer()
    prompt = (payload or {}).get("prompt", "").strip()
    if not prompt:
        yield {"type": "error", "message": "prompt is required"}
        return

    # The chatbot emits the "start" of this hop when it sends the request.
    received = tracer.hop("chatbot", "agent", "POST /invocations", hop_id="invoke")
    user_token = _bearer(context)
    if not user_token:
        yield received.end("denied", reason="missing bearer token")
        yield {"type": "error", "message": "missing bearer token"}
        return
    claims = _claims(user_token)
    token1 = _claims_summary(claims)
    if claims.get("azp") != CHAT_CLIENT_ID:
        yield received.end("denied", reason="token was not issued to the chatbot app", token=token1)
        yield {"type": "error", "message": "forbidden: token was not issued to the chatbot app"}
        return
    if "Agent.Invoke" not in claims.get("roles", []):
        yield received.end("denied", reason="the Agent.Invoke role is required", token=token1)
        yield {"type": "error", "message": "forbidden: the Agent.Invoke role is required"}
        return
    yield received.end("ok", token=token1)
    user = claims.get("preferred_username") or claims.get("oid")

    hop = tracer.hop("agent", "entra", "on-behalf-of token exchange")
    yield hop.start()
    gateway_token, obo_error = await asyncio.to_thread(_gateway_token, user_token)
    if gateway_token is None:
        print(json.dumps({"event": "obo_failed", "user": user, "error": obo_error}))
        yield hop.end("denied", error=_short(obo_error))
        yield {
            "type": "final",
            "answer": "You don't have access to any MCP services through the gateway, so I can't look anything up for you.",
            "tools_used": [],
            "services_allowed": [],
            "services_denied": MCP_ADAPTERS,
            "services_unavailable": [],
            "ms": int((time.perf_counter() - started) * 1000),
        }
        return
    yield hop.end("ok", token=_claims_summary(_claims(gateway_token)))

    async with httpx2.AsyncClient(
        headers={**GATEWAY_HEADERS, "Authorization": f"Bearer {gateway_token}"}, timeout=30
    ) as http:
        access: dict[str, str] = {}
        for adapter in MCP_ADAPTERS:
            hop = tracer.hop("agent", "gateway", f"GET /adapters/{adapter}", target=adapter)
            yield hop.start()
            decision, status = await _adapter_access(http, adapter)
            access[adapter] = decision
            yield hop.end({"allowed": "ok", "denied": "denied", "unavailable": "error"}[decision], http_status=status)
        allowed = [a for a, v in access.items() if v == "allowed"]
        denied = [a for a, v in access.items() if v == "denied"]
        unavailable = [a for a, v in access.items() if v == "unavailable"]

        answer, tools_used = "", []
        try:
            async for item in _run(tracer, http, prompt, allowed, denied, unavailable):
                if isinstance(item, tuple):
                    _, answer, tools_used = item
                else:
                    yield item
        except APIStatusError as exc:  # Bedrock refused the call (model access, throttling, ...)
            print(json.dumps({"event": "model_error", "user": user, "status": exc.status_code, "error": str(exc)}))
            yield {"type": "error", "message": f"The model call failed (HTTP {exc.status_code}). Check Bedrock model access in {REGION}."}
            return

    ms = int((time.perf_counter() - started) * 1000)
    print(json.dumps({"event": "invocation", "user": user, "allowed": allowed, "denied": denied, "unavailable": unavailable,
                      "tools_used": tools_used, "ms": ms}))
    yield {"type": "final", "answer": answer, "tools_used": tools_used, "services_allowed": allowed,
           "services_denied": denied, "services_unavailable": unavailable, "ms": ms}


if __name__ == "__main__":
    app.run()
