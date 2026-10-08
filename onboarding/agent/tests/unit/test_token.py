"""AgentCore Identity token source: the agent requests its own workload token when the runtime context has none."""

import sys
import types

import pytest

from onboarding_agent.isc.client import IscError, agentcore_token_fn


@pytest.fixture
def identity(monkeypatch):  # type: ignore[no-untyped-def]
    calls: dict[str, list] = {"workload": [], "token": []}

    class FakeIdentityClient:
        def __init__(self, region: str) -> None:
            pass

        def get_workload_access_token(self, name: str) -> dict:
            calls["workload"].append(name)
            return {"workloadAccessToken": "wat-from-api"}

        async def get_token(self, *, provider_name, scopes, agent_identity_token, auth_flow):  # type: ignore[no-untyped-def]
            calls["token"].append((provider_name, agent_identity_token, auth_flow))
            if provider_name == "onboarding-isc-bad":
                raise ValueError("invalid_client")
            return "isc-access-token"

    class Ctx:
        value: str | None = None

        @classmethod
        def get_workload_access_token(cls) -> str | None:
            return cls.value

    monkeypatch.setitem(sys.modules, "bedrock_agentcore.services.identity",
                        types.SimpleNamespace(IdentityClient=FakeIdentityClient))
    monkeypatch.setitem(sys.modules, "bedrock_agentcore.runtime.context",
                        types.SimpleNamespace(BedrockAgentCoreContext=Ctx))
    calls["ctx"] = Ctx  # type: ignore[assignment]
    return calls


async def test_requests_own_workload_token_without_context(identity) -> None:  # type: ignore[no-untyped-def]
    get = agentcore_token_fn("onboarding-isc-acme", "ap-southeast-1", "isc_onboarding_agent-ABC")
    assert await get() == "isc-access-token"
    assert identity["workload"] == ["isc_onboarding_agent-ABC"]
    assert identity["token"] == [("onboarding-isc-acme", "wat-from-api", "M2M")]
    assert await get() == "isc-access-token"  # cached
    assert len(identity["token"]) == 1


async def test_context_token_wins(identity) -> None:  # type: ignore[no-untyped-def]
    identity["ctx"].value = "wat-from-context"
    get = agentcore_token_fn("onboarding-isc-acme", "ap-southeast-1", "isc_onboarding_agent-ABC")
    await get()
    assert identity["workload"] == []
    assert identity["token"][0][1] == "wat-from-context"


async def test_errors_distinguish_agent_side_from_rejected_credential(identity, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("ONBOARDING_WORKLOAD_NAME", raising=False)
    with pytest.raises(IscError) as no_name:
        await agentcore_token_fn("onboarding-isc-acme", "ap-southeast-1")()
    assert no_name.value.status == 0
    with pytest.raises(IscError) as rejected:
        await agentcore_token_fn("onboarding-isc-bad", "ap-southeast-1", "isc_onboarding_agent-ABC")()
    assert rejected.value.status == 401
