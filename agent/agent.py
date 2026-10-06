"""Weather + HR assistant running on Amazon Bedrock AgentCore Runtime.

Security model:
- The AgentCore JWT authorizer only admits Entra tokens for this agent's API
  (token #1). This code also requires the token to come from the chatbot app
  (azp) and to carry the Agent.Invoke role.
- Token #1 is exchanged on-behalf-of the user for a gateway token (#2), so the
  MCP Gateway authorizes every tool call against the *user's* roles.
- The only MCP endpoint this agent knows is GATEWAY_URL (fixed at deploy time,
  never taken from the request).

TOOLS_VIA selects the route to the tools:
- "mcp-gateway" (default): the agent does the OBO exchange itself and calls the
  Microsoft MCP Gateway per adapter, as described above.
- "agentcore-gateway": the agent forwards token #1 to one AWS Bedrock AgentCore
  Gateway per adapter (AGENTCORE_GATEWAY_URLS). Each validates it, performs the
  same on-behalf-of exchange through its Entra credential provider, and calls the
  Microsoft MCP Gateway - so tool calls are still authorized against the *user's*
  roles, and AWS sees the gateways and their MCP targets.

The entrypoint is an async generator: AgentCore streams every yielded event to
the chatbot as server-sent events, which drive its live traffic panel.
Events never contain tokens or secrets - only whitelisted claims and truncated
tool arguments/results.
"""

import asyncio
import uuid
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
from mcp import ClientSession, types
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
TOOLS_VIA = os.environ.get("TOOLS_VIA", "mcp-gateway")
# "weather=https://...,hr-directory=https://..." - one AgentCore Gateway per adapter (see scripts/agentcore-gateway.sh).
AGENTCORE_GATEWAY_URLS = {
    name.strip(): url.strip().rstrip("/")
    for name, _, url in (pair.partition("=") for pair in os.environ.get("AGENTCORE_GATEWAY_URLS", "").split(","))
    if name.strip() and url.strip()
}
if TOOLS_VIA not in ("mcp-gateway", "agentcore-gateway"):
    raise RuntimeError(f"TOOLS_VIA must be mcp-gateway or agentcore-gateway, not {TOOLS_VIA!r}")
if TOOLS_VIA == "agentcore-gateway" and not set(MCP_ADAPTERS) <= set(AGENTCORE_GATEWAY_URLS):
    raise RuntimeError("TOOLS_VIA=agentcore-gateway needs AGENTCORE_GATEWAY_URLS for: " + ", ".join(MCP_ADAPTERS))
# AgentCore Gateway names tools <target>___<tool>; each gateway's single target is named after its adapter.
AGENTCORE_TOOL_SEP = "___"
# AGENT_ENGINE picks the model loop: "claude" (default; the agent calls Claude on Bedrock with the tools) or
# "bedrock-agent" (the Bedrock Agent BEDROCK_AGENT_ID/BEDROCK_AGENT_ALIAS_ID runs the loop; its action groups mirror
# the MCP tools and return control, so this agent still executes every tool call with the user's token).
AGENT_ENGINE = os.environ.get("AGENT_ENGINE", "claude")
BEDROCK_AGENT_ID = os.environ.get("BEDROCK_AGENT_ID", "")
BEDROCK_AGENT_ALIAS_ID = os.environ.get("BEDROCK_AGENT_ALIAS_ID", "")
if AGENT_ENGINE not in ("claude", "bedrock-agent"):
    raise RuntimeError(f"AGENT_ENGINE must be claude or bedrock-agent, not {AGENT_ENGINE!r}")
if AGENT_ENGINE == "bedrock-agent" and not (BEDROCK_AGENT_ID and BEDROCK_AGENT_ALIAS_ID):
    raise RuntimeError("AGENT_ENGINE=bedrock-agent needs BEDROCK_AGENT_ID and BEDROCK_AGENT_ALIAS_ID")
# A request may pick the engine ({"engine": ...}, the chatbot's platform switch) among those configured here; both run
# the same tools through the same gateways with the same user token, so the choice carries no extra privilege.
ENGINES = ["claude"] + (["bedrock-agent"] if BEDROCK_AGENT_ID and BEDROCK_AGENT_ALIAS_ID else [])
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


# --- HTTP request/response shown per step in the traffic panel ------------------------------------------------
# Only these headers are shown. Authorization is never shown: it becomes "Bearer <token #N>" (the step's detail
# already carries that token's whitelisted claims). Bodies are cut to DETAIL_CHARS.
SHOWN_HEADERS = ("content-type", "accept", "mcp-session-id", "mcp-protocol-version",
                 "x-amzn-bedrock-agentcore-runtime-session-id")


def _shown_headers(headers, token_label: str | None = None) -> dict:
    items = {str(k).lower(): v for k, v in dict(headers or {}).items()}
    shown = {k: items[k] for k in SHOWN_HEADERS if k in items}
    if "authorization" in items:
        shown["authorization"] = f"Bearer <{token_label or 'token'}>"
    return shown


def _body(raw) -> str | None:
    if raw is None or raw in (b"", ""):
        return None
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", "replace")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return _short(raw)
    return _short(raw)


def _exchange(method: str, url: str, req_headers=None, req_body=None, status: int | None = None,
              resp_headers=None, resp_body=None, token_label: str | None = None) -> dict:
    return {
        "request": {"method": method, "url": url, "headers": _shown_headers(req_headers, token_label),
                    "body": _body(req_body)},
        "response": {"status": status, "headers": _shown_headers(resp_headers), "body": _body(resp_body)},
    }


class HttpRecorder:
    """Records the HTTP exchanges an httpx client makes (event hooks), so each MCP step can show them.

    Response bodies are not read here - MCP responses are SSE streams the MCP client consumes - so the caller
    adds a body excerpt (tool list, tool result, refusal) to the step's last exchange.
    """

    def __init__(self, token_label: str) -> None:
        self.token_label = token_label
        self.exchanges: list[dict] = []
        self._open: dict[int, dict] = {}

    async def _on_request(self, request) -> None:
        try:
            body = request.content
        except Exception:  # streaming request body not read yet
            body = None
        ex = _exchange(request.method, str(request.url), request.headers, body, token_label=self.token_label)
        self._open[id(request)] = ex
        self.exchanges.append(ex)

    async def _on_response(self, response) -> None:
        ex = self._open.pop(id(response.request), None)
        if ex is not None:
            ex["response"]["status"] = response.status_code
            ex["response"]["headers"] = _shown_headers(response.headers)

    def hooks(self) -> dict:
        return {"request": [self._on_request], "response": [self._on_response]}

    def mark(self) -> int:
        return len(self.exchanges)

    def since(self, mark: int, body=None) -> list[dict]:
        exchanges = self.exchanges[mark:]
        if exchanges and body is not None:
            exchanges[-1]["response"]["body"] = _body(body)
        return exchanges


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


async def _adapter_access(http: httpx2.AsyncClient, adapter: str) -> tuple[str, int | None, str | None]:
    """Ask the gateway whether this user may use the adapter (same check as the MCP route).

    Returns ("allowed" | "denied" | "unavailable", HTTP status, response body). Anything other than
    200/401/403 (adapter not registered, gateway or tunnel down) is an outage, not an
    access decision.
    """
    try:
        resp = await http.get(f"{GATEWAY_URL}/adapters/{adapter}", timeout=15)
    except httpx2.HTTPError as exc:
        return "unavailable", None, str(exc)
    if resp.status_code == 200:
        return "allowed", 200, resp.text
    return ("denied" if resp.status_code in (401, 403) else "unavailable"), resp.status_code, resp.text


async def _open_session(stack: list, url: str, http: httpx2.AsyncClient) -> ClientSession:
    transport = streamable_http_client(url, http_client=http)
    # Some mcp releases yield (read, write), others (read, write, get_session_id).
    streams = await transport.__aenter__()
    read, write = streams[0], streams[1]
    stack.append(transport)
    session = ClientSession(read, write)
    await session.__aenter__()
    stack.append(session)
    await session.initialize()
    return session


async def _list_all_tools(session: ClientSession) -> list:
    """tools/list across all pages (an AgentCore Gateway pages its results)."""
    tools, cursor = [], None
    while True:
        page = await session.list_tools(params=types.PaginatedRequestParams(cursor=cursor) if cursor else None)
        tools.extend(page.tools)
        cursor = page.next_cursor
        if not cursor:
            return tools


async def _call_tool(tracer: Tracer, rec: "HttpRecorder", sessions: dict, adapter: str, remote_name: str,
                     tool_name: str, args: dict, out: list):
    """Run one MCP tool call (through whichever gateway the adapter's session uses); yields hop events and appends
    (result text, is_error) to `out`."""
    hop = tracer.hop("agent", "gateway" if TOOLS_VIA == "mcp-gateway" else "agentcore-gw",
                     f"tools/call {tool_name}", target=adapter)
    yield hop.start()
    mark = rec.mark()
    try:
        result = await sessions[adapter].call_tool(remote_name, args)
        text = "\n".join(c.text for c in result.content if getattr(c, "type", "") == "text")
        out.append((text, bool(result.is_error)))
        # What the MCP server itself received from the gateway (see servers/*/src/main.py).
        server_saw = (result.meta or {}).get("hop")
        yield hop.end("error" if result.is_error else "ok", arguments=_short(args), result=_short(text),
                      server_saw=server_saw, http=rec.since(mark, {"isError": bool(result.is_error), "content": text}))
    except Exception as exc:  # gateway denial, timeout, server error
        out.append((f"Tool call failed: {exc}", True))
        yield hop.end("error", arguments=_short(args), error=_short(str(exc)), http=rec.since(mark, str(exc)))


_agents_runtime = None


def _invoke_bedrock_agent(**kwargs) -> tuple[str, dict | None]:
    """InvokeAgent and drain its event stream: (answer text, returnControl payload or None). Runs in a thread."""
    global _agents_runtime
    if _agents_runtime is None:
        _agents_runtime = boto3.client("bedrock-agent-runtime", region_name=REGION)
    response = _agents_runtime.invoke_agent(agentId=BEDROCK_AGENT_ID, agentAliasId=BEDROCK_AGENT_ALIAS_ID, **kwargs)
    text, control = [], None
    for event in response["completion"]:
        if "chunk" in event:
            text.append(event["chunk"]["bytes"].decode("utf-8"))
        elif "returnControl" in event:
            control = event["returnControl"]
    return "".join(text), control


def _last_text(message: dict) -> str:
    """Readable excerpt of a chat message (plain text, or the text of tool results)."""
    content = message.get("content")
    if isinstance(content, str):
        return content
    parts = []
    for block in content or []:
        if isinstance(block, dict):
            parts.append(str(block.get("content") or block.get("text") or ""))
        else:
            parts.append(getattr(block, "text", "") or f"tool_use {getattr(block, 'name', '')}")
    return " | ".join(p for p in parts if p)


def _listing_refusal(exc: Exception) -> str | None:
    """Text of the error an AgentCore Gateway returns *instead of* a tool list (e.g. the Microsoft gateway's 403).

    The gateway answers tools/list with a tool-error shaped result ({content, isError: true}); the MCP client rejects
    it as an invalid ListToolsResult, and the pydantic validation error carries the full reply as its input.
    """
    errors = getattr(exc, "errors", None)
    if not callable(errors):
        return None
    for err in errors():
        reply = err.get("input")
        if isinstance(reply, dict) and reply.get("isError"):
            texts = [c.get("text", "") for c in reply.get("content", []) if isinstance(c, dict)]
            return " ".join(t for t in texts if t) or "error"
    return None


def _system_prompt(denied: list[str], unavailable: list[str]) -> str:
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
    return system


async def _run(tracer: Tracer, http: httpx2.AsyncClient, rec: HttpRecorder, prompt: str, access: dict[str, str] | None,
               engine: str = AGENT_ENGINE):
    """Agent loop. Yields hop events, then ("access", {adapter: decision}) and ("result", answer, tools_used).

    access is the per-adapter decision from the Microsoft gateway probes (mcp-gateway mode), or None in
    agentcore-gateway mode, where it is derived from the tools the AgentCore Gateway lists for this user.
    """
    tools_used: list[str] = []
    sessions: dict[str, ClientSession] = {}
    tools: list[dict] = []
    tool_map: dict[str, tuple[str, str, str]] = {}  # model tool name -> (adapter, MCP tool name, short name)
    stack = []
    try:
        if access is None:
            access = {}
            for adapter in MCP_ADAPTERS:
                hop = tracer.hop("agent", "agentcore-gw", "MCP initialize + tools/list", target=adapter)
                yield hop.start()
                mark = rec.mark()
                try:
                    session = await _open_session(stack, f"{AGENTCORE_GATEWAY_URLS[adapter]}/mcp", http)
                    listed = await _list_all_tools(session)
                except Exception as exc:
                    # The Microsoft gateway's 403 for a missing role comes back as an authorization error result.
                    reason = _listing_refusal(exc) or str(exc)
                    denied = "authoriz" in reason.lower() or "403" in reason
                    access[adapter] = "denied" if denied else "unavailable"
                    yield hop.end("denied" if denied else "error", error=_short(reason),
                                  http=rec.since(mark, {"isError": True, "content": reason}))
                    continue
                sessions[adapter] = session
                names = []
                for tool in listed:
                    short = tool.name.partition(AGENTCORE_TOOL_SEP)[2] or tool.name
                    tool_map[tool.name] = (adapter, tool.name, short)
                    tools.append({"name": tool.name, "description": tool.description or "", "input_schema": tool.input_schema})
                    names.append(short)
                access[adapter] = "allowed"
                yield hop.end("ok", tools=names, obo="performed by the AgentCore Gateway credential provider",
                              http=rec.since(mark, {"tools": [t.name for t in listed]}))
        else:
            for adapter in [a for a, v in access.items() if v == "allowed"]:
                hop = tracer.hop("agent", "gateway", "MCP initialize + tools/list", target=adapter)
                yield hop.start()
                mark = rec.mark()
                session = await _open_session(stack, f"{GATEWAY_URL}/adapters/{adapter}/mcp", http)
                sessions[adapter] = session
                prefix = adapter.replace("-", "_")
                names = []
                for tool in await _list_all_tools(session):
                    name = f"{prefix}__{tool.name}"
                    tool_map[name] = (adapter, tool.name, tool.name)
                    tools.append({"name": name, "description": tool.description or "", "input_schema": tool.input_schema})
                    names.append(tool.name)
                yield hop.end("ok", tools=names, http=rec.since(mark, {"tools": names}))
        yield ("access", access)
        system = _system_prompt([a for a, v in access.items() if v == "denied"],
                                [a for a, v in access.items() if v == "unavailable"])

        if engine == "bedrock-agent":
            async for item in _bedrock_agent_loop(tracer, rec, sessions, tool_map, prompt, access, tools_used):
                yield item
            return

        messages: list[dict] = [{"role": "user", "content": prompt}]
        for turn in range(1, MAX_TURNS + 1):
            hop = tracer.hop("agent", "bedrock", f"messages.create (turn {turn})")
            yield hop.start()
            kwargs = {"tools": tools} if tools else {}
            # Bedrock calls are SigV4-signed by the AWS SDK; there is no httpx client to record, so describe them.
            bedrock_url = f"https://bedrock-runtime.{REGION}.amazonaws.com/model/{MODEL_ID}/invoke"
            bedrock_req = {"anthropic_version": "bedrock-2023-05-31", "max_tokens": MAX_TOKENS,
                           "messages": f"{len(messages)} message(s); last: {_short(_last_text(messages[-1]))}",
                           "tools": [t["name"] for t in tools]}
            sigv4 = {"content-type": "application/json", "authorization": "AWS4-HMAC-SHA256 (agent execution role)"}
            try:
                response = await claude.messages.create(
                    model=MODEL_ID, max_tokens=MAX_TOKENS, system=system, messages=messages, **kwargs
                )
            except APIStatusError as exc:
                ex = _exchange("POST", bedrock_url, None, bedrock_req, exc.status_code, None, str(exc))
                ex["request"]["headers"] = sigv4
                yield hop.end("error", http_status=exc.status_code, error=_short(str(exc)), http=[ex])
                raise
            requested = [b.name for b in response.content if b.type == "tool_use"]
            ex = _exchange("POST", bedrock_url, None, bedrock_req, 200, {"content-type": "application/json"}, {
                "id": response.id, "stop_reason": response.stop_reason, "usage": {
                    "input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens},
                "content": [b.text if b.type == "text" else f"tool_use {b.name}" for b in response.content]})
            ex["request"]["headers"] = sigv4
            yield hop.end("ok", model=MODEL_ID, stop_reason=response.stop_reason, tools_requested=requested,
                          input_tokens=response.usage.input_tokens, output_tokens=response.usage.output_tokens,
                          http=[ex])

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
                if block.name not in tool_map:  # never offered to the model: refuse rather than fail the request
                    results.append({"type": "tool_result", "tool_use_id": block.id, "is_error": True,
                                    "content": f"Unknown tool {block.name}."})
                    continue
                adapter, remote_name, tool_name = tool_map[block.name]
                tools_used.append(f"{adapter}/{tool_name}")
                out: list = []
                async for event in _call_tool(tracer, rec, sessions, adapter, remote_name, tool_name, block.input, out):
                    yield event
                text, is_error = out[-1]
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": text, "is_error": is_error})
            messages.append({"role": "user", "content": results})
        yield ("result", "I couldn't finish that request within the step limit.", tools_used)
    finally:
        for ctx in reversed(stack):
            try:
                await ctx.__aexit__(None, None, None)
            except Exception:
                pass


async def _bedrock_agent_loop(tracer: Tracer, rec: "HttpRecorder", sessions: dict, tool_map: dict, prompt: str,
                              access: dict, tools_used: list):
    """Model loop on the Bedrock Agent. Its action groups (one per MCP server, return control) hand each tool call
    back here; it runs through the same gateways and per-user token as the Claude loop, and the result goes back."""
    by_function = {(adapter, short): (adapter, remote, short) for adapter, remote, short in tool_map.values()}
    denied = [a for a, v in access.items() if v == "denied"]
    unavailable = [a for a, v in access.items() if v == "unavailable"]
    note = ""
    if denied:
        note += f" The user is NOT authorized to use: {', '.join(denied)}."
    if unavailable:
        note += f" Temporarily unavailable (technical problem): {', '.join(unavailable)}."
    session_id = f"mcpdemo-{uuid.uuid4()}"
    url = (f"https://bedrock-agent-runtime.{REGION}.amazonaws.com/agents/{BEDROCK_AGENT_ID}/agentAliases/"
           f"{BEDROCK_AGENT_ALIAS_ID}/sessions/{session_id}/text")
    sigv4 = {"content-type": "application/json", "authorization": "AWS4-HMAC-SHA256 (agent execution role)"}
    request: dict = {"inputText": prompt + (f"\n\n(Access note:{note})" if note else "")}
    for turn in range(1, MAX_TURNS + 1):
        hop = tracer.hop("agent", "bedrock", f"InvokeAgent (turn {turn})")
        yield hop.start()
        try:
            text, control = await asyncio.to_thread(_invoke_bedrock_agent, sessionId=session_id, **request)
        except Exception as exc:
            ex = _exchange("POST", url, None, request, getattr(exc, "response", {}).get("ResponseMetadata", {}).get("HTTPStatusCode"), None, str(exc))
            ex["request"]["headers"] = sigv4
            yield hop.end("error", error=_short(str(exc)), http=[ex])
            yield ("result", "The Bedrock Agent call failed.", tools_used)
            return
        calls = [c["functionInvocationInput"] for c in (control or {}).get("invocationInputs", [])
                 if "functionInvocationInput" in c]
        ex = _exchange("POST", url, None, request, 200, {"content-type": "application/vnd.amazon.eventstream"},
                       {"returnControl": [f"{c['actionGroup']}.{c['function']}" for c in calls]} if control
                       else {"completion": text})
        ex["request"]["headers"] = sigv4
        yield hop.end("ok", agent=f"{BEDROCK_AGENT_ID}/{BEDROCK_AGENT_ALIAS_ID}", session=session_id,
                      tools_requested=[f"{c['actionGroup']}/{c['function']}" for c in calls], http=[ex])
        if not control:
            yield ("result", text, tools_used)
            return
        results = []
        for call in calls:
            # Action groups are named <adapter>-mcp (see scripts/bedrock-agent.sh).
            adapter, function = call["actionGroup"].removesuffix("-mcp"), call["function"]
            args = {p["name"]: int(p["value"]) if p.get("type") == "integer" else p["value"]
                    for p in call.get("parameters", [])}
            if (adapter, function) in by_function and adapter in sessions:
                _, remote, short = by_function[(adapter, function)]
                tools_used.append(f"{adapter}/{short}")
                out: list = []
                async for event in _call_tool(tracer, rec, sessions, adapter, remote, short, args, out):
                    yield event
                body = out[-1][0]
            else:  # the model chose a service this user may not use: tell it, without calling anything
                body = f"The user does not have access to the {adapter} service."
            results.append({"functionResult": {"actionGroup": call["actionGroup"], "function": function,
                                               "responseBody": {"TEXT": {"body": body}}}})
        request = {"sessionState": {"invocationId": control["invocationId"],
                                    "returnControlInvocationResults": results}}
    yield ("result", "I couldn't finish that request within the step limit.", tools_used)


@app.entrypoint
async def invoke(payload: dict, context: RequestContext):
    started = time.perf_counter()
    tracer = Tracer()
    prompt = (payload or {}).get("prompt", "").strip()
    if not prompt:
        yield {"type": "error", "message": "prompt is required"}
        return
    engine = (payload or {}).get("engine") or AGENT_ENGINE
    if engine not in ENGINES:
        yield {"type": "error", "message": f"engine {engine!r} is not available here (available: {', '.join(ENGINES)})"}
        return

    # The chatbot emits the "start" of this hop when it sends the request.
    received = tracer.hop("chatbot", "agent", "POST /invocations", hop_id="invoke")
    # What the agent received (after the AgentCore authorizer). The answer streams back as SSE.
    invoke_http = [_exchange("POST", "/invocations", context.request_headers, payload, 200,
                             {"content-type": "text/event-stream"}, None, token_label="token #1")]
    user_token = _bearer(context)
    if not user_token:
        yield received.end("denied", reason="missing bearer token", http=invoke_http)
        yield {"type": "error", "message": "missing bearer token"}
        return
    claims = _claims(user_token)
    token1 = _claims_summary(claims)
    if claims.get("azp") != CHAT_CLIENT_ID:
        yield received.end("denied", reason="token was not issued to the chatbot app", token=token1, http=invoke_http)
        yield {"type": "error", "message": "forbidden: token was not issued to the chatbot app"}
        return
    if "Agent.Invoke" not in claims.get("roles", []):
        yield received.end("denied", reason="the Agent.Invoke role is required", token=token1, http=invoke_http)
        yield {"type": "error", "message": "forbidden: the Agent.Invoke role is required"}
        return
    yield received.end("ok", token=token1, engine=engine, http=invoke_http)
    user = claims.get("preferred_username") or claims.get("oid")

    if TOOLS_VIA == "agentcore-gateway":
        # Token #1 goes to the AgentCore Gateway as-is; it does the on-behalf-of exchange per target.
        rec = HttpRecorder("token #1")
        async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {user_token}"}, timeout=30,
                                      event_hooks=rec.hooks()) as http:
            async for event in _finish(tracer, http, rec, prompt, None, user, started, engine):
                yield event
        return

    hop = tracer.hop("agent", "entra", "on-behalf-of token exchange")
    yield hop.start()
    gateway_token, obo_error = await asyncio.to_thread(_gateway_token, user_token)
    # MSAL makes this call; describe it (the secret and both tokens are never shown).
    obo_req = {"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "requested_token_use": "on_behalf_of",
               "client_id": AGENT_API_CLIENT_ID, "client_secret": "<from SSM>", "assertion": "<token #1>",
               "scope": f"api://{GATEWAY_API_CLIENT_ID}/access_as_user"}
    obo_url = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token"
    form = {"content-type": "application/x-www-form-urlencoded"}
    if gateway_token is None:
        print(json.dumps({"event": "obo_failed", "user": user, "error": obo_error}))
        yield hop.end("denied", error=_short(obo_error),
                      http=[_exchange("POST", obo_url, form, obo_req, 400, {"content-type": "application/json"},
                                      {"error": obo_error})])
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
    yield hop.end("ok", token=_claims_summary(_claims(gateway_token)),
                  http=[_exchange("POST", obo_url, form, obo_req, 200, {"content-type": "application/json"},
                                  {"token_type": "Bearer", "access_token": "<token #2>",
                                   "token #2 claims": _claims_summary(_claims(gateway_token))})])

    rec = HttpRecorder("token #2")
    async with httpx2.AsyncClient(
        headers={**GATEWAY_HEADERS, "Authorization": f"Bearer {gateway_token}"}, timeout=30, event_hooks=rec.hooks()
    ) as http:
        access: dict[str, str] = {}
        for adapter in MCP_ADAPTERS:
            hop = tracer.hop("agent", "gateway", f"GET /adapters/{adapter}", target=adapter)
            yield hop.start()
            mark = rec.mark()
            decision, status, body = await _adapter_access(http, adapter)
            access[adapter] = decision
            yield hop.end({"allowed": "ok", "denied": "denied", "unavailable": "error"}[decision], http_status=status,
                          http=rec.since(mark, body))
        async for event in _finish(tracer, http, rec, prompt, access, user, started, engine):
            yield event


async def _finish(tracer: Tracer, http: httpx2.AsyncClient, rec: HttpRecorder, prompt: str,
                  access: dict[str, str] | None, user: str | None, started: float, engine: str = AGENT_ENGINE):
    """Run the agent loop and emit the final event (shared by both TOOLS_VIA routes)."""
    answer, tools_used = "", []
    try:
        async for item in _run(tracer, http, rec, prompt, access, engine):
            if isinstance(item, tuple):
                if item[0] == "access":
                    access = item[1]
                else:
                    _, answer, tools_used = item
            else:
                yield item
    except APIStatusError as exc:  # Bedrock refused the call (model access, throttling, ...)
        print(json.dumps({"event": "model_error", "user": user, "status": exc.status_code, "error": str(exc)}))
        yield {"type": "error", "message": f"The model call failed (HTTP {exc.status_code}). Check Bedrock model access in {REGION}."}
        return

    access = access or {}
    allowed = [a for a, v in access.items() if v == "allowed"]
    denied = [a for a, v in access.items() if v == "denied"]
    unavailable = [a for a, v in access.items() if v == "unavailable"]
    ms = int((time.perf_counter() - started) * 1000)
    print(json.dumps({"event": "invocation", "user": user, "via": TOOLS_VIA, "engine": engine, "allowed": allowed, "denied": denied,
                      "unavailable": unavailable, "tools_used": tools_used, "ms": ms}))
    yield {"type": "final", "answer": answer, "engine": engine, "tools_used": tools_used, "services_allowed": allowed,
           "services_denied": denied, "services_unavailable": unavailable, "ms": ms}


if __name__ == "__main__":
    app.run()
