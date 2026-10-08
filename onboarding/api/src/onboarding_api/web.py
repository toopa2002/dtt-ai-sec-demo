"""The web app's production build, served by the API from the same container (no separate web server).

Files under `prefix` come from `web_dir`; any other path there that is not an API path gets `index.html`, so the
Angular router handles it. Headers match what nginx used to set.
"""

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, RedirectResponse

HEADERS = {"X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY"}


def router(web_dir: Path, prefix: str, api_path: str) -> APIRouter:
    root = web_dir.resolve()
    index = root / "index.html"
    api_prefix = api_path.removeprefix(prefix).strip("/") + "/"
    r = APIRouter()

    @r.api_route(prefix, methods=["GET", "HEAD"], include_in_schema=False)
    async def slash() -> RedirectResponse:
        return RedirectResponse(f"{prefix}/", status_code=301)

    @r.api_route(prefix + "/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    async def files(path: str) -> FileResponse:
        if path.startswith(api_prefix) or path + "/" == api_prefix or not index.is_file():  # no build (yet)
            raise HTTPException(404, "Not Found")
        file = (root / path).resolve()
        if path and file.is_relative_to(root) and file.is_file() and file != index:
            return FileResponse(file, headers=HEADERS)
        return FileResponse(index, headers={**HEADERS, "Cache-Control": "no-cache"})

    return r
