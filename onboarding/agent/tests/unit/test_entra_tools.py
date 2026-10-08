"""Spec 002 tool tests against a mocked v2026 tenant (shapes from the Entra skill's references/isc-api.md):
T030 (configure with the vaulted secret, aggregation sequence and full read, existing sources), T064 (service-principal
schema and counts), T069 (datasets and the tenant limitation), T079 (provisioning without directory writes) and T085
(extend-source)."""

import json

import pytest
import respx

from onboarding_agent import loop, playbooks
from onboarding_agent.isc import vault
from onboarding_agent.isc.client import IscClient
from onboarding_agent.isc.tools import IscTools

BASE = "https://acme-demo.api.identitynow-demo.com"
EXPERIMENTAL = playbooks.load("entra-id").settings["experimental_paths"]
SID = "2c9180887a3b4c5d6e7f809112233445"
OWNER = "a" * 32
SECRET = "Xy78Q~abcdefghijklmnopqrstuvwxyz0123456"
CLIENT_ID = "3f6a1c8e-52d4-4b0f-9a7e-c1d28e4b6a05"


def entra_session(capabilities=("directory",), source=None, vault_state="received", mode=None,
                  details=None) -> dict:  # type: ignore[no-untyped-def]
    pb = playbooks.load("entra-id")
    session = {
        "id": "s2", "connector_type": "entra-id",
        "tenant": {"name": "acme-demo", "api_host": "acme-demo.api.identitynow-demo.com",
                   "credential_provider": "onboarding-isc-acme-demo"},
        "details": {"source_name": "Entra ID - Contoso", "source_owner": "w.rakkiatngam",
                    "tenant_domain": "contoso-demo.onmicrosoft.com", "app_name": "SailPoint ISC - acme-demo",
                    "capabilities": list(capabilities), "foundry_subscriptions": [], "upn_domain": "contoso.example",
                    "usage_location": "TH", "source_mode": "new", "client_id": CLIENT_ID, **(details or {})},
        "steps": {}, "source": source,
        "plan": [{"id": s["id"], "state": "todo"} for s in pb.plan],
        "application_secret": {"provider": "onboarding-entra-s2", "state": vault_state, "expires_on": "2027-10-08"},
    }
    if mode:
        session["mode"] = mode
    return session


class Collector:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def __call__(self, event: dict) -> None:
        self.events.append(event)

    def of(self, kind: str) -> list[dict]:
        return [e for e in self.events if e["type"] == kind]

    def final_actions(self) -> list[dict]:
        return [e for e in self.of("action") if e["result"] != "running"]


@pytest.fixture
def emit() -> Collector:
    return Collector()


@pytest.fixture
async def isc():  # type: ignore[no-untyped-def]
    async def token() -> str:
        return "t"

    client = IscClient("h", token, base_url=BASE, experimental=EXPERIMENTAL)
    yield client
    await client.close()


@pytest.fixture
def mock():  # type: ignore[no-untyped-def]
    with respx.mock(base_url=BASE, assert_all_called=False) as m:
        m.get("/v2026/public-identities").respond(200, json=[{"id": OWNER, "name": "w.rakkiatngam"}])
        yield m


@pytest.fixture(autouse=True)
def fake_vault(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    calls: list[str] = []

    async def get(provider: str, workload_name=None) -> str:  # type: ignore[no-untyped-def]
        calls.append(provider)
        return SECRET

    monkeypatch.setattr(vault, "get_application_secret", get)
    return calls


def tools_for(isc, session, emit) -> IscTools:  # type: ignore[no-untyped-def]
    return IscTools(isc, playbooks.for_session(session), session, emit, poll_seconds=0)


def source_doc(delta: bool = True, **attrs) -> dict:  # type: ignore[no-untyped-def]
    return {"id": SID, "name": "Entra ID - Contoso", "connector": "Microsoft-Entra",
            "owner": {"id": OWNER, "name": "w.rakkiatngam"},
            "connectorAttributes": {"domainName": "contoso-demo.onmicrosoft.com", "deltaAggregationEnabled": delta,
                                    **attrs}}


# ---------------------------------------------------------------- T030: configure, sequence, existing sources
async def test_configure_puts_the_vault_value_in_the_patch_only(isc, mock, emit, fake_vault) -> None:  # type: ignore[no-untyped-def]
    patch = mock.patch(f"/v2026/sources/{SID}").respond(200, json={})
    session = entra_session(source={"id": SID, "name": "Entra ID - Contoso"})
    result = await tools_for(isc, session, emit).configure_source()
    assert result["configured"] and result["secret_applied"] is True
    assert SECRET not in json.dumps(result)
    ops = {op["path"].split("/")[-1]: op["value"] for op in json.loads(patch.calls.last.request.content)}
    assert ops["clientSecret"] == SECRET and ops["clientID"] == CLIENT_ID
    assert ops["domainName"] == "contoso-demo.onmicrosoft.com" and ops["grantType"] == "CLIENT_CREDENTIALS"
    assert ops["aggregateAllGroups"] is True and ops["deltaAggregationEnabled"] is True
    assert "manageAzureServicePrincipalAsAccount" not in ops  # capability not chosen
    assert ops["enableManagedIdentityManagement"] is False  # directory only: managed identities stay off
    assert "enableAIFoundryAgent" not in ops
    assert fake_vault == ["onboarding-entra-s2"]
    action = emit.of("action")[0]
    assert SECRET not in json.dumps(emit.events)
    assert action["request"]["fields"]["clientSecret"] == "[vaulted]"
    assert action["secret_applied"] is True and action["summary"].startswith("clientSecret: [vaulted]")
    assert "X-SailPoint-Experimental" not in patch.calls.last.request.headers


async def test_configure_refuses_without_a_secret(isc, mock, emit) -> None:  # type: ignore[no-untyped-def]
    patch = mock.patch(f"/v2026/sources/{SID}")
    session = entra_session(source={"id": SID, "name": "n"}, vault_state="missing")
    result = await tools_for(isc, session, emit).configure_source()
    assert result["configured"] is False and "secret field" in result["error"]
    assert not patch.called


async def test_configure_after_vault_deletion_sends_no_secret(isc, mock, emit, fake_vault) -> None:  # type: ignore[no-untyped-def]
    patch = mock.patch(f"/v2026/sources/{SID}").respond(200, json={})
    session = entra_session(source={"id": SID, "name": "n"}, vault_state="vault_deleted")
    result = await tools_for(isc, session, emit).configure_source()
    assert result["configured"] and result["secret_applied"] is False and fake_vault == []
    assert "clientSecret" not in patch.calls.last.request.content.decode()


async def test_create_source_on_v2026_without_external_id(isc, mock, emit) -> None:  # type: ignore[no-untyped-def]
    mock.get("/v2026/sources").respond(200, json=[])
    create = mock.post("/v2026/sources").respond(201, json={"id": SID, "name": "Entra ID - Contoso",
                                                           "connectorAttributes": {"spConnectorInstanceId": "i"}})
    tenant = mock.get("/v2026/tenant")
    result = await tools_for(isc, entra_session(), emit).create_source()
    assert result["created"]
    body = json.loads(create.calls.last.request.content)
    assert body["connector"] == "Microsoft-Entra"
    assert body["connectorAttributes"]["spConnectorSpecId"] == "b7e9374a-3c50-4b51-8880-e501f947bbf8"
    assert body["connectorAttributes"]["idnProxyType"] == "sp-connect"
    assert not tenant.called
    assert all(c.request.url.path.startswith("/v2026/") for c in mock.calls)


def _aggregation_mocks(mock, delta=True, accounts=4973, sps=None, running=False):  # type: ignore[no-untyped-def]
    mock.get(f"/v2026/sources/{SID}").respond(200, json=source_doc(delta=delta))
    if running:
        mock.get("/v2026/task-status/ta").respond(200, json={"completionStatus": None})
    patches = mock.patch(f"/v2026/sources/{SID}").respond(200, json={})
    mock.post(f"/v2026/sources/{SID}/load-entitlements").respond(202, json={"id": "te"})
    mock.post(f"/v2026/sources/{SID}/load-accounts").respond(202, json={"task": {"id": "ta"}})
    mock.get(url__regex=r"/v2026/task-status/.*").respond(200, json={"completionStatus": "SUCCESS"})
    mock.get("/v2026/entitlements").respond(200, json=[{}], headers={"X-Total-Count": "2418"})

    def accounts_page(request):  # type: ignore[no-untyped-def]
        params = request.url.params
        if params.get("count") == "true":
            return respx.MockResponse(200, json=[{}], headers={"X-Total-Count": str(accounts)})
        offset = int(params.get("offset", 0))
        n = max(0, min(250, accounts - offset))
        sp_left = max(0, (sps or 0) - offset)
        return respx.MockResponse(200, json=[{"attributes": {"spn_servicePrincipalType": "Application"}}
                                             if i < sp_left else {"attributes": {}} for i in range(n)])

    mock.get("/v2026/accounts").mock(side_effect=accounts_page)
    return patches


async def test_aggregation_runs_entitlements_then_accounts_with_delta_off_and_restored(isc, mock, emit) -> None:  # type: ignore[no-untyped-def]
    patches = _aggregation_mocks(mock)
    session = entra_session(source={"id": SID, "name": "Entra ID - Contoso"})
    result = await tools_for(isc, session, emit).start_aggregation()
    assert result["ok"] and result["entitlements"]["entitlements"] == 2418 and result["accounts"]["accounts"] == 4973
    paths = [c.request.url.path for c in mock.calls if c.request.method == "POST"]
    assert paths == [f"/v2026/sources/{SID}/load-entitlements", f"/v2026/sources/{SID}/load-accounts"]
    toggles = [json.loads(c.request.content)[0]["value"] for c in patches.calls]
    assert toggles == [False, True]  # off for the account read, then restored
    load = next(c for c in mock.calls if c.request.url.path.endswith("load-accounts"))
    assert load.request.headers["content-type"].startswith("multipart/form-data")
    finals = emit.final_actions()
    assert [a["action"] for a in finals] == ["aggregate_entitlements", "aggregate_accounts"]
    assert finals[1]["summary"] == "delta off → restored · 4,973 accounts"
    assert finals[1]["response"]["counts"]["users"] == 4973
    count = next(c for c in mock.calls if c.request.url.path == "/v2026/accounts")
    assert count.request.url.params["filters"] == f'source.id eq "{SID}"'


async def test_delta_is_restored_when_the_account_aggregation_fails(isc, mock, emit) -> None:  # type: ignore[no-untyped-def]
    patches = _aggregation_mocks(mock)
    mock.post(f"/v2026/sources/{SID}/load-accounts").respond(500, json={"error": "boom"})
    session = entra_session(source={"id": SID, "name": "n"})
    result = await tools_for(isc, session, emit).start_aggregation()
    assert result["ok"] is False and result["failed"] == "accounts"
    assert [json.loads(c.request.content)[0]["value"] for c in patches.calls] == [False, True]


async def test_still_running_aggregation_is_handed_to_the_followup(isc, mock, emit) -> None:  # type: ignore[no-untyped-def]
    _aggregation_mocks(mock, running=True)
    session = entra_session(source={"id": SID, "name": "n"})
    tools = tools_for(isc, session, emit)
    tools.pb.checks["aggregation"]["wait_in_turn_seconds"] = 0
    result = await tools.start_aggregation()
    assert result["running"] is True and result["kind"] == "accounts"
    follow = [a for a in emit.of("action") if a.get("follow")]
    assert follow and follow[0]["result"] == "running" and follow[0]["plan_step"] == "aggregate_accounts"


async def test_peek_reads_with_delta_off(isc, mock, emit) -> None:  # type: ignore[no-untyped-def]
    mock.get(f"/v2026/sources/{SID}").respond(200, json=source_doc())
    patches = mock.patch(f"/v2026/sources/{SID}").respond(200, json={})
    mock.post(f"/v2026/sources/{SID}/connector/peek-resource-objects").respond(
        200, json={"resourceObjects": [{"identity": "a"}, {"identity": "b"}]})
    result = await tools_for(isc, entra_session(source={"id": SID, "name": "n"}), emit).peek_accounts()
    assert result["ok"] and result["accounts_read"] == 2
    assert [json.loads(c.request.content)[0]["value"] for c in patches.calls] == [False, True]


async def test_find_connector_sources_matches_the_tenant(isc, mock, emit) -> None:  # type: ignore[no-untyped-def]
    route = mock.get("/v2026/sources").respond(200, json=[
        source_doc(), {**source_doc(), "id": "other", "connectorAttributes": {"domainName": "else.onmicrosoft.com"}}])
    result = await tools_for(isc, entra_session(), emit).find_connector_sources()
    assert [s["id"] for s in result["sources"]] == [SID] and result["sources"][0]["owner"] == "w.rakkiatngam"
    assert route.calls.last.request.url.params["filters"] == 'connectorName eq "Microsoft Entra"'


async def test_entra_offers_its_tools_and_the_owner_none_of_the_writes() -> None:
    pb = playbooks.for_session(entra_session())
    iam = set(loop.offered_tools("iam_engineer", pb=pb))
    assert {"start_aggregation", "aggregate_datasets", "adopt_source", "request_new_secret"} <= iam
    assert "get_tenant_external_id" not in iam
    owner = set(loop.offered_tools("application_owner", pb=pb))
    assert "configure_source" not in owner and "apply_application_secret" not in owner
    order = {"user_id": "u", "display_name": "W"}
    rerun = loop.offered_tools("application_owner", order, True, pb=pb)
    assert {t for t in rerun if t in loop.RERUN_TOOLS} == set(loop.RERUN_TOOLS)
    assert loop.check_tools(pb) == ["peek_accounts", "test_connection", "start_aggregation", "aggregate_datasets"]
    assert "apply_application_secret" in loop.offered_tools("application_owner", order, True, pb=pb,
                                                             trigger="secret_submitted")


async def test_apply_application_secret(isc, mock, emit, fake_vault) -> None:  # type: ignore[no-untyped-def]
    patch = mock.patch(f"/v2026/sources/{SID}").respond(200, json={})
    result = await tools_for(isc, entra_session(source={"id": SID, "name": "n"}), emit).apply_application_secret()
    assert result["applied"] and SECRET not in json.dumps(result)
    assert json.loads(patch.calls.last.request.content) == [
        {"op": "add", "path": "/connectorAttributes/clientSecret", "value": SECRET}]
    assert emit.of("action")[0]["request"]["fields"] == {"clientSecret": "[vaulted]"}


# ---------------------------------------------------------------- T064: service principals
async def test_schema_attributes_adds_only_missing_ones(isc, mock, emit) -> None:  # type: ignore[no-untyped-def]
    mock.get(f"/v2026/sources/{SID}/schemas").respond(200, json=[
        {"id": "acc", "name": "account", "attributes": [{"name": "objectId"}, {"name": "displayName"}]},
        {"id": "grp", "name": "group", "attributes": []}])
    patch = mock.patch(f"/v2026/sources/{SID}/schemas/acc").respond(200, json={})
    session = entra_session(("directory", "service_principals"), source={"id": SID, "name": "n"})
    result = await tools_for(isc, session, emit).ensure_schema_attributes()
    ops = json.loads(patch.calls.last.request.content)
    names = [op["value"]["name"] for op in ops]
    assert "objectId" not in names and "displayName" not in names and "spn_appId" in names
    assert all(op["op"] == "add" and op["path"] == "/attributes/-" for op in ops)
    groups = next(op["value"] for op in ops if op["value"]["name"] == "groups")
    assert groups["schema"] == {"type": "CONNECTOR_SCHEMA", "id": "grp", "name": "group"}
    roles = next(op["value"] for op in ops if op["value"]["name"] == "roles")
    assert roles["isEntitlement"] is False and "schema" not in roles  # no role schema on this source
    assert result["already_present"] == 2


async def test_service_principals_are_counted_apart_from_users(isc, mock, emit) -> None:  # type: ignore[no-untyped-def]
    _aggregation_mocks(mock, accounts=300, sps=161)
    session = entra_session(("directory", "service_principals"), source={"id": SID, "name": "n"})
    result = await tools_for(isc, session, emit).start_aggregation()
    assert result["accounts"]["service_principals"] == 161 and result["accounts"]["users"] == 139


async def test_service_principal_settings_only_with_the_capability(isc, mock, emit) -> None:  # type: ignore[no-untyped-def]
    patch = mock.patch(f"/v2026/sources/{SID}").respond(200, json={})
    session = entra_session(("directory", "service_principals"), source={"id": SID, "name": "n"})
    await tools_for(isc, session, emit).configure_source()
    ops = {op["path"].split("/")[-1]: op["value"] for op in json.loads(patch.calls.last.request.content)}
    assert ops["manageAzureServicePrincipalAsAccount"] is True
    assert ops["spnAccountFilter"] == "servicePrincipalType eq 'Application'"
    assert ops["spnManageDirectoryRole"] is True and ops["spnManageRBACRoles"] is True
    assert ops["spnManageAzurePIM"] is False and ops["spnManageAzureADPIM"] is False
    assert ops["enableManagedIdentityManagement"] is True and ops["enableSystemAssignedManagedIdentity"] is True


# ---------------------------------------------------------------- T069: AI agents
async def test_dataset_aggregation_and_count(isc, mock, emit) -> None:  # type: ignore[no-untyped-def]
    mock.get(f"/v2026/sources/{SID}").respond(200, json=source_doc(enableAIFoundryAgent=True))
    agg = mock.post(f"/v2026/sources/{SID}/aggregate-agents").respond(200, json={"id": "tm"})
    mock.get("/v2026/task-status/tm").respond(200, json={"completionStatus": "SUCCESS"})
    mi = mock.get("/v2026/machine-identities").respond(200, json=[
        {"datasetId": "azure:foundry"}, {"datasetId": "azure:foundry"}, {"datasetId": "other"}])
    session = entra_session(("directory", "ai_agents"), source={"id": SID, "name": "n"})
    result = await tools_for(isc, session, emit).aggregate_datasets()
    assert result["ok"] and result["ai_agents"] == 2
    assert json.loads(agg.calls.last.request.content) == {"datasetIds": ["azure:foundry"], "disableOptimization": False}
    assert agg.calls.last.request.headers["X-SailPoint-Experimental"] == "true"
    assert mi.calls.last.request.headers["X-SailPoint-Experimental"] == "true"
    assert emit.final_actions()[-1]["summary"] == "2 AI agents"


async def test_dataset_api_unavailable_is_a_tenant_limitation(isc, mock, emit) -> None:  # type: ignore[no-untyped-def]
    mock.get(f"/v2026/sources/{SID}").respond(200, json=source_doc(enableAIFoundryAgent=True))
    mock.post(f"/v2026/sources/{SID}/aggregate-agents").respond(
        404, json={"messages": [{"text": "The aggregate-agents endpoint is unavailable"}]})
    session = entra_session(("directory", "ai_agents"), source={"id": SID, "name": "Entra ID - Contoso"})
    result = await tools_for(isc, session, emit).aggregate_datasets()
    assert result["tenant_limitation"] is True and "ok" in result and result["ok"] is None
    assert "Entra ID - Contoso" in result["ui_path"] and "Azure AI Foundry" in result["ui_path"]
    action = emit.of("action")[-1]
    assert action["result"] == "tenant_limitation" and action["plan_step"] == "aggregate_foundry"
    assert action["summary"] == "tenant limitation · 404 endpoint unavailable"


async def test_dataset_schedule_put(isc, mock, emit) -> None:  # type: ignore[no-untyped-def]
    path = f"/v2026/sources/{SID}/datasets/azure:foundry"
    mock.get(path).respond(200, json={"id": "azure:foundry", "aggregationEnabled": False, "resources": []})
    put = mock.put(path).respond(200, json={})
    session = entra_session(("directory", "ai_agents"), source={"id": SID, "name": "n"})
    result = await tools_for(isc, session, emit).set_dataset_schedule()
    assert result["ok"] and json.loads(put.calls.last.request.content)["aggregationEnabled"] is True
    assert put.calls.last.request.headers["X-SailPoint-Experimental"] == "true"


async def test_ai_agent_settings_keep_copilot_and_agent365_off(isc, mock, emit) -> None:  # type: ignore[no-untyped-def]
    patch = mock.patch(f"/v2026/sources/{SID}").respond(200, json={})
    session = entra_session(("directory", "ai_agents"), source={"id": SID, "name": "n"})
    await tools_for(isc, session, emit).configure_source()
    ops = {op["path"].split("/")[-1]: op["value"] for op in json.loads(patch.calls.last.request.content)}
    assert ops["enableAIFoundryAgent"] is True
    assert ops["enableCopilotAIAgent"] is False and ops["enableMicrosoftAgent365"] is False


# ---------------------------------------------------------------- T079: provisioning
async def test_provisioning_policy_created_with_session_values(isc, mock, emit) -> None:  # type: ignore[no-untyped-def]
    mock.get(f"/v2026/sources/{SID}/provisioning-policies").respond(200, json=[])
    post = mock.post(f"/v2026/sources/{SID}/provisioning-policies").respond(201, json={})
    session = entra_session(("directory", "provisioning"), source={"id": SID, "name": "n"})
    result = await tools_for(isc, session, emit).set_provisioning_policy()
    body = json.loads(post.calls.last.request.content)
    upn = next(f for f in body["fields"] if f["name"] == "userPrincipalName")
    assert result["ok"] and not result["kept_existing"] and "_notes" not in body
    assert upn["attributes"]["template"].endswith("@contoso.example")
    loc = next(f for f in body["fields"] if f["name"] == "usageLocation")
    assert loc["transform"]["attributes"]["value"] == "TH"


async def test_existing_policy_is_kept_unless_replace(isc, mock, emit) -> None:  # type: ignore[no-untyped-def]
    mock.get(f"/v2026/sources/{SID}/provisioning-policies").respond(200, json=[{"usageType": "CREATE"}])
    post = mock.post(f"/v2026/sources/{SID}/provisioning-policies")
    put = mock.put(f"/v2026/sources/{SID}/provisioning-policies/CREATE").respond(200, json={})
    session = entra_session(("directory", "provisioning"), source={"id": SID, "name": "n"})
    tools = tools_for(isc, session, emit)
    assert (await tools.set_provisioning_policy())["kept_existing"] is True
    assert not post.called and not put.called
    assert (await tools.set_provisioning_policy(replace=True))["kept_existing"] is False and put.called


async def test_correlation_and_read_source_setup(isc, mock, emit) -> None:  # type: ignore[no-untyped-def]
    mock.get(f"/v2026/sources/{SID}/correlation-config").respond(200, json={"id": "c1", "name": "Entra corr"})
    put = mock.put(f"/v2026/sources/{SID}/correlation-config").respond(200, json={})
    session = entra_session(("directory", "provisioning"), source={"id": SID, "name": "n"})
    tools = tools_for(isc, session, emit)
    result = await tools.set_correlation()
    body = json.loads(put.calls.last.request.content)
    assert result["ok"] and body["id"] == "c1" and len(body["attributeAssignments"]) == 2
    mock.get(f"/v2026/sources/{SID}").respond(200, json=source_doc())
    mock.get(f"/v2026/sources/{SID}/schemas").respond(200, json=[{"id": "a", "name": "account", "attributes": []}])
    mock.get(f"/v2026/sources/{SID}/provisioning-policies").respond(200, json=[{"usageType": "CREATE"}])
    mock.get(f"/v2026/sources/{SID}/correlation-config").respond(200, json={"attributeAssignments": [{}]})
    setup = await tools.read_source_setup()
    assert setup["provisioning_policy"] is True and setup["correlation"] is True
    # nothing in the provisioning tools creates an account
    assert not [c for c in mock.calls if c.request.method == "POST" and "/accounts" in c.request.url.path]


# ---------------------------------------------------------------- T085: extend-source
async def test_adopt_source_refuses_someone_elses_or_another_tenant(isc, mock, emit) -> None:  # type: ignore[no-untyped-def]
    mock.get(f"/v2026/sources/{SID}").respond(200, json={**source_doc(), "owner": {"id": "b" * 32, "name": "x"}})
    session = entra_session(mode=None)
    result = await tools_for(isc, session, emit).adopt_source(SID)
    assert result["adopted"] is False and "belongs to x" in result["refused"][0]
    assert not emit.of("source") and session["source"] is None
    mock.get(f"/v2026/sources/{SID}").respond(200, json={**source_doc(), "connectorAttributes": {
        "domainName": "else.onmicrosoft.com"}})
    assert (await tools_for(isc, session, emit).adopt_source(SID))["adopted"] is False


async def test_adopt_then_extend_only_and_never_delete(isc, mock, emit) -> None:  # type: ignore[no-untyped-def]
    mock.get(f"/v2026/sources/{SID}").respond(200, json=source_doc())
    session = entra_session(("directory", "service_principals"), vault_state="missing",
                            details={"source_mode": "extend", "client_id": ""})
    tools = tools_for(isc, session, emit)
    result = await tools.adopt_source(SID)
    assert result["adopted"] and emit.of("source")[0]["adopted"] is True
    patch = mock.patch(f"/v2026/sources/{SID}").respond(200, json={})
    configured = await tools.configure_source()
    assert configured["configured"], configured
    sent = {op["path"].split("/")[-1] for op in json.loads(patch.calls.last.request.content)}
    assert sent and all(not k.startswith(("domainName", "clientID", "grantType", "aggregateAllGroups", "clientSecret"))
                        for k in sent)
    assert "manageAzureServicePrincipalAsAccount" in sent
    delete = mock.delete(f"/v2026/sources/{SID}")
    assert (await tools.delete_session_source())["deleted"] is False and not delete.called


async def test_vault_failure_is_the_services_problem_not_the_secret(isc, mock, emit, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Live run 2026-10-08: a vault read failure must not send the administrator off to make a new secret (E12)."""
    async def broken(provider: str, workload_name=None) -> str:  # type: ignore[no-untyped-def]
        raise vault.VaultError("AgentCore Identity could not return the stored value (AccessDeniedException)")

    monkeypatch.setattr(vault, "get_application_secret", broken)
    patch = mock.patch(f"/v2026/sources/{SID}")
    result = await tools_for(isc, entra_session(source={"id": SID, "name": "n"}), emit).configure_source()
    assert result["configured"] is False and result["side"] == "onboarding service"
    assert "Do not ask for a new secret" in result["instruction"] and not patch.called
    from onboarding_agent.masking_lite import strip_tokens

    assert "[masked]" not in strip_tokens(result["error"])


async def test_counts_wait_for_the_index_to_settle(isc, mock, emit) -> None:  # type: ignore[no-untyped-def]
    """Live run 2026-10-08: right after SUCCESS ISC counted 158 of 225 accounts; the count is read until it settles."""
    _aggregation_mocks(mock)
    totals = iter(["158", "201", "225", "225"])

    def accounts(request):  # type: ignore[no-untyped-def]
        if request.url.params.get("count") == "true":
            return respx.MockResponse(200, json=[{}], headers={"X-Total-Count": next(totals)})
        return respx.MockResponse(200, json=[])

    mock.get("/v2026/accounts").mock(side_effect=accounts)
    result = await tools_for(isc, entra_session(source={"id": SID, "name": "n"}), emit).start_aggregation()
    assert result["accounts"]["accounts"] == 225


async def test_machine_classification_enabled_with_the_ui_criteria(isc, mock, emit) -> None:  # type: ignore[no-untyped-def]
    """Machine Account Classification as a UI-configured source has it (Enable + Customize classification)."""
    put = mock.put(f"/v2026/sources/{SID}/machine-classification-config").respond(200, json={})
    classify = mock.post(f"/v2026/sources/{SID}/classify").respond(200, json={"Accounts submitted for processing": 225})
    session = entra_session(("directory", "service_principals"), source={"id": SID, "name": "n"})
    result = await tools_for(isc, session, emit).set_machine_classification()
    body = json.loads(put.calls.last.request.content)
    assert result["ok"] and result["accounts_submitted"] == 225 and classify.called
    assert body["enabled"] is True and body["classificationMethod"] == "CRITERIA" and "_notes" not in body
    groups = body["criteria"]["children"]
    assert body["criteria"]["operation"] == "OR" and len(groups) == 2
    assert {c["value"] for c in groups[0]["children"]} == {"Microsoft.ManagedIdentity", "userAssignedIdentities",
                                                           "systemAssignedIdentities"}
    assert {(c["attribute"], c["value"]) for c in groups[1]["children"]} == {
        ("spn_servicePrincipalType", "Application"), ("spn_servicePrincipalType", "Legacy")}
    # GA in the spec, but the live tenant requires the experimental header (2026-10-08)
    assert put.calls.last.request.headers["X-SailPoint-Experimental"] == "true"
    assert classify.calls.last.request.headers["X-SailPoint-Experimental"] == "true"
    assert emit.final_actions()[-1]["summary"] == "machine accounts: classification on · 225 submitted"


async def test_machine_classification_needs_service_principals(isc, mock, emit) -> None:  # type: ignore[no-untyped-def]
    put = mock.put(f"/v2026/sources/{SID}/machine-classification-config")
    result = await tools_for(isc, entra_session(source={"id": SID, "name": "n"}), emit).set_machine_classification()
    assert result["skipped"] and not put.called
