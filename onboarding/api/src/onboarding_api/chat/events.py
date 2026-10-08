"""Event journal + in-process broadcaster (research R7, contracts/live-events.md).

Every event is written to `events` first (per-session increasing event_id), then pushed to the open SSE streams of
that session. Reconnecting viewers replay from the journal with Last-Event-ID.
"""

import asyncio
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from bson import ObjectId

from ..db import db
from ..masking import mask_obj
from ..sessions import repo

_subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)
_presence: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))


async def emit(session_id: ObjectId, type_: str, payload: dict[str, Any], visible_to: str = "both") -> dict:
    event_id = await repo.next_counter(session_id, "event_seq")
    doc = {
        "session_id": session_id,
        "event_id": event_id,
        "type": type_,
        "payload": mask_obj(payload),
        "visible_to": visible_to,
        "created_at": datetime.now(UTC),
        "expires_at": None,
    }
    await db().events.insert_one(doc)
    for queue in list(_subscribers[str(session_id)]):
        queue.put_nowait(doc)
    return doc


def subscribe(session_id: ObjectId) -> asyncio.Queue:
    queue: asyncio.Queue = asyncio.Queue()
    _subscribers[str(session_id)].add(queue)
    return queue


def unsubscribe(session_id: ObjectId, queue: asyncio.Queue) -> None:
    _subscribers[str(session_id)].discard(queue)


async def replay(session_id: ObjectId, after: int) -> list[dict]:
    return [e async for e in db().events.find({"session_id": session_id, "event_id": {"$gt": after}},
                                               sort=[("event_id", 1)])]


def visible(event: dict, user_id: ObjectId, role: str | None = None) -> bool:
    """`both`, a role (`role:iam_engineer`: whoever holds that place now, so it survives a handover, research R24),
    or one user id (a held screenshot's uploader, an `access.revoked` notice)."""
    to = event["visible_to"]
    return to == "both" or to == str(user_id) or (role is not None and to == f"role:{role}")


def for_role(role: str) -> str:
    return f"role:{role}"


# Presence: count of open streams per role in this API process (single replica, research R7).
def presence_join(session_id: ObjectId, role: str) -> bool:
    counts = _presence[str(session_id)]
    counts[role] += 1
    return counts[role] == 1


def presence_leave(session_id: ObjectId, role: str) -> bool:
    counts = _presence[str(session_id)]
    counts[role] = max(0, counts[role] - 1)
    return counts[role] == 0


def online(session_id: ObjectId, role: str) -> bool:
    return _presence[str(session_id)][role] > 0
