"""FastAPI app: every route under /onboarding/api (contracts/session-api.openapi.yaml), plus the web app's build
under /onboarding/ when WEB_DIR is set (one container serves both)."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import db, metrics, web
from .audit import routes as audit_routes
from .auth import admin_routes
from .auth import routes as auth_routes
from .catalog import routes as catalog_routes
from .chat import attachments, followups, stream
from .chat import routes as chat_routes
from .config import settings
from .logging import setup_logging
from .secrets import routes as secret_routes
from .secrets import store as secret_store
from .sessions import admin_routes as session_admin_routes
from .sessions import routes as session_routes
from .tenants import routes as tenant_routes


@asynccontextmanager
async def lifespan(_: FastAPI):  # type: ignore[no-untyped-def]
    setup_logging()
    secret_store.check_startup()
    await db.ensure_indexes()
    followups.start_loop()  # spec 002 FR-139: long aggregations followed without a turn
    yield
    await followups.stop_loop()
    await db.close()


app = FastAPI(title="ISC Onboarding — session API", lifespan=lifespan, docs_url=None, redoc_url=None,
              openapi_url=f"{settings().base_path}/openapi.json")

api = APIRouter(prefix=settings().base_path)
for module in (auth_routes, admin_routes, catalog_routes, tenant_routes, session_routes, chat_routes, attachments,
               stream, audit_routes, session_admin_routes, secret_routes):
    api.include_router(module.router)


@api.get("/healthz", include_in_schema=False)
async def healthz() -> dict:
    await db.db().command("ping")
    return {"ok": True}


@api.get("/healthz/metrics", include_in_schema=False)
async def metrics_summary() -> dict:
    return metrics.summary()


app.include_router(api)
if secret_store.local_mode():
    app.include_router(secret_routes.local_router)
if settings().web_dir:
    app.include_router(web.router(Path(settings().web_dir), settings().base_path.rsplit("/", 1)[0],
                                  settings().base_path))


@app.exception_handler(StarletteHTTPException)
async def http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Errors follow the contract's flat {code, message[, retry_after_seconds]} shape."""
    if isinstance(exc.detail, dict):
        body = exc.detail
    else:
        body = {"code": "error", "message": str(exc.detail)}
    headers = {"Retry-After": str(body["retry_after_seconds"])} if "retry_after_seconds" in body else None
    return JSONResponse(status_code=exc.status_code, content=body, headers=headers)


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    fields = "; ".join(f"{'.'.join(str(p) for p in e['loc'][1:])}: {e['msg']}" for e in exc.errors())
    return JSONResponse(status_code=422, content={"code": "validation_failed", "message": fields})


@app.exception_handler(Exception)
async def unhandled(_: Request, exc: Exception) -> JSONResponse:  # noqa: ARG001
    return JSONResponse(status_code=500, content={"code": "internal", "message": "Something went wrong."})
