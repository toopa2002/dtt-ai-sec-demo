"""SailPoint ISC stub for local and end-to-end runs (T081). Replays the AWS SaaS calls the agent makes, with response
shapes taken from live tenants (.claude/skills/sailpoint-isc-aws-connector/references/isc-api.md).

Scenario switches (POST /_stub/...), so the troubleshooting flow (US4) can be exercised without real AWS:
  trust_broken     connection check fails with the AssumeRole denial until /_stub/fix_trust
  foreign_source   a same-name source already exists, owned by someone else (SC-007)
  reset            clear sources and switches

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
state: dict[str, Any] = {"sources": {}, "trust_broken": False, "aggregated": set(), "calls": []}


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
    return {"ok": True}


@app.get("/_stub/state")
async def stub_state() -> dict:
    return {"sources": list(state["sources"].values()), "trust_broken": state["trust_broken"], "calls": state["calls"]}
