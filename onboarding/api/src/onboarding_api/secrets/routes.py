"""/sessions/{id}/application-secret (spec 002 FR-120–FR-122, contracts/session-api.openapi.yaml).

PUT is write-only and for this session's application owner only; GET returns metadata to both participants. No route
ever returns the value. The request body is never logged: nothing in this API logs bodies, and the masker covers the
Entra secret format as a second line (masking.py).
"""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from ..auth.deps import CurrentUser, current_user, error
from ..catalog import catalog
from ..chat.access import participant_session
from ..tenants.identity import CredentialStoreError
from . import service, store

router = APIRouter(tags=["secrets"])


class SecretIn(BaseModel):
    value: str = Field(max_length=1024)
    expires_on: str | None = Field(default=None, max_length=32)


def _secret_type(session: dict) -> None:
    connector = catalog.get(session["connector_type"]) or {}
    if not connector.get("secret"):
        raise error("not_found", "This connector type has no application secret.", status.HTTP_404_NOT_FOUND)


@router.put("/sessions/{session_id}/application-secret")
async def put_secret(session_id: str, body: SecretIn, request: Request,
                     user: CurrentUser = Depends(current_user)) -> dict:  # noqa: B008
    session, role = await participant_session(session_id, user)
    _secret_type(session)
    if role != "application_owner":
        raise error("forbidden_role", "Only the session's application owner can provide the secret.",
                    status.HTTP_403_FORBIDDEN)
    if session.get("status") == "finished":
        raise error("session_finished", "This session is finished.", status.HTTP_409_CONFLICT)
    try:
        out = await service.submit(session, user.id, body.value, body.expires_on)
    except service.SecretError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail={
            "code": "validation_failed", "message": str(exc), "errors": exc.errors}) from None
    except CredentialStoreError as exc:
        raise error("vault_refused", str(exc), status.HTTP_502_BAD_GATEWAY) from None
    from ..chat import turns  # late import: turns imports this package's service

    await turns.after_secret_submitted(session["_id"], user)
    return out or {}


@router.get("/sessions/{session_id}/application-secret")
async def get_secret(session_id: str, user: CurrentUser = Depends(current_user)) -> dict:  # noqa: B008
    session, _ = await participant_session(session_id, user)
    _secret_type(session)
    return service.status(session, await service._names(session)) or {"state": "missing"}


# Local stub runs only (research R1): the local agent reads the value the owner submitted, so the stub ISC receives
# exactly it. Registered only in local mode, outside the public /onboarding/ prefix, and for loopback callers only.
local_router = APIRouter(include_in_schema=False)


@local_router.get("/_local/apikey/{name}")
async def local_api_key(name: str, request: Request) -> dict:
    if not store.local_mode() or (request.client and request.client.host not in ("127.0.0.1", "::1")):
        raise error("not_found", "Not found.", status.HTTP_404_NOT_FOUND)
    value = store.LocalApiKeyStore.values.get(name)
    if value is None:
        raise error("not_found", "No such key.", status.HTTP_404_NOT_FOUND)
    return {"apiKey": value}
