"""Tenant registry (data-model.md `tenants`). The credential is checked once while it is in the request, then handed
to the credential store and forgotten (FR-025). Later checks go through the agent, which holds no secret either."""

import re
from datetime import UTC, datetime
from typing import Any

import httpx
from bson import ObjectId

from ..config import settings
from ..db import db
from . import identity

CLIENT_ID_RE = re.compile(r"^[0-9a-f]{32}$")
SECRET_RE = re.compile(r"^[0-9a-f]{64}$")


class TenantError(ValueError):
    pass


def normalise_host(value: str) -> str:
    """Accept the API host, the UI host or a pasted URL; return the API host.

    Same rule as the skill's `isc_base_url` (.claude/skills/sailpoint-isc-aws-connector/scripts/lib.sh):
    acme.identitynow.com -> acme.api.identitynow.com
    acme-demo.identitynow-demo.com -> acme-demo.api.identitynow-demo.com
    """
    host = re.sub(r"^https?://", "", value.strip()).split("/")[0].lower()
    if "." not in host or not re.match(r"^[a-z0-9.-]+$", host):
        raise TenantError("tenant host must be the full host, e.g. acme.api.identitynow.com")
    m = re.match(r"^([a-z0-9-]+)\.(identitynow[a-z0-9-]*\.com)$", host)
    if m:
        return f"{m.group(1)}.api.{m.group(2)}"
    return host


def check_pat_shape(client_id: str, client_secret: str) -> None:
    if CLIENT_ID_RE.match(client_secret.strip()) and SECRET_RE.match(client_id.strip()):
        raise TenantError("The client ID and secret look swapped: the ID is 32 hex characters, the secret 64.")
    if not CLIENT_ID_RE.match(client_id.strip()):
        raise TenantError("The client ID should be 32 hex characters.")
    if not SECRET_RE.match(client_secret.strip()):
        raise TenantError("The client secret should be 64 hex characters.")


def _base_url(api_host: str) -> str:
    return settings().isc_base_url or f"https://{api_host}"


async def check_with_credential(api_host: str, client_id: str, client_secret: str) -> dict[str, Any]:
    """Token + GET /beta/tenant using the credential in hand. Returns {'status', 'external_id'}."""
    base = _base_url(api_host)
    async with httpx.AsyncClient(timeout=20) as http:
        token = await http.post(f"{base}/oauth/token", data={
            "grant_type": "client_credentials", "client_id": client_id, "client_secret": client_secret,
        })
        if token.status_code in (400, 401, 403):
            return {"status": "credential_rejected", "external_id": None}
        token.raise_for_status()
        access = token.json()["access_token"]
        tenant = await http.get(f"{base}/beta/tenant", headers={"Authorization": f"Bearer {access}"})
        if tenant.status_code in (401, 403):
            return {"status": "credential_rejected", "external_id": None}
        tenant.raise_for_status()
        body = tenant.json()
    # The tenant External ID is on the "idn" product (as the skill's tenant_external_id() reads it).
    external_id = next((p.get("attributes", {}).get("externalId") for p in body.get("products") or []
                        if p.get("productName") == "idn"), None) or body.get("externalId")
    return {"status": "usable", "external_id": external_id}


def public(t: dict) -> dict[str, Any]:
    return {
        "id": str(t["_id"]),
        "name": t["name"],
        "api_host": t["api_host"],
        "external_id": t.get("external_id"),
        "credential_hint": t.get("credential_hint", ""),
        "status": t.get("status", "unchecked"),
        "last_checked_at": t.get("last_checked_at"),
    }


async def list_tenants() -> list[dict]:
    return [t async for t in db().tenants.find({}, sort=[("name", 1)])]


async def get(tenant_id: str | ObjectId) -> dict | None:
    return await db().tenants.find_one({"_id": ObjectId(tenant_id)})


async def register(name: str, host: str, client_id: str, client_secret: str, admin_id: ObjectId) -> dict:
    name = name.strip()
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{1,62}$", name):
        raise TenantError("name must be 2–63 characters: letters, digits, space, '.', '_' or '-'")
    if await db().tenants.find_one({"name": name}):
        raise TenantError("a tenant with that name is already registered")
    api_host = normalise_host(host)
    client_id, client_secret = client_id.strip(), client_secret.strip()
    check_pat_shape(client_id, client_secret)
    result = await check_with_credential(api_host, client_id, client_secret)
    provider = identity.provider_name(name)
    identity.store().put(provider, api_host, client_id, client_secret)
    now = datetime.now(UTC)
    doc = {
        "name": name,
        "api_host": api_host,
        "credential_provider": provider,
        "credential_hint": client_id[-4:],
        "external_id": result["external_id"],
        "status": result["status"],
        "last_checked_at": now,
        "created_by": admin_id,
        "created_at": now,
    }
    inserted = await db().tenants.insert_one(doc)
    doc["_id"] = inserted.inserted_id
    return doc


async def replace_credential(tenant_id: str, client_id: str, client_secret: str) -> dict:
    tenant = await get(tenant_id)
    if not tenant:
        raise TenantError("no such tenant")
    client_id, client_secret = client_id.strip(), client_secret.strip()
    check_pat_shape(client_id, client_secret)
    result = await check_with_credential(tenant["api_host"], client_id, client_secret)
    identity.store().put(tenant["credential_provider"], tenant["api_host"], client_id, client_secret)
    update = {"credential_hint": client_id[-4:], "status": result["status"], "last_checked_at": datetime.now(UTC)}
    if result["external_id"]:
        update["external_id"] = result["external_id"]
    await db().tenants.update_one({"_id": tenant["_id"]}, {"$set": update})
    return await get(tenant["_id"])  # type: ignore[return-value]


async def set_status(tenant_id: ObjectId, status: str, external_id: str | None = None) -> None:
    update: dict[str, Any] = {"status": status, "last_checked_at": datetime.now(UTC)}
    if external_id:
        update["external_id"] = external_id
    await db().tenants.update_one({"_id": tenant_id}, {"$set": update})
