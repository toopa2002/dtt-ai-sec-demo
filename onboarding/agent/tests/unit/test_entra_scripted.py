"""Spec 002 (T029, quickstart §2): whole Entra turns with the scripted model, the real tool loop, the real tools and
the stub ISC in process. No model call, no network: what the browser e2e runs, minus the browser and the API."""

import sys
from pathlib import Path

import httpx
import pytest

from onboarding_agent import fake_model, loop, playbooks
from onboarding_agent.isc import vault
from onboarding_agent.isc.client import IscClient
from onboarding_agent.isc.tools import IscTools
from onboarding_agent.tools.session import SessionTools

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "deploy" / "stub-isc"))
import stub_isc  # noqa: E402

SECRET = "Xy78Q~abcdefghijklmnopqrstuvwxyz0123456"


class Events(list):
    async def __call__(self, event: dict) -> None:
        self.append(event)

    def of(self, kind: str) -> list[dict]:
        return [e for e in self if e.get("type") == kind]


@pytest.fixture
async def isc(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    await stub_isc.reset()

    async def get(provider: str, workload_name=None) -> str:  # type: ignore[no-untyped-def]
        return SECRET

    monkeypatch.setattr(vault, "get_application_secret", get)

    async def token() -> str:
        return "stub-token"

    client = IscClient("stub", token, base_url="http://stub", transport=httpx.ASGITransport(app=stub_isc.app),
                       experimental=playbooks.load("entra-id").settings["experimental_paths"])
    yield client
    await client.close()


def _session(capabilities: list[str], **extra) -> dict:  # type: ignore[no-untyped-def]
    pb = playbooks.load("entra-id")
    return {"id": "s7", "connector_type": "entra-id", "tenant": {"name": "acme-demo", "api_host": "stub"},
            "details": {"source_name": "Entra ID - Contoso", "source_owner": "w.rakkiatngam",
                        "tenant_domain": "contoso-demo.onmicrosoft.com", "app_name": "SailPoint ISC - acme-demo",
                        "capabilities": capabilities, "foundry_subscriptions": ["8b1e4c2a-0d3f-4a77-9c51-6f2e8a0b3d19"],
                        "upn_domain": "contoso.example", "usage_location": "TH", "source_mode": "new",
                        "client_id": "3f6a1c8e-52d4-4b0f-9a7e-c1d28e4b6a05"},
            "steps": {}, "source": None, "plan": [{"id": s["id"], "title": s["title"], "actor": s["actor"],
                                                   "state": "todo"} for s in pb.plan],
            "application_secret": {"provider": "onboarding-entra-s7", "state": "received", "expires_on": "2027-10-08"},
            **extra}


async def _turn(isc, session: dict, role: str, text: str, **payload_extra) -> Events:  # type: ignore[no-untyped-def]
    events = Events()
    payload = {"mode": "turn", "turn_id": "t1", "lang": "en",
               "ordered_by": {"user_id": "u", "role": role, "display_name": "Wichai" if role == "iam_engineer" else "Ploy"},
               "session": session, "history": [], "message": {"seq": 1, "thread": role, "speaker": role, "text": text},
               "images": [], "waiting_on": None, "check_order": None, "suggestion_defaults": {}, **payload_extra}
    pb = playbooks.for_session(session)
    tools = IscTools(isc, pb, session, events, poll_seconds=0)
    await loop.run_turn(payload, pb, tools, SessionTools(events, role), events, claude=fake_model.FakeModel())
    return events


async def test_directory_order_runs_the_proof_in_order(isc) -> None:  # type: ignore[no-untyped-def]
    session = _session(["directory"])
    events = await _turn(isc, session, "iam_engineer", "Create the connector and run the checks")
    final = [e for e in events.of("action") if e["result"] != "running"]
    assert [a["action"] for a in final] == ["create_source", "configure_source", "connection_check",
                                            "test_connection", "aggregate_entitlements", "aggregate_accounts"]
    assert all(a["result"] == "ok" for a in final)
    assert final[1]["request"]["fields"]["clientSecret"] == "[vaulted]"
    assert "All checks passed" in events.of("final")[0]["text"]
    assert SECRET not in repr(events)
    state = await stub_isc.stub_state()
    assert state["sources"][0]["connectorAttributes"]["clientSecret"] == SECRET


async def test_all_capabilities_and_the_dataset_fallback(isc) -> None:  # type: ignore[no-untyped-def]
    await stub_isc.entra_switch("dataset_unavailable")
    session = _session(["directory", "service_principals", "ai_agents", "provisioning"])
    events = await _turn(isc, session, "iam_engineer", "Create the connector and run the checks")
    names = [a["action"] for a in events.of("action") if a["result"] != "running"]
    assert "ensure_schema_attributes" in names and "aggregate_datasets" in names
    limitation = next(a for a in events.of("action") if a["action"] == "aggregate_datasets")
    assert limitation["result"] == "tenant_limitation"
    assert "tenant limitation" in events.of("final")[0]["text"]
    await stub_isc.aggregate_in_ui(session["source"]["id"])
    done = await _turn(isc, session, "iam_engineer", "done, I started it in ISC")
    assert next(a for a in done.of("action") if a["action"] == "aggregate_datasets")["response"]["counts"] == \
        {"ai_agents": 2}
    assert any(a["action"] == "set_dataset_schedule" and a["result"] == "ok" for a in done.of("action"))


async def test_secret_pasted_in_chat_asks_for_a_new_one(isc) -> None:  # type: ignore[no-untyped-def]
    events = await _turn(isc, _session(["directory"]), "application_owner", "here it is: [masked]",
                         secret_exposed=True)
    assert events.of("secret_needed") and "secret field" in events.of("final")[0]["text"]
    assert not events.of("action")


async def test_invalid_secret_is_diagnosed_and_a_new_one_requested(isc) -> None:  # type: ignore[no-untyped-def]
    await stub_isc.entra_switch("secret_invalid")
    events = await _turn(isc, _session(["directory"]), "iam_engineer", "Create the connector and run the checks")
    assert events.of("secret_needed") and "AADSTS7000215" in events.of("final")[0]["text"]


async def test_extend_source_adopts_and_only_adds(isc) -> None:  # type: ignore[no-untyped-def]
    first = _session(["directory"])
    await _turn(isc, first, "iam_engineer", "Create the connector and run the checks")
    second = _session(["service_principals"], mode=None)
    second["details"]["source_mode"] = "extend"
    second["application_secret"] = {"provider": "onboarding-entra-s8", "state": "missing"}
    events = await _turn(isc, second, "iam_engineer", "Extend the existing source")
    assert events.of("source")[0]["adopted"] is True
    patches = (await stub_isc.stub_state())["entra"]["patches"]
    extend_patch = next(p for p in patches if "manageAzureServicePrincipalAsAccount" in p)
    assert "clientSecret" not in extend_patch and "domainName" not in extend_patch
    assert sum(1 for p in patches if "clientSecret" in p) == 1  # only the first session's configure
    assert any(a["action"] == "ensure_schema_attributes" and a["result"] == "ok" for a in events.of("action"))
