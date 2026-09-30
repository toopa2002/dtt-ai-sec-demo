"""Weather + HR assistant running on Amazon Bedrock AgentCore Runtime.

Security model:
- The AgentCore JWT authorizer only admits Entra tokens for this agent's API
  (token #1). This code also requires the token to come from the chatbot app
  (azp) and to carry the Agent.Invoke role.
- Token #1 is exchanged on-behalf-of the user for a gateway token (#2), so the
  MCP Gateway authorizes every tool call against the *user's* roles.
- The only MCP endpoint this agent knows is GATEWAY_URL (fixed at deploy time,
  never taken from the request).
"""

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

# ngrok's free tier shows an HTML warning page to browser-like clients unless this header is set.
GATEWAY_HEADERS = {"ngrok-skip-browser-warning": "true"}

app = BedrockAgentCoreApp()
claude = AsyncAnthropicBedrock(aws_region=REGION)

_msal_app: msal.ConfidentialClientApplication | None = None


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


def _adapter_access(adapter: str, gateway_token: str) -> str:
    """Ask the gateway whether this user may use the adapter (same check as the MCP route).

    Returns "allowed" (200), "denied" (401/403) or "unavailable" (anything else, e.g. the
    adapter isn't registered or the gateway/tunnel is down) - an outage is not an access decision.
    """
    try:
        resp = httpx2.get(
            f"{GATEWAY_URL}/adapters/{adapter}",
            headers={**GATEWAY_HEADERS, "Authorization": f"Bearer {gateway_token}"},
            timeout=15,
        )
    except httpx2.HTTPError:
        return "unavailable"
    if resp.status_code == 200:
        return "allowed"
    return "denied" if resp.status_code in (401, 403) else "unavailable"


async def _run(prompt: str, gateway_token: str, allowed: list[str], denied: list[str], unavailable: list[str]) -> tuple[str, list[str]]:
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

    async with httpx2.AsyncClient(
        headers={**GATEWAY_HEADERS, "Authorization": f"Bearer {gateway_token}"}, timeout=30
    ) as http:
        sessions: dict[str, ClientSession] = {}
        tools: list[dict] = []
        tool_map: dict[str, tuple[str, str]] = {}
        stack = []
        try:
            for adapter in allowed:
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
                for tool in (await session.list_tools()).tools:
                    name = f"{prefix}__{tool.name}"
                    tool_map[name] = (adapter, tool.name)
                    tools.append({"name": name, "description": tool.description or "", "input_schema": tool.input_schema})

            messages: list[dict] = [{"role": "user", "content": prompt}]
            for _ in range(MAX_TURNS):
                kwargs = {"tools": tools} if tools else {}
                response = await claude.messages.create(
                    model=MODEL_ID, max_tokens=MAX_TOKENS, system=system, messages=messages, **kwargs
                )
                if response.stop_reason == "refusal":
                    return "Sorry, I can't help with that request.", tools_used
                if response.stop_reason != "tool_use":
                    text = "".join(b.text for b in response.content if b.type == "text")
                    return text, tools_used

                messages.append({"role": "assistant", "content": response.content})
                results = []
                for block in response.content:
                    if block.type != "tool_use":
                        continue
                    adapter, tool_name = tool_map[block.name]
                    tools_used.append(f"{adapter}/{tool_name}")
                    try:
                        result = await sessions[adapter].call_tool(tool_name, block.input)
                        text = "\n".join(c.text for c in result.content if getattr(c, "type", "") == "text")
                        results.append({"type": "tool_result", "tool_use_id": block.id, "content": text, "is_error": bool(result.is_error)})
                    except Exception as exc:  # gateway denial, timeout, server error
                        results.append({"type": "tool_result", "tool_use_id": block.id, "content": f"Tool call failed: {exc}", "is_error": True})
                messages.append({"role": "user", "content": results})
            return "I couldn't finish that request within the step limit.", tools_used
        finally:
            for ctx in reversed(stack):
                try:
                    await ctx.__aexit__(None, None, None)
                except Exception:
                    pass


@app.entrypoint
async def invoke(payload: dict, context: RequestContext):
    started = time.time()
    prompt = (payload or {}).get("prompt", "").strip()
    if not prompt:
        return {"error": "prompt is required"}

    user_token = _bearer(context)
    if not user_token:
        return {"error": "missing bearer token"}
    claims = _claims(user_token)
    if claims.get("azp") != CHAT_CLIENT_ID:
        return {"error": "forbidden: token was not issued to the chatbot app"}
    if "Agent.Invoke" not in claims.get("roles", []):
        return {"error": "forbidden: the Agent.Invoke role is required"}
    user = claims.get("preferred_username") or claims.get("oid")

    gateway_token, obo_error = _gateway_token(user_token)
    if gateway_token is None:
        print(json.dumps({"event": "obo_failed", "user": user, "error": obo_error}))
        return {
            "answer": "You don't have access to any MCP services through the gateway, so I can't look anything up for you.",
            "tools_used": [],
            "services_allowed": [],
            "services_denied": MCP_ADAPTERS,
        }

    access = {a: _adapter_access(a, gateway_token) for a in MCP_ADAPTERS}
    allowed = [a for a, v in access.items() if v == "allowed"]
    denied = [a for a, v in access.items() if v == "denied"]
    unavailable = [a for a, v in access.items() if v == "unavailable"]
    try:
        answer, tools_used = await _run(prompt, gateway_token, allowed, denied, unavailable)
    except APIStatusError as exc:  # Bedrock refused the call (model access, throttling, ...)
        print(json.dumps({"event": "model_error", "user": user, "status": exc.status_code, "error": str(exc)}))
        return {"error": f"The model call failed (HTTP {exc.status_code}). Check Bedrock model access in {REGION}."}
    print(json.dumps({"event": "invocation", "user": user, "allowed": allowed, "denied": denied, "unavailable": unavailable,
                      "tools_used": tools_used, "ms": int((time.time() - started) * 1000)}))
    return {"answer": answer, "tools_used": tools_used, "services_allowed": allowed, "services_denied": denied,
            "services_unavailable": unavailable}


if __name__ == "__main__":
    app.run()
