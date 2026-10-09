"""/sessions (FR-005, US6). Only IAM engineers create; only participants read (404 otherwise)."""

from datetime import UTC, datetime

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from ..audit import audit
from ..auth.deps import CurrentUser, current_user, error, require_role
from ..catalog import catalog
from ..chat import events
from ..chat.access import participant_session
from ..db import db
from ..secrets import service as secrets
from ..tenants import service as tenants
from . import plan as plans
from . import repo

router = APIRouter(prefix="/sessions", tags=["sessions"])


class SessionIn(BaseModel):
    connector_type: str
    tenant_id: str
    details: dict = Field(default_factory=dict)
    application_owner_id: str | None = None
    accept_warnings: list[str] = Field(default_factory=list, max_length=10)  # spec 002 FR-103


def summary(s: dict) -> dict:
    return {
        "id": str(s["_id"]),
        "title": s["title"],
        "connector_type": s["connector_type"],
        "status": s["status"],
        "steps": {k: v["state"] for k, v in s["steps"].items()},
        "created_at": s["created_at"],
        "finished_at": s.get("finished_at"),
    }


async def full(s: dict) -> dict:
    tenant = await tenants.get(s["tenant_id"])
    users = {u["_id"]: u async for u in db().users.find(
        {"_id": {"$in": [i for i in (s["iam_engineer_id"], s.get("application_owner_id")) if i]}})}

    def person(user_id, role):  # type: ignore[no-untyped-def]
        u = users.get(user_id)
        return {"id": str(user_id), "display_name": u["display_name"], "username": u["username"],
                "online": events.online(s["_id"], role)} if u else None

    done, total, next_step = plans.progress(s.get("plan") or [])
    return summary(s) | {
        "tenant": {"id": str(tenant["_id"]), "name": tenant["name"], "api_host": tenant["api_host"],
                   "external_id": tenant.get("external_id")} if tenant else None,
        "details": s["details"],
        "source": s.get("source"),
        "waiting_on": s.get("waiting_on"),
        "waiting_reason": s.get("waiting_reason"),
        "check_order": {"display_name": s["check_order"]["display_name"], "at": s["check_order"]["at"]}
        if s.get("check_order") else None,
        "event_seq": s.get("event_seq", 0),
        "plan": plans.public(s.get("plan") or []),
        "milestone_order": catalog.milestone_order(s["connector_type"]),
        "mode": s.get("mode", "new"),
        "proof": s.get("proof"),  # spec 002 R18; removed for the application owner in get_session
        "followups": [{k: f.get(k) for k in ("plan_step", "kind", "started_at", "state")}
                      for f in s.get("followups") or []],
        "application_secret": secrets.status(s, {str(u["_id"]): u["display_name"] for u in users.values()}),
        "plan_done": done,
        "plan_total": total,
        "next_step_id": next_step,
        "reopened_at": s.get("reopened_at"),
        "participants": {
            "iam_engineer": person(s["iam_engineer_id"], "iam_engineer"),
            "application_owner": person(s.get("application_owner_id"), "application_owner"),
        },
    }


@router.get("")
async def list_sessions(status_: str | None = Query(None, alias="status", pattern="^(open|finished)$"),
                        user: CurrentUser = Depends(current_user)) -> list[dict]:  # noqa: B008
    return [summary(s) for s in await repo.list_for(user.id, status_)]


@router.get("/people")
async def application_owners(_: CurrentUser = Depends(require_role("iam_engineer"))) -> list[dict]:  # noqa: B008
    """Application owners an IAM engineer can invite."""
    cursor = db().users.find({"role": "application_owner", "status": {"$ne": "disabled"}},
                             projection={"display_name": 1, "username": 1}, sort=[("display_name", 1)])
    return [{"id": str(u["_id"]), "display_name": u["display_name"], "username": u["username"]} async for u in cursor]


@router.get("/tenants")
async def usable_tenants(_: CurrentUser = Depends(require_role("iam_engineer"))) -> list[dict]:  # noqa: B008
    return [{"id": str(t["_id"]), "name": t["name"], "api_host": t["api_host"], "status": t.get("status")}
            for t in await tenants.list_tenants()]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_session(body: SessionIn,
                         user: CurrentUser = Depends(require_role("iam_engineer"))) -> dict:  # noqa: B008
    connector = catalog.get(body.connector_type)
    if not connector:
        raise error("validation_failed", "Unknown connector type.", status.HTTP_422_UNPROCESSABLE_CONTENT)
    if connector["status"] != "available":
        raise error("connector_planned", f"{connector['name']} is planned and not available yet.",
                    status.HTTP_409_CONFLICT)
    tenant = await tenants.get(body.tenant_id) if ObjectId.is_valid(body.tenant_id) else None
    if not tenant:
        raise error("validation_failed", "Unknown tenant.", status.HTTP_422_UNPROCESSABLE_CONTENT)
    if tenant.get("status") != "usable":
        raise error("tenant_unusable", "This tenant's SailPoint credential isn't working. Ask an admin to check it.",
                    status.HTTP_409_CONFLICT)
    owner_id = None
    if body.application_owner_id:
        owner = await db().users.find_one({"_id": ObjectId(body.application_owner_id),
                                           "role": "application_owner"}) \
            if ObjectId.is_valid(body.application_owner_id) else None
        if not owner:
            raise error("validation_failed", "The invited person must be an application owner.",
                        status.HTTP_422_UNPROCESSABLE_CONTENT)
        owner_id = owner["_id"]
    try:
        details, warnings = catalog.validate(body.connector_type, body.details, tenant["name"])
    except catalog.DetailsError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail={
            "code": "validation_failed", "message": str(exc), "errors": exc.errors}) from None
    # FR-103: a capability with a warning (provisioning writes to the directory) needs the IAM engineer's acceptance.
    caps = {c["id"]: c for c in connector.get("capabilities") or []}
    needing = [c for c in details.get("capabilities") or [] if caps.get(c, {}).get("warning")]
    missing = [c for c in needing if c not in body.accept_warnings]
    if missing:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail={
            "code": "validation_failed", "message": "Accept the warning to continue.",
            "errors": {"warnings_accepted": f"accept the {caps[missing[0]]['label'].lower()} warning"}})
    if needing:
        now = datetime.now(UTC).isoformat(timespec="seconds")  # details go into the agent payload as JSON
        details["warnings_accepted"] = [{"capability": c, "user_id": str(user.id), "at": now} for c in needing]
    application = details.get("source_name") or connector["name"]
    title = f"{application} → {tenant['name']}"
    session = await repo.create(title=title, connector_type=body.connector_type, tenant_id=tenant["_id"],
                                details=details, iam_engineer_id=user.id, application_owner_id=owner_id,
                                plan=plans.seed(catalog.plan_template(body.connector_type), details))
    for c in needing:
        await audit.record("provisioning_warning_accepted", user.id, target=str(session["_id"]),
                           detail={"capability": c})
    return await full(session) | ({"warnings": warnings} if warnings else {})


@router.get("/{session_id}")
async def get_session(session_id: str, user: CurrentUser = Depends(current_user)) -> dict:  # noqa: B008
    session, role = await participant_session(session_id, user)
    out = await full(session)
    if role != "iam_engineer":
        out.pop("proof", None)  # SailPoint results: the IAM engineer passes them on (spec 002 R18)
    return out


@router.post("/{session_id}/finish")
async def finish_session(session_id: str,
                         user: CurrentUser = Depends(require_role("iam_engineer"))) -> dict:  # noqa: B008
    session, role = await participant_session(session_id, user)
    if role != "iam_engineer":
        raise error("forbidden_role", "Only the session's IAM engineer can finish it.", status.HTTP_403_FORBIDDEN)
    await repo.finish(session["_id"])
    await secrets.discard_on_finish(session)
    await events.emit(session["_id"], "session.updated", {"status": "finished"})
    return await full(await repo.get(session["_id"]))  # type: ignore[arg-type]
