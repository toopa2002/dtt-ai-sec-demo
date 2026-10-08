"""Local accounts (data-model.md `users`, FR-001, FR-002)."""

import re
from datetime import UTC, datetime
from typing import Any, Literal

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from bson import ObjectId

from ..db import db

Role = Literal["iam_engineer", "application_owner"]
ROLES: tuple[str, ...] = ("iam_engineer", "application_owner")
USERNAME_RE = re.compile(r"^[a-z0-9._-]{3,64}$")
MIN_PASSWORD = 12

_hasher = PasswordHasher()  # argon2id defaults


class UserError(ValueError):
    """Validation failure; message is safe to show to an admin."""


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def validate(username: str, display_name: str, role: str, is_admin: bool) -> None:
    if not USERNAME_RE.match(username):
        raise UserError("username must be 3–64 characters: lowercase letters, digits, '.', '_' or '-'")
    if not 1 <= len(display_name) <= 100:
        raise UserError("display name must be 1–100 characters")
    if role not in ROLES:
        raise UserError("role must be iam_engineer or application_owner")
    if is_admin and role != "iam_engineer":
        raise UserError("only IAM engineers can be admins")


def validate_password(password: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise UserError(f"password must be at least {MIN_PASSWORD} characters")


async def create_user(username: str, display_name: str, role: str, password: str, is_admin: bool = False) -> dict:
    validate(username, display_name, role, is_admin)
    validate_password(password)
    now = datetime.now(UTC)
    doc = {
        "username": username,
        "display_name": display_name,
        "role": role,
        "is_admin": bool(is_admin),
        "password_hash": hash_password(password),
        "status": "active",
        "failed_attempts": 0,
        "locked_until": None,
        "last_sign_in": None,
        "created_at": now,
        "updated_at": now,
    }
    if await db().users.find_one({"username": username}):
        raise UserError("that username is taken")
    result = await db().users.insert_one(doc)
    doc["_id"] = result.inserted_id
    return doc


async def get_user(user_id: ObjectId | str) -> dict | None:
    return await db().users.find_one({"_id": ObjectId(user_id)})


async def get_by_username(username: str) -> dict | None:
    return await db().users.find_one({"username": username})


async def list_users() -> list[dict]:
    return [u async for u in db().users.find({}, sort=[("username", 1)])]


async def update_user(user_id: ObjectId, changes: dict[str, Any]) -> dict:
    user = await get_user(user_id)
    if not user:
        raise UserError("no such user")
    role = changes.get("role", user["role"])
    is_admin = changes.get("is_admin", user["is_admin"] if role == "iam_engineer" else False)
    validate(user["username"], user["display_name"], role, is_admin)
    update: dict[str, Any] = {"role": role, "is_admin": is_admin, "updated_at": datetime.now(UTC)}
    if "status" in changes:
        if changes["status"] not in ("active", "disabled"):
            raise UserError("status must be active or disabled")
        update["status"] = changes["status"]
    if changes.get("unlock"):
        update.update(status="active" if update.get("status", user["status"]) != "disabled" else "disabled",
                      failed_attempts=0, locked_until=None)
    if changes.get("new_password"):
        validate_password(changes["new_password"])
        update["password_hash"] = hash_password(changes["new_password"])
    await db().users.update_one({"_id": user["_id"]}, {"$set": update})
    return await get_user(user["_id"])  # type: ignore[return-value]


def public(user: dict) -> dict:
    """Account fields safe for the browser (never the hash)."""
    return {
        "id": str(user["_id"]),
        "username": user["username"],
        "display_name": user["display_name"],
        "role": user["role"],
        "is_admin": user["is_admin"],
        "status": effective_status(user),
        "locked_until": user.get("locked_until"),
        "last_sign_in": user.get("last_sign_in"),
    }


def effective_status(user: dict) -> str:
    locked_until = user.get("locked_until")
    if user["status"] == "disabled":
        return "disabled"
    if locked_until and locked_until > datetime.now(UTC):
        return "locked"
    return "active"
