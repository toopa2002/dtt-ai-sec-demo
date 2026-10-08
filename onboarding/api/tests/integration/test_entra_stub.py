"""Spec 002: the agent's Entra tools against the stub ISC, in process (no model, no network). Checks the two halves
agree: every call on /v2026, multipart aggregation, the experimental header only where needed, delta off and back on,
the vaulted secret reaching the stub's PATCH unchanged, counts, the dataset fallback and no account ever created."""

import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "agent" / "src"))
sys.path.insert(0, str(ROOT / "deploy" / "stub-isc"))

import stub_isc  # noqa: E402
from onboarding_agent import playbooks  # noqa: E402
from onboarding_agent.isc import vault  # noqa: E402
from onboarding_agent.isc.client import IscClient  # noqa: E402
from onboarding_agent.isc.tools import IscTools  # noqa: E402

SECRET = "Xy78Q~abcdefghijklmnopqrstuvwxyz0123456"


class Events(list):
    async def __call__(self, event: dict) -> None:
        self.append(event)


@pytest.fixture
async def stub(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    await stub_isc.reset()

    async def get(provider: str, workload_name=None) -> str:  # type: ignore[no-untyped-def]
        return SECRET

    monkeypatch.setattr(vault, "get_application_secret", get)

    async def token() -> str:
        return "stub-token"

    pb = playbooks.load("entra-id")
    client = IscClient("stub", token, base_url="http://stub", transport=httpx.ASGITransport(app=stub_isc.app),
                       experimental=pb.settings["experimental_paths"])
    yield client
    await client.close()


def _session(capabilities: list[str]) -> dict:
    pb = playbooks.load("entra-id")
    return {"id": "s9", "connector_type": "entra-id", "tenant": {"name": "acme-demo"},
            "details": {"source_name": "Entra ID - Contoso", "source_owner": "w.rakkiatngam",
                        "tenant_domain": "contoso-demo.onmicrosoft.com", "app_name": "SailPoint ISC - acme-demo",
                        "capabilities": capabilities, "foundry_subscriptions": ["8b1e4c2a-0d3f-4a77-9c51-6f2e8a0b3d19"],
                        "upn_domain": "contoso.example", "usage_location": "TH", "source_mode": "new",
                        "client_id": "3f6a1c8e-52d4-4b0f-9a7e-c1d28e4b6a05"},
            "steps": {}, "source": None, "plan": [{"id": s["id"], "state": "todo"} for s in pb.plan],
            "application_secret": {"provider": "onboarding-entra-s9", "state": "received", "expires_on": "2027-10-08"}}


async def test_full_entra_proof_against_the_stub(stub) -> None:  # type: ignore[no-untyped-def]
    await stub_isc.entra_switch("delta_empty_peek")
    session = _session(["directory", "service_principals", "ai_agents", "provisioning"])
    events = Events()
    tools = IscTools(stub, playbooks.for_session(session), session, events, poll_seconds=0)
    assert (await tools.find_connector_sources())["sources"] == []
    assert (await tools.check_tenant_features()) == {"connector": True, "machine_identities": True}
    assert (await tools.create_source())["created"]
    assert (await tools.configure_source())["secret_applied"] is True
    assert (await tools.ensure_schema_attributes())["ok"]
    assert (await tools.peek_accounts())["accounts_read"] == 5  # delta switched off for the read
    assert (await tools.test_connection())["ok"]
    agg = await tools.start_aggregation()
    assert agg["ok"] and agg["entitlements"]["entitlements"] == 412
    assert agg["accounts"]["users"] == 64 and agg["accounts"]["service_principals"] == 161
    assert (await tools.set_machine_classification())["accounts_submitted"] == 225
    assert (await tools.aggregate_datasets())["ai_agents"] == 2
    assert (await tools.set_dataset_schedule())["schedule"] == "on"
    assert (await tools.set_provisioning_policy())["kept_existing"] is False
    assert (await tools.set_correlation())["ok"]
    state = await stub_isc.stub_state()
    source = state["sources"][0]
    assert source["connectorAttributes"]["deltaAggregationEnabled"] is True  # restored
    assert source["connectorAttributes"]["clientSecret"] == SECRET  # the vaulted value, exactly
    assert [p for p in state["entra"]["patches"] if "clientSecret" in p] == [state["entra"]["patches"][0]]
    assert state["entra"]["datasets"][source["id"]]["azure:foundry"]["aggregationEnabled"] is True
    assert state["entra"]["account_creates"] == 0
    assert state["entra"]["classification"][source["id"]]["classificationMethod"] == "CRITERIA"
    assert all(c.split()[1].startswith("/v2026/") for c in state["calls"])
    assert SECRET not in repr(events)


async def test_dataset_fallback_and_invalid_secret(stub) -> None:  # type: ignore[no-untyped-def]
    await stub_isc.entra_switch("dataset_unavailable")
    session = _session(["directory", "ai_agents"])
    events = Events()
    tools = IscTools(stub, playbooks.for_session(session), session, events, poll_seconds=0)
    await tools.create_source()
    await tools.configure_source()
    result = await tools.aggregate_datasets()
    assert result["tenant_limitation"] is True and "Entra ID - Contoso" in result["ui_path"]
    await stub_isc.aggregate_in_ui(session["source"]["id"])
    assert (await tools.count_ai_agents())["ai_agents"] == 2
    await stub_isc.entra_switch("secret_invalid")
    peek = await tools.peek_accounts()
    assert peek["ok"] is False and "AADSTS7000215" in peek["error"]


async def test_slow_account_aggregation_is_handed_over(stub) -> None:  # type: ignore[no-untyped-def]
    await stub_isc.entra_switch("slow_aggregation", polls=5)
    session = _session(["directory"])
    events = Events()
    tools = IscTools(stub, playbooks.for_session(session), session, events, poll_seconds=0)
    tools.pb.checks["aggregation"]["wait_in_turn_seconds"] = 0
    await tools.create_source()
    await tools.configure_source()
    result = await tools.start_aggregation()
    assert result["running"] is True and result["kind"] == "accounts"
    assert [e for e in events if e.get("type") == "action" and e.get("follow")]
