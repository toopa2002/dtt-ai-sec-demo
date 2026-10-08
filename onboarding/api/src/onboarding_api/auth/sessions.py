"""Server-side browser sessions (research R8): random 256-bit cookie, only its SHA-256 is stored, 30-minute idle."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from bson import ObjectId

from ..config import settings
from ..db import db

COOKIE = "ob_session"


def _key(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def create(user_id: ObjectId) -> tuple[str, str]:
    """Return (cookie value, csrf token)."""
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(24)
    now = datetime.now(UTC)
    await db().auth_sessions.insert_one({
        "_id": _key(token),
        "user_id": user_id,
        "csrf": csrf,
        "created_at": now,
        "last_seen": now,
        "expires_at": now + timedelta(minutes=settings().idle_minutes),
    })
    return token, csrf


async def touch(token: str) -> dict | None:
    """Return the live session and slide its idle expiry, or None when missing or idle too long."""
    now = datetime.now(UTC)
    doc = await db().auth_sessions.find_one_and_update(
        {"_id": _key(token), "expires_at": {"$gt": now}},
        {"$set": {"last_seen": now, "expires_at": now + timedelta(minutes=settings().idle_minutes)}},
        return_document=True,
    )
    return doc


async def revoke(token: str) -> None:
    await db().auth_sessions.delete_one({"_id": _key(token)})


async def revoke_user(user_id: ObjectId) -> None:
    await db().auth_sessions.delete_many({"user_id": user_id})
