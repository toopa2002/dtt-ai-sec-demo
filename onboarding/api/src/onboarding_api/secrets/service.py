"""The application secret's metadata and lifecycle (spec 002 FR-120–FR-125, data-model.md ApplicationSecret).

missing → received (owner submits) → in_isc (configure/apply put it in the source) → vault_deleted (Test Connection
passed); a submit from any state replaces the vault copy and goes back to received. MongoDB holds metadata only; the
value goes once from the request to the vault (`store.py`).
"""

import re
from datetime import UTC, date, datetime, timedelta
from typing import Any

from bson import ObjectId

from ..audit import audit
from ..chat import events
from ..db import db
from . import store

GUID = re.compile(r"^\s*[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\s*$", re.I)
MSG_GUID = "this is the secret's ID, not its Value"
MSG_LENGTH = "must be 16–128 characters"
MSG_SPACE = "must not contain spaces or line breaks"
MSG_EXPIRY = "must be in the future and at most 2 years ahead"
MSG_EXPIRY_MISSING = "is required: copy the Expires date shown next to the secret in Entra"
EXPIRY_WARNING_DAYS = 30


class SecretError(ValueError):
    def __init__(self, errors: dict[str, str]):
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))
        self.errors = errors


def validate(value: Any, expires_on: Any, today: date | None = None) -> date:
    """FR-121: reject a GUID (the secret's ID), odd lengths and whitespace; require a plausible expiry date."""
    today = today or datetime.now(UTC).date()
    errors: dict[str, str] = {}
    text = value if isinstance(value, str) else ""
    if GUID.match(text):
        errors["value"] = MSG_GUID
    elif not 16 <= len(text) <= 128:
        errors["value"] = MSG_LENGTH
    elif re.search(r"\s", text):
        errors["value"] = MSG_SPACE
    expiry: date | None = None
    if not expires_on:
        errors["expires_on"] = MSG_EXPIRY_MISSING
    else:
        try:
            expiry = date.fromisoformat(str(expires_on)[:10])
        except ValueError:
            errors["expires_on"] = MSG_EXPIRY
        else:
            if not today < expiry <= today + timedelta(days=731):
                errors["expires_on"] = MSG_EXPIRY
    if errors:
        raise SecretError(errors)
    assert expiry is not None
    return expiry


def status(session: dict, names: dict[str, str] | None = None, today: date | None = None) -> dict | None:
    """Metadata only, for both participants; `expires_soon` is computed on read (FR-121 edge case, 30 days)."""
    secret = session.get("application_secret")
    if not secret:
        return None
    today = today or datetime.now(UTC).date()
    expires = secret.get("expires_on")
    expires_day = date.fromisoformat(expires) if isinstance(expires, str) else None
    provided_by = secret.get("provided_by")
    return {
        "state": secret.get("state", "missing"),
        "provided_by": (names or {}).get(str(provided_by)) if provided_by else None,
        "provided_at": secret.get("provided_at"),
        "expires_on": expires,
        "expires_soon": bool(expires_day and (expires_day - today).days <= EXPIRY_WARNING_DAYS),
        "applied_at": secret.get("applied_at"),
        "replaced_at": secret.get("replaced_at"),
        "vault_deleted_at": secret.get("vault_deleted_at"),
    }


async def _names(session: dict) -> dict[str, str]:
    ids = [i for i in (session.get("iam_engineer_id"), session.get("application_owner_id")) if i]
    return {str(u["_id"]): u["display_name"] async for u in db().users.find({"_id": {"$in": ids}})}


async def _emit(session_id: ObjectId) -> dict | None:
    session = await db().sessions.find_one({"_id": session_id})
    out = status(session, await _names(session)) if session else None
    await events.emit(session_id, "secret.updated", out or {})
    return out


async def submit(session: dict, user_id: ObjectId, value: str, expires_on: Any) -> dict | None:
    expiry = validate(value, expires_on)
    name = (session.get("application_secret") or {}).get("provider") or store.provider_name(str(session["_id"]))
    store.store().put(name, value)  # CredentialStoreError propagates (route: 502, AWS error code only)
    now = datetime.now(UTC)
    previous = session.get("application_secret") or {}
    replaced = previous.get("state") not in (None, "missing")
    update = {"provider": name, "state": "received", "provided_by": user_id, "provided_at": now,
              "expires_on": expiry.isoformat(), "applied_at": None, "vault_deleted_at": None,
              "replaced_at": now if replaced else previous.get("replaced_at")}
    await db().sessions.update_one({"_id": session["_id"]}, {"$set": {"application_secret": update}})
    await audit.record("application_secret_replaced" if replaced else "application_secret_received", user_id,
                       target=str(session["_id"]), detail={"expires_on": expiry.isoformat()})
    return await _emit(session["_id"])


async def mark_applied(session_id: ObjectId) -> None:
    result = await db().sessions.update_one(
        {"_id": session_id, "application_secret.state": "received"},
        {"$set": {"application_secret.state": "in_isc", "application_secret.applied_at": datetime.now(UTC)}})
    if result.modified_count:
        await _emit(session_id)


async def delete_vault_copy(session_id: ObjectId, source_id: str | None = None) -> bool:
    """FR-125: after Test Connection passes with the secret, ISC holds the only copy."""
    session = await db().sessions.find_one({"_id": session_id})
    secret = (session or {}).get("application_secret") or {}
    if secret.get("state") != "in_isc":
        return False
    store.store().delete(secret["provider"])
    await db().sessions.update_one({"_id": session_id}, {"$set": {
        "application_secret.state": "vault_deleted", "application_secret.vault_deleted_at": datetime.now(UTC)}})
    await audit.record("application_secret_vault_deleted", None, target=str(session_id),
                       detail={"source_id": source_id})
    await _emit(session_id)
    return True


async def discard_on_finish(session: dict) -> None:
    """A finished (and later expired) session keeps no vault copy."""
    secret = session.get("application_secret") or {}
    if secret.get("provider") and secret.get("state") in ("received", "in_isc"):
        store.store().delete(secret["provider"])
        await db().sessions.update_one({"_id": session["_id"]}, {"$set": {
            "application_secret.state": "vault_deleted", "application_secret.vault_deleted_at": datetime.now(UTC)}})
        await audit.record("application_secret_vault_deleted", None, target=str(session["_id"]),
                           detail={"reason": "session finished"})


def turn_context(session: dict) -> dict | None:
    """What the agent payload carries: metadata only (contracts/agent-invocation.md)."""
    secret = session.get("application_secret")
    if not secret:
        return None
    return {"provider": secret.get("provider"), "state": secret.get("state"), "expires_on": secret.get("expires_on"),
            "provided_by": str(secret.get("provided_by")) if secret.get("provided_by") else None}
