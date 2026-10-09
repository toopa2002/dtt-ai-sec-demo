"""SailPoint ISC stub for local and end-to-end runs (T081). Replays the AWS SaaS calls the agent makes, with response
shapes taken from live tenants (.claude/skills/sailpoint-isc-aws-connector/references/isc-api.md).

Scenario switches (POST /_stub/...), so the troubleshooting flow (US4) can be exercised without real AWS:
  trust_broken     connection check fails with the AssumeRole denial until /_stub/fix_trust
  foreign_source   a same-name source already exists, owned by someone else (SC-007)
  reset            clear sources and switches

Spec 002 adds the Microsoft Entra calls on /v2026 (shapes from .claude/skills/sailpoint-isc-entra-connector/references/
isc-api.md, checked live) and switches under /_stub/entra/:
  secret_invalid       connection check / Test Connection fail with AADSTS7000215 until /_stub/entra/fix_secret
  dataset_unavailable  aggregate-agents answers 404 "The aggregate-agents endpoint is unavailable"
  slow_aggregation     ?polls=N: account aggregation tasks report "still running" for the next N status reads
  delta_empty_peek     a peek with delta on returns no accounts (the tools switch delta off first)
  foreign_entra_source an Entra source for the session's tenant owned by someone else
  existing_policy      new Entra sources already have a CREATE provisioning policy

Run: uv run --project onboarding/api uvicorn stub_isc:app --app-dir onboarding/deploy/stub-isc --port 8099
"""

import itertools
import re
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse

SPEC_ID = "6e47875b-73f1-481d-a613-59d4deca0c6a"
EXTERNAL_ID = "5c0d9a3e-7b14-4f2a-9e61-2d8a4c7b1f90"
FIELDS = ["AgentAwsRegion", "AgentCoreAwsRegion", "assumeRoleSessionName", "changePasswordPolicyARN", "cloudScope",
          "enableDiscoverBedrockAgent", "enableDiscoverBedrockAgentCore", "externalId", "managementAccountId",
          "region", "roleName"]

app = FastAPI(title="ISC stub")
_ids = itertools.count(1)
ENTRA_SPEC_ID = "b7e9374a-3c50-4b51-8880-e501f947bbf8"
ENTRA_FIELDS = ["domainName", "clientID", "clientSecret", "grantType", "aggregateAllGroups", "deltaAggregationEnabled",
                "aggregateGroupHierarchy", "pageSize", "manageO365Groups", "enableTeamsGovernance",
                "enableAccessPackageManagement", "enableManagedIdentityManagement",
                "enableSystemAssignedManagedIdentity", "manageAzureServicePrincipalAsAccount", "spnAccountFilter",
                "spnManageDirectoryRole", "spnManageAppRoles", "spnManageGroups", "spnManageRBACRoles",
                "manageAdminConsentedPermissions", "manageCustomSecurityAttributesForServicePrincipals",
                "spnManageAzurePIM", "spnManageAzureADPIM", "enableAIFoundryAgent", "foundryAggregateLatestVersionOnly",
                "enableCopilotAIAgent", "enableMicrosoftAgent365"]
ENTRA_USERS, ENTRA_SPS, ENTRA_ENTITLEMENTS, FOUNDRY_AGENTS = 64, 161, 412, 2
state: dict[str, Any] = {"sources": {}, "trust_broken": False, "aggregated": set(), "calls": [], "entra": {}}


def _entra_reset() -> None:
    state["entra"] = {"secret_invalid": False, "dataset_unavailable": False, "slow_polls": 0, "delta_empty_peek": False,
                      "existing_policy": False, "patches": [], "policies": {}, "correlation": {}, "datasets": {},
                      "schemas": {}, "agents_aggregated": set(), "entitlements_loaded": set(), "account_creates": 0,
                      "classification": {}, "classified": {}}


_entra_reset()


def _sid() -> str:
    return f"{next(_ids):032x}"


@app.middleware("http")
async def record(request: Request, call_next):  # type: ignore[no-untyped-def]
    if not request.url.path.startswith("/_stub"):
        state["calls"].append(f"{request.method} {request.url.path}")
    return await call_next(request)


@app.post("/oauth/token")
async def token() -> dict:
    return {"access_token": "stub-token", "token_type": "bearer", "expires_in": 600, "identity_id": "a" * 32}


@app.get("/beta/tenant")
async def tenant() -> dict:
    return {"name": "acme-demo", "products": [{"productName": "idn", "attributes": {"externalId": EXTERNAL_ID}}]}


@app.get("/v3/public-identities")
async def identities(filters: str = "") -> list:
    return [{"id": "a" * 32, "name": re.sub(r'.*eq "(.*)".*', r"\1", filters) or "owner"}]


@app.get("/v3/connectors/{script}")
async def connector(script: str) -> dict:
    return {"name": "AWS SaaS", "scriptName": script, "type": SPEC_ID}


@app.get("/v3/connectors/{script}/source-config")
async def source_config(script: str) -> PlainTextResponse:
    xml = "<Form>" + "".join(f'<Field name="{f}" type="string"/>' for f in FIELDS) + "</Form>"
    return PlainTextResponse(xml, media_type="application/xml")


@app.get("/v3/sources")
async def find_sources(filters: str = "") -> list:
    name = re.sub(r'.*eq "(.*)".*', r"\1", filters)
    return [s for s in state["sources"].values() if s["name"] == name]


@app.post("/v3/sources", status_code=201)
async def create_source(request: Request) -> Any:
    body = await request.json()
    attrs = body.get("connectorAttributes") or {}
    if attrs.get("spConnectorSpecId") != SPEC_ID or attrs.get("idnProxyType") != "sp-connect":
        return JSONResponse(status_code=400, content={"messages": [{"text": "missing spConnectorSpecId / idnProxyType"}]})
    sid = _sid()
    source = {"id": sid, "name": body["name"], "owner": {"id": body["owner"]["id"], "name": "the IAM engineer"},
              "connector": body["connector"], "connectorAttributes": attrs | {"spConnectorInstanceId": "inst-" + sid}}
    state["sources"][sid] = source
    return source


@app.patch("/v3/sources/{sid}")
async def patch_source(sid: str, request: Request) -> Any:
    source = state["sources"].get(sid)
    if not source:
        return JSONResponse(status_code=404, content={"messages": [{"text": "source not found"}]})
    for op in await request.json():
        key = op["path"].split("/")[-1]
        if key not in FIELDS:
            return JSONResponse(status_code=400, content={"messages": [{"text": f"unknown field {key}"}]})
        source["connectorAttributes"][key] = op["value"]
    return source


@app.delete("/v3/sources/{sid}", status_code=204)
async def delete_source(sid: str) -> None:
    state["sources"].pop(sid, None)


@app.post("/beta/sources/{sid}/connector/peek-resource-objects")
async def peek(sid: str) -> Any:
    if sid not in state["sources"]:
        return JSONResponse(status_code=404, content={"messages": [{"text": "source not found"}]})
    if state["trust_broken"]:
        role = state["sources"][sid]["connectorAttributes"].get("roleName", "SailPointISCRole")
        acct = state["sources"][sid]["connectorAttributes"].get("managementAccountId", "111122223333")
        return JSONResponse(status_code=400, content={"detailCode": "400.1 Bad request content", "messages": [{"text":
            f"AWS Client creation failed: User: arn:aws:sts::874540850173:assumed-role/ciem_universal/sp is not "
            f"authorized to perform: sts:AssumeRole on resource: arn:aws:iam::{acct}:role/{role}"}]})
    return {"resourceObjects": [{"identity": "alice"}, {"identity": "bob"}, {"identity": "ci-deployer"}]}


@app.post("/beta/sources/{sid}/load-accounts")
async def load_accounts(sid: str) -> dict:
    state["aggregated"].add(sid)
    return {"task": {"id": f"acct-{sid[-6:]}"}}


@app.post("/beta/sources/{sid}/load-entitlements")
async def load_entitlements(sid: str) -> dict:
    return {"id": f"ent-{sid[-6:]}"}


@app.get("/beta/task-status/{task_id}")
async def task_status(task_id: str) -> dict:
    return {"id": task_id, "completionStatus": "SUCCESS", "messages": []}


@app.get("/v3/accounts")
async def accounts(filters: str = "") -> list:
    return [{"id": str(i)} for i in range(3)]


@app.post("/beta/sources/{sid}/connector/test-configuration")
async def test_configuration(sid: str) -> dict:
    if sid not in state["aggregated"]:
        return {"status": "FAILURE", "details": 'NullPointerException: Cannot invoke ... because "req.input" is null'}
    return {"status": "SUCCESS", "elapsedMillis": 812}


# ---- scenario switches -------------------------------------------------------------------------------------------
@app.post("/_stub/trust_broken")
async def trust_broken() -> dict:
    state["trust_broken"] = True
    return {"ok": True}


@app.post("/_stub/fix_trust")
async def fix_trust() -> dict:
    state["trust_broken"] = False
    return {"ok": True}


@app.post("/_stub/foreign_source")
async def foreign_source(name: str) -> dict:
    sid = _sid()
    state["sources"][sid] = {"id": sid, "name": name, "owner": {"id": "f" * 32, "name": "someone.else"},
                             "connector": "awssaas", "connectorAttributes": {}}
    return {"ok": True, "id": sid}


@app.post("/_stub/reset")
async def reset() -> dict:
    state.update(sources={}, trust_broken=False, aggregated=set(), calls=[])
    _entra_reset()
    return {"ok": True}


@app.get("/_stub/state")
async def stub_state() -> dict:
    entra = state["entra"]
    return {"sources": list(state["sources"].values()), "trust_broken": state["trust_broken"], "calls": state["calls"],
            "entra": {"patches": entra["patches"], "account_creates": entra["account_creates"],
                      "classification": entra["classification"], "classified": entra["classified"],
                      "policies": entra["policies"], "datasets": entra["datasets"]}}


# ---- Microsoft Entra on /v2026 (spec 002) --------------------------------------------------------------------------
def _entra(sid: str) -> dict | None:
    src = state["sources"].get(sid)
    return src if src and src.get("connector") == "Microsoft-Entra" else None


def _not_found() -> JSONResponse:
    return JSONResponse(status_code=404, content={"messages": [{"text": "source not found"}]})


def _aadsts(code: str, text: str) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detailCode": "400.1 Bad request content", "messages": [
        {"text": f"[Microsoft-Entra]: {code}: {text}"}]})


@app.get("/v2026/public-identities")
async def identities_v2026(filters: str = "") -> list:
    return [{"id": "a" * 32, "name": re.sub(r'.*eq "(.*)".*', r"\1", filters) or "owner"}]


@app.get("/v2026/connectors/{script}")
async def connector_v2026(script: str) -> Any:
    if script != "Microsoft-Entra":
        return JSONResponse(status_code=404, content={"messages": [{"text": "connector not found"}]})
    return {"name": "Microsoft Entra", "scriptName": script, "type": ENTRA_SPEC_ID}


@app.get("/v2026/connectors/{script}/source-config")
async def source_config_v2026(script: str) -> PlainTextResponse:
    xml = "<Form>" + "".join(f'<Field name="{f}" type="string"/>' for f in ENTRA_FIELDS) + "</Form>"
    return PlainTextResponse(xml, media_type="application/xml")


@app.get("/v2026/sources")
async def find_sources_v2026(filters: str = "") -> list:
    value = re.sub(r'.*eq "(.*)".*', r"\1", filters)
    if filters.startswith("connectorName"):
        return [s for s in state["sources"].values() if s.get("connector") == "Microsoft-Entra"]
    return [s for s in state["sources"].values() if s["name"] == value]


@app.post("/v2026/sources", status_code=201)
async def create_source_v2026(request: Request) -> Any:
    body = await request.json()
    attrs = body.get("connectorAttributes") or {}
    if attrs.get("spConnectorSpecId") != ENTRA_SPEC_ID or attrs.get("idnProxyType") != "sp-connect":
        return JSONResponse(status_code=400, content={"messages": [{"text": "missing spConnectorSpecId / idnProxyType"}]})
    sid = _sid()
    source = {"id": sid, "name": body["name"], "owner": {"id": body["owner"]["id"], "name": "the IAM engineer"},
              "connector": body["connector"], "connectorAttributes": attrs | {"spConnectorInstanceId": "inst-" + sid}}
    state["sources"][sid] = source
    state["entra"]["schemas"][sid] = [
        {"id": f"acc-{sid[-4:]}", "name": "account", "nativeObjectType": "User",
         "attributes": [{"name": "objectId"}, {"name": "displayName"}, {"name": "groups"}]},
        {"id": f"grp-{sid[-4:]}", "name": "group", "attributes": []},
        {"id": f"rol-{sid[-4:]}", "name": "role", "attributes": []},
        {"id": f"app-{sid[-4:]}", "name": "applicationRole", "attributes": []}]
    state["entra"]["datasets"][sid] = {"azure:foundry": {"id": "azure:foundry", "name": "Azure AI Foundry",
                                                         "aggregationEnabled": False, "resources": []}}
    if state["entra"]["existing_policy"]:
        state["entra"]["policies"][sid] = [{"name": "Account", "usageType": "CREATE", "fields": []}]
    return source


@app.get("/v2026/sources/{sid}")
async def get_source_v2026(sid: str) -> Any:
    return _entra(sid) or _not_found()


@app.patch("/v2026/sources/{sid}")
async def patch_source_v2026(sid: str, request: Request) -> Any:
    source = _entra(sid)
    if not source:
        return _not_found()
    ops = await request.json()
    for op in ops:
        key = op["path"].split("/")[-1]
        if key not in ENTRA_FIELDS:
            return JSONResponse(status_code=400, content={"messages": [{"text": f"unknown field {key}"}]})
        source["connectorAttributes"][key] = op["value"]
    # kept for the leak test: the one place the application secret is expected (in memory, never logged)
    state["entra"]["patches"].append({k: v for k, v in ((op["path"].split("/")[-1], op["value"]) for op in ops)})
    return source


@app.delete("/v2026/sources/{sid}", status_code=204)
async def delete_source_v2026(sid: str) -> None:
    state["sources"].pop(sid, None)


def _signed_in(source: dict) -> JSONResponse | None:
    attrs = source["connectorAttributes"]
    if state["entra"]["secret_invalid"] or not attrs.get("clientSecret"):
        return _aadsts("AADSTS7000215", "Invalid client secret provided. Ensure the secret being sent in the request "
                                        "is the client secret value, not the client secret ID")
    return None


@app.post("/v2026/sources/{sid}/connector/peek-resource-objects")
async def peek_v2026(sid: str) -> Any:
    source = _entra(sid)
    if not source:
        return _not_found()
    if (denied := _signed_in(source)) is not None:
        return denied
    if state["entra"]["delta_empty_peek"] and source["connectorAttributes"].get("deltaAggregationEnabled"):
        return {"resourceObjects": []}
    return {"resourceObjects": [{"identity": f"user{i}@contoso.example"} for i in range(5)]}


@app.post("/v2026/sources/{sid}/connector/test-configuration")
async def test_v2026(sid: str) -> Any:
    source = _entra(sid)
    if not source:
        return _not_found()
    if (denied := _signed_in(source)) is not None:
        return {"status": "FAILURE", "details": denied.body.decode()}
    return {"status": "SUCCESS", "elapsedMillis": 640}


@app.post("/v2026/sources/{sid}/load-entitlements", status_code=202)
async def load_entitlements_v2026(sid: str, request: Request) -> Any:
    if not _entra(sid):
        return _not_found()
    if not request.headers.get("content-type", "").startswith("multipart/form-data"):
        return JSONResponse(status_code=415, content={"messages": [{"text": "multipart/form-data expected"}]})
    state["entra"]["entitlements_loaded"].add(sid)
    return {"id": f"ent-{sid[-6:]}"}


@app.post("/v2026/sources/{sid}/load-accounts", status_code=202)
async def load_accounts_v2026(sid: str, request: Request) -> Any:
    if not _entra(sid):
        return _not_found()
    if not request.headers.get("content-type", "").startswith("multipart/form-data"):
        return JSONResponse(status_code=415, content={"messages": [{"text": "multipart/form-data expected"}]})
    state["aggregated"].add(sid)
    return {"task": {"id": f"acct-{sid[-6:]}"}}


@app.get("/v2026/task-status/{task_id}")
async def task_status_v2026(task_id: str) -> dict:
    if task_id.startswith("acct-") and state["entra"]["slow_polls"] > 0:
        state["entra"]["slow_polls"] -= 1
        return {"id": task_id, "completionStatus": None, "messages": []}
    return {"id": task_id, "completionStatus": "SUCCESS", "messages": []}


def _entra_accounts(sid: str) -> list[dict]:
    source = _entra(sid)
    if not source or sid not in state["aggregated"]:
        return []
    out = [{"id": f"u{i}", "attributes": {"displayName": f"User {i}"}} for i in range(ENTRA_USERS)]
    has_sp_attrs = any(a["name"] == "spn_servicePrincipalType" for a in state["entra"]["schemas"][sid][0]["attributes"])
    if source["connectorAttributes"].get("manageAzureServicePrincipalAsAccount") and has_sp_attrs:
        out += [{"id": f"sp{i}", "attributes": {"spn_servicePrincipalType": "Application"}} for i in range(ENTRA_SPS)]
    return out


def _page(items: list, request: Request, count: str | None) -> JSONResponse:
    limit = int(request.query_params.get("limit", 250))
    offset = int(request.query_params.get("offset", 0))
    headers = {"X-Total-Count": str(len(items))} if count == "true" else {}
    return JSONResponse(items[offset:offset + limit], headers=headers)


@app.get("/v2026/accounts")
async def accounts_v2026(request: Request, filters: str = "", count: str | None = None) -> Any:
    if "sourceId" in filters:
        return JSONResponse(status_code=400, content={"messages": [{"text": "Invalid filter: sourceId"}]})
    sid = re.sub(r'.*source\.id eq "([^"]*)".*', r"\1", filters)
    return _page(_entra_accounts(sid), request, count)


@app.get("/v2026/entitlements")
async def entitlements_v2026(request: Request, filters: str = "", count: str | None = None) -> Any:
    sid = re.sub(r'.*source\.id eq "([^"]*)".*', r"\1", filters)
    items = [{"id": f"e{i}"} for i in range(ENTRA_ENTITLEMENTS)] if sid in state["entra"]["entitlements_loaded"] else []
    return _page(items, request, count)


@app.get("/v2026/sources/{sid}/schemas")
async def schemas_v2026(sid: str) -> Any:
    return state["entra"]["schemas"].get(sid) or _not_found()


@app.patch("/v2026/sources/{sid}/schemas/{schema_id}")
async def patch_schema_v2026(sid: str, schema_id: str, request: Request) -> Any:
    schema = next((s for s in state["entra"]["schemas"].get(sid) or [] if s["id"] == schema_id), None)
    if not schema:
        return _not_found()
    for op in await request.json():
        if op["op"] != "add" or op["path"] != "/attributes/-":
            return JSONResponse(status_code=400, content={"messages": [{"text": "only add /attributes/- is stubbed"}]})
        schema["attributes"].append(op["value"])
    return schema


def _experimental(request: Request) -> JSONResponse | None:
    if request.headers.get("x-sailpoint-experimental") != "true":
        return JSONResponse(status_code=400, content={"messages": [{"text": "X-SailPoint-Experimental required"}]})
    return None


@app.post("/v2026/sources/{sid}/aggregate-agents")
async def aggregate_agents_v2026(sid: str, request: Request) -> Any:
    if (bad := _experimental(request)) is not None:
        return bad
    if state["entra"]["dataset_unavailable"]:
        return JSONResponse(status_code=404, content={"messages": [
            {"text": "The aggregate-agents endpoint is unavailable"}]})
    body = await request.json()
    if not _entra(sid) or not body.get("datasetIds"):
        return JSONResponse(status_code=400, content={"messages": [{"text": "datasetIds required"}]})
    state["entra"]["agents_aggregated"].add(sid)
    return {"id": f"mi-{sid[-6:]}"}


@app.post("/_stub/entra/aggregate_in_ui")
async def aggregate_in_ui(sid: str) -> dict:
    """What the IAM engineer does in the ISC interface when the API is unavailable (FR-135)."""
    state["entra"]["agents_aggregated"].add(sid)
    return {"ok": True}


@app.get("/v2026/sources/{sid}/datasets/{dataset_id}")
async def get_dataset_v2026(sid: str, dataset_id: str, request: Request) -> Any:
    if (bad := _experimental(request)) is not None:
        return bad
    return (state["entra"]["datasets"].get(sid) or {}).get(dataset_id) or _not_found()


@app.put("/v2026/sources/{sid}/datasets/{dataset_id}")
async def put_dataset_v2026(sid: str, dataset_id: str, request: Request) -> Any:
    if (bad := _experimental(request)) is not None:
        return bad
    datasets = state["entra"]["datasets"].get(sid) or {}
    if dataset_id not in datasets:
        return _not_found()
    datasets[dataset_id] = await request.json()
    return datasets[dataset_id]


@app.get("/v2026/machine-identities")
async def machine_identities_v2026(request: Request, filters: str = "", count: str | None = None) -> Any:
    if (bad := _experimental(request)) is not None:
        return bad
    sid = re.sub(r'.*source\.id eq "([^"]*)".*', r"\1", filters)
    items = [{"id": f"mi{i}", "datasetId": "azure:foundry", "subtype": "AI Agent"} for i in range(FOUNDRY_AGENTS)] \
        if sid in state["entra"]["agents_aggregated"] else []
    return _page(items, request, count)


@app.get("/v2026/sources/{sid}/provisioning-policies")
async def policies_v2026(sid: str) -> Any:
    return state["entra"]["policies"].get(sid, []) if _entra(sid) else _not_found()


@app.post("/v2026/sources/{sid}/provisioning-policies", status_code=201)
async def create_policy_v2026(sid: str, request: Request) -> Any:
    if not _entra(sid):
        return _not_found()
    policy = await request.json()
    state["entra"]["policies"].setdefault(sid, []).append(policy)
    return policy


@app.put("/v2026/sources/{sid}/provisioning-policies/{usage}")
async def put_policy_v2026(sid: str, usage: str, request: Request) -> Any:
    policy = await request.json()
    state["entra"]["policies"][sid] = [p for p in state["entra"]["policies"].get(sid, [])
                                       if p.get("usageType") != usage] + [policy]
    return policy


@app.get("/v2026/sources/{sid}/correlation-config")
async def get_correlation_v2026(sid: str) -> Any:
    if not _entra(sid):
        return _not_found()
    return state["entra"]["correlation"].get(sid) or {"id": f"corr-{sid[-4:]}", "name": "Entra correlation",
                                                       "attributeAssignments": []}


@app.put("/v2026/sources/{sid}/correlation-config")
async def put_correlation_v2026(sid: str, request: Request) -> Any:
    state["entra"]["correlation"][sid] = await request.json()
    return state["entra"]["correlation"][sid]


@app.get("/v2026/sources/{sid}/machine-classification-config")
async def get_classification_v2026(sid: str) -> Any:
    if not _entra(sid):
        return _not_found()
    return state["entra"]["classification"].get(sid) or {"sourceId": None, "enabled": False,
                                                         "classificationMethod": "SOURCE", "criteria": None}


@app.put("/v2026/sources/{sid}/machine-classification-config")
async def put_classification_v2026(sid: str, request: Request) -> Any:
    if not _entra(sid):
        return _not_found()
    body = await request.json()
    if body.get("classificationMethod") not in ("SOURCE", "CRITERIA"):
        return JSONResponse(status_code=400, content={"messages": [{"text": "classificationMethod: SOURCE or CRITERIA"}]})
    state["entra"]["classification"][sid] = body | {"sourceId": sid}
    return state["entra"]["classification"][sid]


@app.post("/v2026/sources/{sid}/classify")
async def classify_v2026(sid: str) -> Any:
    if not _entra(sid):
        return _not_found()
    n = len(_entra_accounts(sid))
    state["entra"]["classified"][sid] = n
    return {"Accounts submitted for processing": n}


@app.post("/v2026/accounts")
async def create_account_v2026() -> dict:
    """Counted only: the onboarding must never create an account (FR-136)."""
    state["entra"]["account_creates"] += 1
    return {"id": "never"}


# ---- Entra scenario switches ----------------------------------------------------------------------------------------
@app.post("/_stub/entra/{switch}")
async def entra_switch(switch: str, polls: int = 3, name: str = "Entra ID - Contoso",
                       tenant: str = "contoso-demo.onmicrosoft.com") -> Any:
    entra = state["entra"]
    if switch in ("secret_invalid", "dataset_unavailable", "delta_empty_peek", "existing_policy"):
        entra[switch] = True
    elif switch == "fix_secret":
        entra["secret_invalid"] = False
    elif switch == "slow_aggregation":
        entra["slow_polls"] = polls
    elif switch == "foreign_entra_source":
        sid = _sid()
        state["sources"][sid] = {"id": sid, "name": name, "owner": {"id": "f" * 32, "name": "someone.else"},
                                 "connector": "Microsoft-Entra", "connectorAttributes": {"domainName": tenant}}
        return {"ok": True, "id": sid}
    else:
        return JSONResponse(status_code=404, content={"messages": [{"text": f"unknown switch {switch}"}]})
    return {"ok": True}
