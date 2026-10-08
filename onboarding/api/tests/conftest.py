"""Integration fixtures: a throwaway MongoDB (ONB_TEST_MONGO_URI, default the local test container on :27018), the app
over ASGI, SailPoint mocked with respx, AgentCore Identity and the agent runtime replaced by fakes."""

import json
import os
import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
import respx

os.environ.setdefault("MONGO_URI", os.environ.get(
    "ONB_TEST_MONGO_URI", "mongodb://127.0.0.1:27018/?replicaSet=rs0&directConnection=true"))
os.environ["MONGO_DB"] = f"onboarding_test_{uuid.uuid4().hex[:8]}"
os.environ["COOKIE_SECURE"] = "false"
os.environ["ISC_BASE_URL"] = "https://isc.test"

from onboarding_api import db  # noqa: E402
from onboarding_api.agent_client import client as agent_client  # noqa: E402
from onboarding_api.auth import users  # noqa: E402
from onboarding_api.main import app  # noqa: E402
from onboarding_api.tenants import identity  # noqa: E402

PAT_ID = "a1b2c3d4e5f60718293a4b5c6d7e8f90"
PAT_SECRET = "3f6c1d0e9b8a7f6e5d4c3b2a19087f6e5d4c3b2a19087f6e5d4c3b2a19087f6e"
EXTERNAL_ID = "5c0d9a3e-7b14-4f2a-9e61-2d8a4c7b1f90"


class FakeStore(identity.CredentialStore):
    def __init__(self) -> None:
        self.saved: dict[str, tuple[str, str]] = {}

    def put(self, name, api_host, client_id, client_secret):  # type: ignore[no-untyped-def]
        self.saved[name] = (client_id, client_secret)

    def delete(self, name):  # type: ignore[no-untyped-def]
        self.saved.pop(name, None)


class FakeAgent:
    """Replays a scripted list of agent events per call; records the payloads it was sent."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.script: list[list[dict]] = []

    async def invoke(self, payload, runtime_session_id):  # type: ignore[no-untyped-def]
        json.dumps(payload)  # the real client sends JSON: a payload that can't be serialised must fail here too
        self.calls.append(payload)
        if payload.get("mode") == "secret_check":
            import base64
            data = base64.b64decode(payload.get("images", [{}])[0].get("data_b64", ""))
            yield {"type": "secret_check", "result": "held" if b"SECRET" in data else "passed",
                   "reason": "shows an access key"}
            return
        if payload.get("mode") == "tenant_check":
            yield {"type": "tenant_check", "status": "usable", "external_id": EXTERNAL_ID}
            return
        events = self.script.pop(0) if self.script else [{"type": "final", "text": "ok"}]
        for ev in events:
            yield ev


@pytest.fixture(scope="session")
async def setup_db() -> AsyncIterator[None]:
    await db.ensure_indexes()
    yield
    await db.client().drop_database(os.environ["MONGO_DB"])
    await db.close()


@pytest.fixture
def fake_store(monkeypatch: pytest.MonkeyPatch) -> FakeStore:
    store = FakeStore()
    monkeypatch.setattr(identity, "store", lambda: store)
    return store


@pytest.fixture
def fake_agent(monkeypatch: pytest.MonkeyPatch) -> FakeAgent:
    fake = FakeAgent()
    monkeypatch.setattr(agent_client, "invoke", fake.invoke)
    return fake


@pytest.fixture
def isc() -> respx.MockRouter:
    with respx.mock(base_url="https://isc.test", assert_all_called=False) as mock:
        mock.post("/oauth/token").respond(200, json={"access_token": "eyJhbGciOiJIUzI1NiJ9.e30.sig", "expires_in": 600})
        mock.get("/beta/tenant").respond(200, json={"name": "acme-demo", "products": [
            {"productName": "idn", "attributes": {"externalId": EXTERNAL_ID}}]})
        yield mock


async def make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def sign_in(c: httpx.AsyncClient, username: str, password: str) -> dict:
    r = await c.post("/onboarding/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    me = r.json()
    c.headers["X-CSRF-Token"] = me["csrf_token"]
    return me


async def new_user(role: str, is_admin: bool = False) -> tuple[str, str]:
    name = f"{role[:3]}{uuid.uuid4().hex[:6]}"
    password = "correct horse battery"
    await users.create_user(name, name.title(), role, password, is_admin)
    return name, password
