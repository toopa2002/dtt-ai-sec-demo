"""SailPoint ISC REST client (research R4). The access token comes from AgentCore Identity (M2M flow on the tenant's
credential provider); the agent never sees the client secret. Errors are returned as text with tokens stripped."""

import asyncio
import inspect
import os
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

TokenFn = Callable[[], Awaitable[str]]
_TOKEN_RE = re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]+")


class IscError(RuntimeError):
    def __init__(self, status: int, method: str, path: str, text: str):
        self.status, self.method, self.path = status, method, path
        super().__init__(f"{method} {path} -> HTTP {status}: {_TOKEN_RE.sub('[token]', text)[:1500]}")


def agentcore_token_fn(provider_name: str, region: str, workload_name: str | None = None) -> TokenFn:
    """A token source backed by AgentCore Identity, cached until shortly before expiry.

    AgentCore puts a workload access token in the request context only when the caller names an end user. The
    session API invokes the runtime as a service, so the agent asks for its own workload token (GetWorkloadAccessToken
    on the standalone workload identity the API names; the runtime's own identity is service-linked and refuses) and
    uses it for the tenant's M2M credential provider."""
    from bedrock_agentcore.runtime.context import BedrockAgentCoreContext
    from bedrock_agentcore.services.identity import IdentityClient

    client = IdentityClient(region)
    cache: dict[str, Any] = {"token": None, "at": 0.0}

    async def workload_token() -> str:
        wat = BedrockAgentCoreContext.get_workload_access_token()
        if wat:
            return wat
        name = workload_name or os.environ.get("ONBOARDING_WORKLOAD_NAME")
        if not name:
            raise IscError(0, "TOKEN", provider_name, "no workload identity name to request a workload access token")
        try:
            result = await asyncio.to_thread(client.get_workload_access_token, name)
        except Exception as exc:  # noqa: BLE001 — reported as an agent-side token problem, not a credential one
            raise IscError(0, "TOKEN", provider_name, f"workload access token refused: {type(exc).__name__}: {exc}") \
                from None
        return result["workloadAccessToken"]

    async def get() -> str:
        if cache["token"] and time.monotonic() - cache["at"] < 600:
            return cache["token"]
        wat = await workload_token()
        try:
            result = client.get_token(provider_name=provider_name, scopes=[], agent_identity_token=wat,
                                      auth_flow="M2M")
            token = await result if inspect.isawaitable(result) else result
        except Exception as exc:  # noqa: BLE001 — SailPoint refused the stored credential (or the provider is gone)
            raise IscError(401, "TOKEN", provider_name, f"{type(exc).__name__}: {exc}") from None
        cache.update(token=token, at=time.monotonic())
        return token

    return get


class IscClient:
    """`experimental`: None keeps the original behaviour (the experimental header on every call, AWS SaaS); a list of
    path regexes sends it only where an endpoint needs it (playbook `experimental_paths`, spec 002 FR-138)."""

    def __init__(self, api_host: str, token_fn: TokenFn, base_url: str | None = None,
                 transport: httpx.AsyncBaseTransport | None = None, experimental: list[str] | None = None):
        self.base = (base_url or os.environ.get("ONBOARDING_ISC_BASE_URL") or f"https://{api_host}").rstrip("/")
        self._token_fn = token_fn
        self._http = httpx.AsyncClient(base_url=self.base, timeout=60, transport=transport)
        self._experimental = None if experimental is None else [re.compile(p) for p in experimental]

    def _needs_experimental(self, path: str) -> bool:
        if self._experimental is None:
            return True
        bare = path.split("?", 1)[0]
        return any(p.search(bare) for p in self._experimental)

    async def close(self) -> None:
        await self._http.aclose()

    async def _send(self, method: str, path: str, *, json: Any = None, content: str | None = None,
                    content_type: str | None = None, params: dict | None = None,
                    files: dict | None = None) -> httpx.Response:
        headers = {"Authorization": f"Bearer {await self._token_fn()}", "Accept": "application/json, */*"}
        if self._needs_experimental(path):
            headers["X-SailPoint-Experimental"] = "true"
        if content_type:
            headers["Content-Type"] = content_type
        response = await self._http.request(method, path, json=json, content=content, params=params, headers=headers,
                                            files=files)
        if response.status_code >= 400:
            raise IscError(response.status_code, method, path, response.text)
        return response

    @staticmethod
    def _body(response: httpx.Response) -> Any:
        if not response.content:
            return {}
        if "json" in response.headers.get("content-type", ""):
            return response.json()
        return response.text

    async def request(self, method: str, path: str, *, json: Any = None, content: str | None = None,
                      content_type: str | None = None, params: dict | None = None) -> Any:
        return self._body(await self._send(method, path, json=json, content=content, content_type=content_type,
                                           params=params))

    async def post_multipart(self, path: str, fields: dict[str, str] | None = None) -> Any:
        """multipart/form-data, as v2026 load-accounts / load-entitlements expect; `fields` may be empty."""
        if fields:
            return self._body(await self._send("POST", path, files={k: (None, v) for k, v in fields.items()}))
        boundary = "onboarding-boundary"  # an empty multipart body still needs its closing boundary
        return self._body(await self._send("POST", path, content=f"--{boundary}--\r\n",
                                           content_type=f"multipart/form-data; boundary={boundary}"))

    async def count(self, path: str, filters: str) -> int:
        """A collection's size from X-Total-Count (`count=true&limit=1`)."""
        response = await self._send("GET", path, params={"filters": filters, "count": "true", "limit": "1"})
        return int(response.headers.get("X-Total-Count") or 0)

    async def get(self, path: str, **kw: Any) -> Any:
        return await self.request("GET", path, **kw)

    async def post(self, path: str, **kw: Any) -> Any:
        return await self.request("POST", path, **kw)

    async def patch_json(self, path: str, ops: list[dict]) -> Any:
        import json as _json

        return await self.request("PATCH", path, content=_json.dumps(ops), content_type="application/json-patch+json")
