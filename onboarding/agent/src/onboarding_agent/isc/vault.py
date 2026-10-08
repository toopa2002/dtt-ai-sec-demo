"""The application secret, read in tool code only (spec 002 research R1, R2).

On AgentCore: the API-key credential provider the session API created for the session, read with the agent's
workload token (the same token path as the ISC M2M token in client.py). Local stub runs (ONBOARDING_ISC_TOKEN set):
the session API's loopback-only `/_local/apikey/{name}`, so the stub receives exactly what the owner submitted.
The value is returned to the calling tool, put into one PATCH op, and never logged, emitted or given to the model.
"""

import asyncio
import os

import httpx

from .client import IscError


class VaultError(RuntimeError):
    pass


async def _workload_token(workload_name: str | None) -> str:
    from bedrock_agentcore.runtime.context import BedrockAgentCoreContext
    from bedrock_agentcore.services.identity import IdentityClient

    wat = BedrockAgentCoreContext.get_workload_access_token()
    if wat:
        return wat
    name = workload_name or os.environ.get("ONBOARDING_WORKLOAD_NAME")
    if not name:
        raise VaultError("no workload identity name to request a workload access token")
    client = IdentityClient(os.environ.get("AWS_REGION", "ap-southeast-1"))
    result = await asyncio.to_thread(client.get_workload_access_token, name)
    return result["workloadAccessToken"]


async def get_application_secret(provider: str, workload_name: str | None = None) -> str:
    if not provider or not provider.startswith("onboarding-entra-"):
        raise VaultError("no application secret provider for this session")
    if os.environ.get("ONBOARDING_ISC_TOKEN"):  # local stub runs only; never set on AgentCore
        base = os.environ.get("ONBOARDING_LOCAL_VAULT_URL", "http://127.0.0.1:8080")
        async with httpx.AsyncClient(base_url=base, timeout=10) as http:
            response = await http.get(f"/_local/apikey/{provider}")
        if response.status_code != 200:
            raise VaultError(f"the local vault has no secret for this session (HTTP {response.status_code})")
        return response.json()["apiKey"]
    from bedrock_agentcore.services.identity import IdentityClient

    client = IdentityClient(os.environ.get("AWS_REGION", "ap-southeast-1"))
    try:
        token = await _workload_token(workload_name)
        result = client.get_api_key(provider_name=provider, agent_identity_token=token)
        value = await result if asyncio.iscoroutine(result) else result
    except Exception as exc:  # noqa: BLE001 — reported without the value
        raise VaultError(f"AgentCore Identity refused the application secret: {type(exc).__name__}") from None
    if not value:
        raise VaultError("the vault returned an empty application secret")
    return str(value)


_ = IscError
