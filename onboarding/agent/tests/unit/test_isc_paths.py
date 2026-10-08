"""T010 (spec 002 research R4, FR-138): the v2026 path table, the experimental header only where needed, counts from
X-Total-Count, and the v2026 accounts filter field."""

import httpx
import respx

from onboarding_agent.isc import paths
from onboarding_agent.isc.client import IscClient

BASE = "https://acme-demo.api.identitynow-demo.com"
EXPERIMENTAL = ["/aggregate-agents$", "/datasets(/|$)", "^/v2026/machine-identities"]


def test_every_v2026_path_is_versioned() -> None:
    for op, template in paths.V2026.ops.items():
        assert template.startswith("/v2026/"), op
    assert set(paths.V2026.ops) == set(paths.LEGACY.ops)
    assert paths.for_playbook({"isc_api": "v2026"}) is paths.V2026
    assert paths.for_playbook({}) is paths.LEGACY


def test_accounts_filter_field() -> None:
    assert paths.V2026.account_source_field == "source.id"
    assert paths.LEGACY.account_source_field == "sourceId"


async def _token() -> str:
    return "t"


async def test_experimental_header_only_on_listed_paths() -> None:
    client = IscClient("h", _token, base_url=BASE, experimental=EXPERIMENTAL)
    with respx.mock(base_url=BASE) as mock:
        route = mock.route().respond(200, json={})
        for path in ("/v2026/sources/s1", "/v2026/sources/s1/aggregate-agents", "/v2026/sources/s1/datasets",
                     "/v2026/sources/s1/datasets/azure:foundry", "/v2026/machine-identities",
                     "/v2026/sources/s1/load-accounts"):
            await client.get(path)
        sent = {c.request.url.path: c.request.headers.get("X-SailPoint-Experimental") for c in route.calls}
    await client.close()
    assert sent == {"/v2026/sources/s1": None, "/v2026/sources/s1/aggregate-agents": "true",
                    "/v2026/sources/s1/datasets": "true", "/v2026/sources/s1/datasets/azure:foundry": "true",
                    "/v2026/machine-identities": "true", "/v2026/sources/s1/load-accounts": None}


async def test_legacy_client_keeps_header_everywhere() -> None:
    client = IscClient("h", _token, base_url=BASE)
    with respx.mock(base_url=BASE) as mock:
        route = mock.route().respond(200, json={})
        await client.get("/v3/sources")
    await client.close()
    assert route.calls.last.request.headers["X-SailPoint-Experimental"] == "true"


async def test_count_reads_total_header() -> None:
    client = IscClient("h", _token, base_url=BASE, experimental=EXPERIMENTAL)
    with respx.mock(base_url=BASE) as mock:
        route = mock.get("/v2026/accounts").respond(200, json=[{}], headers={"X-Total-Count": "4973"})
        n = await client.count("/v2026/accounts", 'source.id eq "s1"')
    await client.close()
    assert n == 4973
    q = route.calls.last.request.url.params
    assert q["count"] == "true" and q["limit"] == "1" and q["filters"] == 'source.id eq "s1"'


async def test_multipart_bodies() -> None:
    client = IscClient("h", _token, base_url=BASE, experimental=EXPERIMENTAL)
    with respx.mock(base_url=BASE) as mock:
        route = mock.post(url__regex=r".*").respond(202, json={"id": "t"})
        await client.post_multipart("/v2026/sources/s1/load-accounts", {"disableOptimization": "true"})
        await client.post_multipart("/v2026/sources/s1/load-entitlements")
    await client.close()
    first, second = route.calls
    assert first.request.headers["content-type"].startswith("multipart/form-data")
    assert b'name="disableOptimization"' in first.request.content and b"true" in first.request.content
    assert second.request.headers["content-type"].startswith("multipart/form-data")


_ = httpx


async def test_task_check_mode_reads_status_without_a_model(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from onboarding_agent import main

    monkeypatch.setenv("ONBOARDING_ISC_TOKEN", "stub")
    monkeypatch.setenv("ONBOARDING_ISC_BASE_URL", BASE)
    with respx.mock(base_url=BASE) as mock:
        mock.get("/v2026/task-status/t1").respond(200, json={"completionStatus": "SUCCESS", "messages": []})
        mock.get("/v2026/task-status/t2").respond(200, json={"completionStatus": None})
        events = [e async for e in main.invoke({"mode": "task_check", "isc_api": "v2026", "task_ids": ["t1", "t2"],
                                                "tenant": {"api_host": "h", "credential_provider": "p"}})]
    assert events == [{"type": "task_check", "tasks": [
        {"id": "t1", "completion_status": "SUCCESS", "messages": []},
        {"id": "t2", "completion_status": None, "messages": []}]}]
