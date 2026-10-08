"""Shared fixtures: an ISC mocked with respx (shapes from the skill's references/isc-api.md and live tenants), the AWS
SaaS playbook for a sample session, and an event collector."""

import httpx
import pytest
import respx

from onboarding_agent import playbooks
from onboarding_agent.isc.client import IscClient

BASE = "https://acme-demo.api.identitynow-demo.com"
EXTERNAL_ID = "5c0d9a3e-7b14-4f2a-9e61-2d8a4c7b1f90"
SPEC_ID = "6e47875b-73f1-481d-a613-59d4deca0c6a"


def sample_session(source=None) -> dict:  # type: ignore[no-untyped-def]
    return {
        "id": "s1", "connector_type": "aws-saas",
        "tenant": {"name": "acme-demo", "api_host": "acme-demo.api.identitynow-demo.com",
                   "credential_provider": "onboarding-isc-acme-demo", "external_id": EXTERNAL_ID},
        "details": {"source_name": "AWS - Acme Org", "source_owner": "w.rakkiatngam",
                    "management_account_id": "111122223333", "accounts": ["111122223333", "444455556666"],
                    "region": "ap-southeast-1", "role_name": "SailPointISCRole-acme-demo",
                    "bedrock_regions": [], "agentcore_regions": ["ap-southeast-1"]},
        "steps": {}, "source": source,
    }


class Collector:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def __call__(self, event: dict) -> None:
        self.events.append(event)

    def of(self, kind: str) -> list[dict]:
        return [e for e in self.events if e["type"] == kind]


@pytest.fixture
def emit() -> Collector:
    return Collector()


@pytest.fixture
def isc_mock():  # type: ignore[no-untyped-def]
    with respx.mock(base_url=BASE, assert_all_called=False) as mock:
        mock.get("/beta/tenant").respond(200, json={"products": [{"productName": "idn",
                                                                  "attributes": {"externalId": EXTERNAL_ID}}]})
        mock.get("/v3/public-identities").respond(200, json=[{"id": "a" * 32, "name": "w.rakkiatngam"}])
        yield mock


@pytest.fixture
async def isc():  # type: ignore[no-untyped-def]
    async def token() -> str:
        return "test-token"

    client = IscClient("acme-demo.api.identitynow-demo.com", token, base_url=BASE, transport=None)
    yield client
    await client.close()


@pytest.fixture
def pb():  # type: ignore[no-untyped-def]
    return playbooks.for_session(sample_session())


_ = httpx  # keep the import: respx patches httpx
