"""T044: generic ISC tools against a mocked tenant. create_source sends the creation-only fields; a same-name source
owned by someone else is never touched (SC-007); every write emits exactly one action event (SC-006)."""

import json

import httpx

from onboarding_agent.isc.tools import IscTools

from .conftest import EXTERNAL_ID, SPEC_ID, sample_session


async def test_create_source_sends_creation_only_fields(isc, isc_mock, pb, emit) -> None:  # type: ignore[no-untyped-def]
    isc_mock.get("/v3/sources").respond(200, json=[])
    create = isc_mock.post("/v3/sources").respond(201, json={
        "id": "2c91808a" * 4, "name": "AWS - Acme Org", "connectorAttributes": {"spConnectorInstanceId": "inst"}})
    session = sample_session()
    tools = IscTools(isc, pb, session, emit)
    result = await tools.create_source()
    assert result["created"] and result["connector_instance_linked"]
    body = json.loads(create.calls.last.request.content)
    attrs = body["connectorAttributes"]
    assert body["connector"] == "awssaas"
    assert attrs["spConnectorSpecId"] == SPEC_ID
    assert attrs["idnProxyType"] == "sp-connect"
    assert attrs["spConnectorSupportsCustomSchemas"] is True
    assert attrs["externalId"] == EXTERNAL_ID
    assert body["owner"] == {"type": "IDENTITY", "id": "a" * 32}
    assert session["source"]["id"] == "2c91808a" * 4
    assert len(emit.of("action")) == 1 and emit.of("action")[0]["result"] == "ok"
    assert {"type": "set_step", "step": "source_created", "state": "passed"} in emit.events
    assert emit.of("source")[0]["name"] == "AWS - Acme Org"


async def test_same_name_source_owned_by_someone_else_is_untouched(isc, isc_mock, pb, emit) -> None:  # type: ignore[no-untyped-def]
    isc_mock.get("/v3/sources").respond(200, json=[{"id": "f" * 32, "name": "AWS - Acme Org",
                                                    "owner": {"name": "someone.else"}}])
    post = isc_mock.post("/v3/sources")
    patch = isc_mock.patch(url__regex=r"/v3/sources/.*")
    tools = IscTools(isc, pb, sample_session(), emit)
    result = await tools.create_source()
    assert result["created"] is False and result["owned_by"] == "someone.else"
    assert not post.called and not patch.called
    actions = emit.of("action")
    assert len(actions) == 1 and actions[0]["result"] == "failed" and "someone.else" in actions[0]["error"]
    # configure refuses too: the session never created a source
    assert (await tools.configure_source())["configured"] is False
    assert not patch.called


async def test_configure_maps_session_values_to_connector_fields(isc, isc_mock, pb, emit) -> None:  # type: ignore[no-untyped-def]
    sid = "2c91808a" * 4
    patch = isc_mock.patch(f"/v3/sources/{sid}").respond(200, json={})
    tools = IscTools(isc, pb, sample_session(source={"id": sid, "name": "AWS - Acme Org"}), emit)
    result = await tools.configure_source()
    assert result["configured"]
    ops = {op["path"].split("/")[-1]: op["value"] for op in json.loads(patch.calls.last.request.content)}
    assert ops["roleName"] == "SailPointISCRole-acme-demo"
    assert ops["managementAccountId"] == "111122223333"
    assert ops["cloudScope"] == ["111122223333", "444455556666"]
    assert ops["changePasswordPolicyARN"] == "arn:aws:iam::aws:policy/IAMUserChangePassword"
    assert ops["enableDiscoverBedrockAgentCore"] is True and ops["AgentCoreAwsRegion"] == ["ap-southeast-1"]
    assert ops["enableDiscoverBedrockAgent"] is False and "AgentAwsRegion" not in ops
    assert ops["externalId"] == EXTERNAL_ID
    assert patch.calls.last.request.headers["content-type"] == "application/json-patch+json"
    assert len(emit.of("action")) == 1


async def test_role_arn_is_rejected(isc, isc_mock, pb, emit) -> None:  # type: ignore[no-untyped-def]
    session = sample_session(source={"id": "x" * 32, "name": "n"})
    session["details"]["role_name"] = "arn:aws:iam::111122223333:role/SailPointISCRole"
    result = await IscTools(isc, pb, session, emit).configure_source()
    assert result["configured"] is False and "not an ARN" in result["error"]
    assert emit.of("action")[0]["result"] == "failed"


async def test_connection_check_failure_reports_error_and_one_action(isc, isc_mock, pb, emit) -> None:  # type: ignore[no-untyped-def]
    sid = "2c91808a" * 4
    isc_mock.post(f"/beta/sources/{sid}/connector/peek-resource-objects").respond(
        400, json={"detailCode": "400.1 Bad request content", "messages": [{"text":
            "AWS Client creation failed: is not authorized to perform: sts:AssumeRole"}]})
    tools = IscTools(isc, pb, sample_session(source={"id": sid, "name": "n"}), emit)
    result = await tools.peek_accounts()
    assert result["ok"] is False and "sts:AssumeRole" in result["error"]
    assert {"type": "set_step", "step": "connection_check", "state": "failed"} in emit.events
    assert len(emit.of("action")) == 1


async def test_connection_check_success_marks_application_ready(isc, isc_mock, pb, emit) -> None:  # type: ignore[no-untyped-def]
    sid = "2c91808a" * 4
    isc_mock.post(f"/beta/sources/{sid}/connector/peek-resource-objects").respond(
        200, json={"resourceObjects": [{"identity": "alice"}, {"identity": "bob"}]})
    result = await IscTools(isc, pb, sample_session(source={"id": sid, "name": "n"}), emit).peek_accounts()
    assert result == {"ok": True, "accounts_read": 2, "sample": ["alice", "bob"]}
    assert {"type": "set_step", "step": "application_ready", "state": "passed"} in emit.events


async def test_aggregation_waits_for_tasks(isc, isc_mock, pb, emit) -> None:  # type: ignore[no-untyped-def]
    sid = "2c91808a" * 4
    isc_mock.post(f"/beta/sources/{sid}/load-accounts").respond(200, json={"task": {"id": "t1"}})
    isc_mock.post(f"/beta/sources/{sid}/load-entitlements").respond(200, json={"id": "t2"})
    isc_mock.get("/beta/task-status/t1").mock(side_effect=[httpx.Response(200, json={}),
                                                           httpx.Response(200, json={"completionStatus": "SUCCESS"})])
    isc_mock.get("/beta/task-status/t2").respond(200, json={"completionStatus": "SUCCESS"})
    isc_mock.get("/v3/accounts").respond(200, json=[{"id": "1"}, {"id": "2"}, {"id": "3"}])
    tools = IscTools(isc, pb, sample_session(source={"id": sid, "name": "n"}), emit, poll_seconds=0)
    result = await tools.start_aggregation()
    assert result == {"ok": True, "task_ids": ["t1", "t2"], "accounts_on_source": 3}
    acts = emit.of("action")
    # T158 (research R24): `running` first, then the final result under the same ref, with task states and counts.
    assert [a["result"] for a in acts] == ["running", "running", "ok"]
    assert len({a["action_ref"] for a in acts}) == 1
    assert acts[0]["task_ids"] == ["t1"] and acts[0]["duration_ms"] is None
    final = acts[-1]
    assert final["task_ids"] == ["t1", "t2"] and final["response"]["task_states"] == {"t1": "SUCCESS", "t2": "SUCCESS"}
    assert final["response"]["counts"] == {"accounts": 3} and isinstance(final["duration_ms"], int)
    assert final["request"]["entitlements"] is True and final["started_at"]


async def test_test_connection_before_aggregation_gives_hint(isc, isc_mock, pb, emit) -> None:  # type: ignore[no-untyped-def]
    sid = "2c91808a" * 4
    isc_mock.post(f"/beta/sources/{sid}/connector/test-configuration").respond(
        200, json={"status": "FAILURE", "details": "NullPointerException ... \"req.input\" is null"})
    result = await IscTools(isc, pb, sample_session(source={"id": sid, "name": "n"}), emit).test_connection()
    assert result["ok"] is False and "aggregation" in result["hint"]


async def test_delete_only_the_session_source(isc, isc_mock, pb, emit) -> None:  # type: ignore[no-untyped-def]
    tools = IscTools(isc, pb, sample_session(), emit)
    assert (await tools.delete_session_source())["deleted"] is False
    sid = "2c91808a" * 4
    delete = isc_mock.delete(f"/v3/sources/{sid}").respond(204)
    session = sample_session(source={"id": sid, "name": "n"})
    assert (await IscTools(isc, pb, session, emit).delete_session_source())["deleted"] is True
    assert delete.called and session["source"] is None


async def test_owner_lookup_survives_sailpoint_500(isc, isc_mock, pb, emit, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """SailPoint's public-identities lookup can fail on its side (HTTP 500 from its segment cache): retry, then
    search; the source is still created with the right owner."""
    import onboarding_agent.isc.tools as tools_mod

    monkeypatch.setattr(tools_mod, "OWNER_RETRY_SECONDS", 0)
    redis_500 = {"errorName": "UncheckedExecutionException",
                 "errorMessage": "Error getting isSegmentEnabledForCurrentUser from redis"}
    lookup = isc_mock.get("/v3/public-identities").respond(500, json=redis_500)
    search = isc_mock.post("/v3/search").respond(200, json=[{"id": "b" * 32, "name": "w.rakkiatngam"}])
    isc_mock.get("/v3/sources").respond(200, json=[])
    create = isc_mock.post("/v3/sources").respond(201, json={
        "id": "2c91808a" * 4, "name": "AWS - Acme Org", "connectorAttributes": {"spConnectorInstanceId": "inst"}})
    result = await IscTools(isc, pb, sample_session(), emit).create_source()
    assert result["created"]
    assert lookup.call_count == 2 and search.called
    assert json.loads(search.calls.last.request.content)["indices"] == ["identities"]
    assert json.loads(create.calls.last.request.content)["owner"] == {"type": "IDENTITY", "id": "b" * 32}


async def test_every_action_carries_its_request_response_and_timing(isc, isc_mock, pb, emit) -> None:  # type: ignore[no-untyped-def]
    """T158: request (no credentials), response with counts or the error, action_ref, start and duration."""
    sid = "2c91808a" * 4
    isc_mock.post(f"/beta/sources/{sid}/connector/peek-resource-objects").respond(
        200, json={"resourceObjects": [{"identity": "alice"}, {"identity": "bob"}, {"identity": "carol"}]})
    isc_mock.post(f"/beta/sources/{sid}/connector/test-configuration").respond(400, json={"messages": [
        {"text": "req.input is null"}]})
    tools = IscTools(isc, pb, sample_session(source={"id": sid, "name": "n"}), emit)
    await tools.peek_accounts()
    await tools.test_connection()
    peek, test = emit.of("action")
    assert peek["action_ref"] != test["action_ref"]
    assert peek["request"] == {"objectType": "account", "maxCount": 5} and peek["response"]["counts"] == {"accounts": 3}
    assert test["result"] == "failed" and "req.input" in test["response"]["error"]
    for a in (peek, test):
        assert a["started_at"] and isinstance(a["duration_ms"], int)
        assert "token" not in str(a["request"]).lower() and "secret" not in str(a["request"]).lower()


async def test_configure_records_the_values_sent(isc, isc_mock, pb, emit) -> None:  # type: ignore[no-untyped-def]
    sid = "2c91808a" * 4
    isc_mock.patch(f"/v3/sources/{sid}").respond(200, json={})
    await IscTools(isc, pb, sample_session(source={"id": sid, "name": "n"}), emit).configure_source()
    [a] = emit.of("action")
    assert a["result"] == "ok" and "SailPointISCRole-acme-demo" in str(a["request"]["fields"])
