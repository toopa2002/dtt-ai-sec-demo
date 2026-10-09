# ruff: noqa: E501  (the snapshot lines below are recorded values)
"""T005 (spec 002, SC-106): adding the Entra connector type changes nothing for AWS SaaS.

Snapshot taken before any spec 002 code change: the tools offered to the model per role and state, the tool
definitions and static system prompt the model sees (the cached prefix), and every ISC call the AWS tools make (method,
path, experimental header). Update these values only for a deliberate AWS change."""

import hashlib
import inspect
import json

from onboarding_agent import loop
from onboarding_agent.isc.tools import IscTools

from .conftest import sample_session
from .test_role_gate import payload

ORDER = {"user_id": "u", "display_name": "Wichai"}
STATES = [("iam_engineer", None, False), ("iam_engineer", ORDER, True), ("application_owner", None, False),
          ("application_owner", ORDER, False), ("application_owner", ORDER, True), ("application_owner", None, True)]

EXPECTED_TOOLS = {
    "iam_engineer|False|False": [
        "configure_source",
        "create_source",
        "delete_session_source",
        "find_source",
        "get_connector_form",
        "get_task",
        "get_tenant_external_id",
        "note_diagnosis",
        "notify_other_thread",
        "peek_accounts",
        "post_to_other_thread",
        "set_waiting",
        "start_aggregation",
        "suggest_replies",
        "test_connection",
        "update_plan"
    ],
    "iam_engineer|True|True": [
        "configure_source",
        "create_source",
        "delete_session_source",
        "find_source",
        "get_connector_form",
        "get_task",
        "get_tenant_external_id",
        "note_diagnosis",
        "notify_other_thread",
        "peek_accounts",
        "post_to_other_thread",
        "set_waiting",
        "start_aggregation",
        "suggest_replies",
        "test_connection",
        "update_plan"
    ],
    "application_owner|False|False": [
        "find_source",
        "get_connector_form",
        "get_task",
        "get_tenant_external_id",
        "note_diagnosis",
        "notify_other_thread",
        "post_to_other_thread",
        "set_waiting",
        "suggest_replies",
        "update_plan"
    ],
    "application_owner|True|False": [
        "find_source",
        "get_connector_form",
        "get_task",
        "get_tenant_external_id",
        "note_diagnosis",
        "notify_other_thread",
        "post_to_other_thread",
        "set_waiting",
        "suggest_replies",
        "update_plan"
    ],
    "application_owner|True|True": [
        "find_source",
        "get_connector_form",
        "get_task",
        "get_tenant_external_id",
        "note_diagnosis",
        "notify_other_thread",
        "peek_accounts",
        "post_to_other_thread",
        "set_waiting",
        "start_aggregation",
        "suggest_replies",
        "test_connection",
        "update_plan"
    ],
    "application_owner|False|True": [
        "find_source",
        "get_connector_form",
        "get_task",
        "get_tenant_external_id",
        "note_diagnosis",
        "notify_other_thread",
        "post_to_other_thread",
        "set_waiting",
        "suggest_replies",
        "update_plan"
    ]
}
EXPECTED_TOOL_DEFS_SHA = "d75da5d422df697538581b4634ff17302beebf5f908cb55e8b97d295cb69f56f"
EXPECTED_STATIC_SHA = "98ec1440594481c485c9d3cfcbf52a18c96f9f7bf144b6206aa0d09d2d8ca0e3"
EXPECTED_CALLS: list[list[str]] = [
    ["GET", "/v3/sources", "true", "", ""],
    ["GET", "/v3/public-identities", "true", "", ""],
    ["GET", "/beta/tenant", "true", "", ""],
    ["POST", "/v3/sources", "true", "application/json", "{\"name\":\"AWS - Acme Org\",\"description\":\"AWS SaaS source managed by the ISC Onboarding Agent\",\"owner\":{\"type\":\"IDENTITY\",\"id\":\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\"},\"connector\":\"awssaas\",\"connectorAttribu"],
    ["GET", "/beta/tenant", "true", "", ""],
    ["PATCH", "/v3/sources/2c91808a2c91808a2c91808a2c91808a", "true", "application/json-patch+json", "[{\"op\": \"add\", \"path\": \"/connectorAttributes/roleName\", \"value\": \"SailPointISCRole-acme-demo\"}, {\"op\": \"add\", \"path\": \"/connectorAttributes/managementAccountId\", \"value\": \"111122223333\"}, {\"op\": \"add\""],
    ["POST", "/beta/sources/2c91808a2c91808a2c91808a2c91808a/connector/peek-resource-objects", "true", "application/json", "{\"objectType\":\"account\",\"maxCount\":5}"],
    ["POST", "/beta/sources/2c91808a2c91808a2c91808a2c91808a/load-accounts", "true", "application/x-www-form-urlencoded", "disableOptimization=true"],
    ["POST", "/beta/sources/2c91808a2c91808a2c91808a2c91808a/load-entitlements", "true", "", ""],
    ["GET", "/beta/task-status/t1", "true", "", ""],
    ["GET", "/beta/task-status/t2", "true", "", ""],
    ["GET", "/v3/accounts", "true", "", ""],
    ["POST", "/beta/sources/2c91808a2c91808a2c91808a2c91808a/connector/test-configuration", "true", "", ""],
]


def _offered(role, order, has_source, pb):  # type: ignore[no-untyped-def]
    if "pb" in inspect.signature(loop.offered_tools).parameters:
        return loop.offered_tools(role, order, has_source, pb=pb)
    return loop.offered_tools(role, order, has_source)


def snapshot_tools(pb) -> dict:  # type: ignore[no-untyped-def]
    return {f"{r}|{bool(o)}|{s}": sorted(_offered(r, o, s, pb)) for r, o, s in STATES}


def snapshot_tool_defs(pb) -> str:  # type: ignore[no-untyped-def]
    names = _offered("iam_engineer", None, False, pb)
    defs = [{"name": n, "description": loop.TOOL_SPECS[n]["description"],
             "input_schema": loop.TOOL_SPECS[n]["input_schema"]} for n in names]
    return hashlib.sha256(json.dumps(defs, sort_keys=True).encode()).hexdigest()


def snapshot_static(pb) -> str:  # type: ignore[no-untyped-def]
    return hashlib.sha256(loop.static_system(payload("iam_engineer", "x"), pb).encode()).hexdigest()


async def snapshot_calls(isc, isc_mock, pb, emit) -> list[list[str]]:  # type: ignore[no-untyped-def]
    sid = "2c91808a" * 4
    isc_mock.get("/v3/sources").respond(200, json=[])
    isc_mock.get("/v3/connectors/awssaas").respond(200, json={"type": "spec"})
    isc_mock.post("/v3/sources").respond(201, json={"id": sid, "name": "AWS - Acme Org", "connectorAttributes": {}})
    isc_mock.patch(f"/v3/sources/{sid}").respond(200, json={})
    isc_mock.post(f"/beta/sources/{sid}/connector/peek-resource-objects").respond(
        200, json={"resourceObjects": [{"identity": "alice"}]})
    isc_mock.post(f"/beta/sources/{sid}/load-accounts").respond(200, json={"task": {"id": "t1"}})
    isc_mock.post(f"/beta/sources/{sid}/load-entitlements").respond(200, json={"id": "t2"})
    isc_mock.get(url__regex=r"/beta/task-status/.*").respond(200, json={"completionStatus": "SUCCESS"})
    isc_mock.get("/v3/accounts").respond(200, json=[{"id": "a"}])
    isc_mock.post(f"/beta/sources/{sid}/connector/test-configuration").respond(200, json={"status": "SUCCESS"})
    session = sample_session()
    tools = IscTools(isc, pb, session, emit, poll_seconds=0)
    await tools.create_source()
    await tools.configure_source()
    await tools.peek_accounts()
    await tools.start_aggregation()
    await tools.test_connection()
    return [[c.request.method, c.request.url.path, c.request.headers.get("X-SailPoint-Experimental", ""),
             c.request.headers.get("content-type", "").split(";")[0], c.request.content.decode()[:200]]
            for c in isc_mock.calls]


def test_offered_tools_unchanged(pb) -> None:  # type: ignore[no-untyped-def]
    assert snapshot_tools(pb) == EXPECTED_TOOLS


def test_tool_definitions_unchanged(pb) -> None:  # type: ignore[no-untyped-def]
    assert snapshot_tool_defs(pb) == EXPECTED_TOOL_DEFS_SHA


def test_static_system_prompt_unchanged(pb) -> None:  # type: ignore[no-untyped-def]
    assert snapshot_static(pb) == EXPECTED_STATIC_SHA


async def test_isc_calls_unchanged(isc, isc_mock, pb, emit) -> None:  # type: ignore[no-untyped-def]
    assert await snapshot_calls(isc, isc_mock, pb, emit) == EXPECTED_CALLS
