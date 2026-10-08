"""/admin/users — account management for admin IAM engineers (FR-002)."""

from bson import ObjectId
from fastapi import APIRouter, Depends, status
from pydantic import BaseModel

from ..audit import audit
from . import sessions, users
from .deps import CurrentUser, error, require_admin

router = APIRouter(prefix="/admin/users", tags=["admin"])


class UserIn(BaseModel):
    username: str
    display_name: str
    role: users.Role
    is_admin: bool = False
    initial_password: str


class UserPatch(BaseModel):
    status: str | None = None
    unlock: bool | None = None
    new_password: str | None = None
    role: users.Role | None = None
    is_admin: bool | None = None


@router.get("")
async def list_users(_: CurrentUser = Depends(require_admin)) -> list[dict]:  # noqa: B008
    return [users.public(u) for u in await users.list_users()]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create(body: UserIn, admin: CurrentUser = Depends(require_admin)) -> dict:  # noqa: B008
    try:
        user = await users.create_user(body.username.strip().lower(), body.display_name.strip(), body.role,
                                       body.initial_password, body.is_admin)
    except users.UserError as exc:
        raise error("validation_failed", str(exc), status.HTTP_422_UNPROCESSABLE_CONTENT) from None
    await audit.record("user_created", admin.id, target=user["username"],
                       detail={"role": user["role"], "is_admin": user["is_admin"]})
    return users.public(user)


@router.patch("/{user_id}")
async def update(user_id: str, body: UserPatch, admin: CurrentUser = Depends(require_admin)) -> dict:  # noqa: B008
    if not ObjectId.is_valid(user_id):
        raise error("not_found", "No such user.", status.HTTP_404_NOT_FOUND)
    changes = body.model_dump(exclude_none=True)
    if ObjectId(user_id) == admin.id and (changes.get("status") == "disabled" or changes.get("is_admin") is False
                                          or changes.get("role") == "application_owner"):
        raise error("validation_failed", "You can't remove your own admin access.", status.HTTP_422_UNPROCESSABLE_CONTENT)
    before = await users.get_user(user_id)
    try:
        user = await users.update_user(ObjectId(user_id), changes)
    except users.UserError as exc:
        raise error("validation_failed", str(exc), status.HTTP_422_UNPROCESSABLE_CONTENT) from None
    if changes.get("status") == "disabled":
        await sessions.revoke_user(user["_id"])
        await audit.record("user_disabled", admin.id, target=user["username"])
    if changes.get("new_password"):
        await sessions.revoke_user(user["_id"])
        await audit.record("password_reset", admin.id, target=user["username"])
    if before and (before["role"] != user["role"] or before["is_admin"] != user["is_admin"]):
        await audit.record("role_changed", admin.id, target=user["username"],
                           detail={"role": user["role"], "is_admin": user["is_admin"]})
    return users.public(user)
