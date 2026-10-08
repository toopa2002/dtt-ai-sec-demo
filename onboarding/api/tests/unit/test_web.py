"""The API serves the web build from the same container: files, the Angular fallback, and no leaks outside it."""

import httpx
import pytest
from fastapi import FastAPI

from onboarding_api import web


@pytest.fixture
async def client(tmp_path):  # type: ignore[no-untyped-def]
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "index.html").write_text("<app-root></app-root>")
    (tmp_path / "web" / "main-ABC.js").write_text("console.log(1)")
    (tmp_path / "secret.txt").write_text("outside")
    app = FastAPI()

    @app.get("/onboarding/api/healthz")
    async def healthz() -> dict:
        return {"ok": True}

    app.include_router(web.router(tmp_path / "web", "/onboarding", "/onboarding/api"))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        yield c


async def test_serves_files_and_falls_back_to_index(client: httpx.AsyncClient) -> None:
    r = await client.get("/onboarding/main-ABC.js")
    assert r.status_code == 200 and r.text == "console.log(1)"
    assert r.headers["x-frame-options"] == "DENY" and r.headers["x-content-type-options"] == "nosniff"
    for path in ("/onboarding/", "/onboarding/sessions/abc/iam", "/onboarding/index.html"):
        r = await client.get(path)
        assert r.status_code == 200 and "app-root" in r.text and r.headers["cache-control"] == "no-cache"


async def test_redirects_bare_prefix(client: httpx.AsyncClient) -> None:
    r = await client.get("/onboarding")
    assert r.status_code == 301 and r.headers["location"] == "/onboarding/"
    assert (await client.head("/onboarding/sessions/abc/iam")).status_code == 200


async def test_api_paths_are_not_the_web_app(client: httpx.AsyncClient) -> None:
    assert (await client.get("/onboarding/api/healthz")).json() == {"ok": True}
    assert (await client.get("/onboarding/api/nope")).status_code == 404
    assert (await client.get("/onboarding/api")).status_code == 404


async def test_no_build_yet_is_404(tmp_path) -> None:  # type: ignore[no-untyped-def]
    app = FastAPI()
    app.include_router(web.router(tmp_path / "missing", "/onboarding", "/onboarding/api"))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.get("/onboarding/")).status_code == 404


async def test_no_files_outside_the_build(client: httpx.AsyncClient) -> None:
    for path in ("/onboarding/../secret.txt", "/onboarding/%2e%2e/secret.txt", "/onboarding/..%2fsecret.txt"):
        assert "outside" not in (await client.get(path)).text
