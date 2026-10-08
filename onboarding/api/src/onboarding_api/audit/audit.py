"""Sign-in and admin audit (data-model.md `audit`; no TTL)."""

from datetime import UTC, datetime
from typing import Literal

from bson import ObjectId

from ..db import db
from ..masking import mask_obj

Kind = Literal[
    "sign_in", "sign_in_failed", "locked", "user_created", "user_disabled", "password_reset", "role_changed",
    "tenant_added", "credential_replaced",
]


async def record(kind: Kind, actor_id: ObjectId | None = None, *, username_tried: str | None = None,
                 target: str | None = None, detail: dict | None = None) -> None:
    await db().audit.insert_one({
        "at": datetime.now(UTC),
        "kind": kind,
        "actor_id": actor_id,
        "username_tried": username_tried,
        "target": target,
        "detail": mask_obj(detail or {}),
    })
