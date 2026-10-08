"""GET /sessions/{id}/events — Server-Sent Events (contracts/live-events.md, research R7)."""

import asyncio
import json
import time

from fastapi import APIRouter, Depends, Request
from sse_starlette.sse import EventSourceResponse

from ..auth.deps import CurrentUser, current_user
from ..metrics import record_metric
from ..sessions import repo
from . import events
from .access import participant_session

router = APIRouter(tags=["live"])
KEEPALIVE_SECONDS = 15
# A comment this long gets the stream past proxies that hold small responses back (TLS-inspecting corporate proxies).
PADDING = " " * 4096
PING = {"event": "ping", "data": "{}"}


def _frame(event: dict) -> dict:
    return {"id": str(event["event_id"]), "event": event["type"],
            "data": json.dumps(event["payload"], default=str)}


@router.get("/sessions/{session_id}/events")
async def stream(session_id: str, request: Request, user: CurrentUser = Depends(current_user)):  # noqa: B008
    session, role = await participant_session(session_id, user)
    sid = session["_id"]
    # Resume point: the EventSource Last-Event-ID header on its own reconnects, or ?after=N on a fresh connection
    # (browsers can't set the header on the first request; the page passes the event_seq it loaded history at).
    try:
        last = int(request.headers.get("last-event-id") or request.query_params.get("after") or 0)
    except ValueError:
        last = 0

    async def gen():  # type: ignore[no-untyped-def]
        queue = events.subscribe(sid)
        if events.presence_join(sid, role):
            await events.emit(sid, "participant.presence", {"role": role, "online": True})
        try:
            # First bytes right away: some proxies (ngrok) hold the response headers until the body starts, and the
            # browser only reports the stream open once they arrive. `retry` sets the browser's own reconnect delay.
            # The keepalive is a `ping` event, not a comment, so the page can tell a stream that delivers from one a
            # proxy holds back, and fall back to polling (contracts/live-events.md).
            yield {"comment": PADDING, "retry": 3000}
            yield PING
            sent = last
            for event in await events.replay(sid, last):
                if events.visible(event, user.id, role):
                    sent = event["event_id"]
                    yield _frame(event)
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=KEEPALIVE_SECONDS)
                except TimeoutError:
                    yield PING
                    continue
                if event["event_id"] <= sent or not events.visible(event, user.id, role):
                    continue
                sent = event["event_id"]
                record_metric("relay_ms", (time.time() - event["created_at"].timestamp()) * 1000)
                yield _frame(event)
                if event["type"] in ("participant.changed", "access.revoked") and not await _still_in(sid, user.id):
                    break  # handed over (FR-033): the previous participant's stream ends here
        finally:
            events.unsubscribe(sid, queue)
            if events.presence_leave(sid, role):
                await events.emit(sid, "participant.presence", {"role": role, "online": False})

    # LF line endings: ngrok re-parses event streams and, with the default CRLF, merges events and drops fields.
    return EventSourceResponse(gen(), ping=None, sep="\n",
                               headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"})


POLL_WAIT_SECONDS = 25


async def _still_in(session_id, user_id) -> bool:  # type: ignore[no-untyped-def]
    session = await repo.get(session_id)
    return bool(session) and repo.participant_role(session, user_id) is not None


@router.get("/sessions/{session_id}/events/poll")
async def poll(session_id: str, after: int = 0, user: CurrentUser = Depends(current_user)):  # noqa: B008
    """Long-poll fallback for networks whose proxy holds the event stream back: the events after `after` visible to
    this viewer, waiting up to 25 s for the first one. A plain JSON response, so buffering proxies pass it on."""
    session, role = await participant_session(session_id, user)
    sid = session["_id"]
    queue = events.subscribe(sid)
    try:
        deadline = time.monotonic() + POLL_WAIT_SECONDS
        while True:
            found = [e for e in await events.replay(sid, after) if events.visible(e, user.id, role)]
            left = deadline - time.monotonic()
            if found or left <= 0:
                break
            try:
                await asyncio.wait_for(queue.get(), timeout=left)
            except TimeoutError:
                pass
    finally:
        events.unsubscribe(sid, queue)
    return {"events": [json.loads(json.dumps({"id": e["event_id"], "type": e["type"], "data": e["payload"]},
                                             default=str)) for e in found]}
