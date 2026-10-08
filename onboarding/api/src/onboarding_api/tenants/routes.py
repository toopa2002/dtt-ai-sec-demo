"""/admin/tenants (admin only). Responses never contain the secret (FR-025)."""

import httpx
from fastapi import APIRouter, Depends, status
from pydantic import BaseModel

from ..agent_client import client as agent
from ..audit import audit
from ..auth.deps import CurrentUser, error, require_admin
from . import service
from .identity import CredentialStoreError

router = APIRouter(prefix="/admin/tenants", tags=["admin"])


class TenantIn(BaseModel):
    name: str
    api_host: str
    client_id: str
    client_secret: str


class CredentialIn(BaseModel):
    client_id: str
    client_secret: str


def _unprocessable(exc: Exception):  # type: ignore[no-untyped-def]
    return error("validation_failed", str(exc), status.HTTP_422_UNPROCESSABLE_CONTENT)


def _store_failed(exc: CredentialStoreError):  # type: ignore[no-untyped-def]
    hint = "" if "AWS credentials" in str(exc) else " Check the onboarding-api IAM user's permissions."
    return error("credential_store_failed",
                 f"SailPoint accepted the credential, but it could not be stored: {exc}.{hint}", status.HTTP_502_BAD_GATEWAY)


@router.get("")
async def list_tenants(_: CurrentUser = Depends(require_admin)) -> list[dict]:  # noqa: B008
    return [service.public(t) for t in await service.list_tenants()]


@router.post("", status_code=status.HTTP_201_CREATED)
async def add_tenant(body: TenantIn, admin: CurrentUser = Depends(require_admin)) -> dict:  # noqa: B008
    try:
        tenant = await service.register(body.name, body.api_host, body.client_id, body.client_secret, admin.id)
    except service.TenantError as exc:
        raise _unprocessable(exc) from None
    except CredentialStoreError as exc:
        raise _store_failed(exc) from None
    except httpx.HTTPError:
        raise error("tenant_unreachable", "SailPoint did not answer. Check the tenant host and try again.",
                    status.HTTP_502_BAD_GATEWAY) from None
    await audit.record("tenant_added", admin.id, target=tenant["name"])
    return service.public(tenant)


@router.put("/{tenant_id}/credential")
async def replace_credential(tenant_id: str, body: CredentialIn,
                             admin: CurrentUser = Depends(require_admin)) -> dict:  # noqa: B008
    try:
        tenant = await service.replace_credential(tenant_id, body.client_id, body.client_secret)
    except service.TenantError as exc:
        raise _unprocessable(exc) from None
    except CredentialStoreError as exc:
        raise _store_failed(exc) from None
    except httpx.HTTPError:
        raise error("tenant_unreachable", "SailPoint did not answer.", status.HTTP_502_BAD_GATEWAY) from None
    await audit.record("credential_replaced", admin.id, target=tenant["name"])
    return service.public(tenant)


@router.post("/{tenant_id}/check")
async def check_now(tenant_id: str, _: CurrentUser = Depends(require_admin)) -> dict:  # noqa: B008
    tenant = await service.get(tenant_id)
    if not tenant:
        raise error("not_found", "No such tenant.", status.HTTP_404_NOT_FOUND)
    result = await agent.tenant_check(tenant)
    await service.set_status(tenant["_id"], result["status"], result.get("external_id"))
    return service.public(await service.get(tenant_id))  # type: ignore[arg-type]
