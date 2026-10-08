"""GET /sessions/{id}/events — Server-Sent Events (contracts/live-events.md, research R7)."""

import asyncio
import json
import time

from fastapi import APIRouter, Depends, Request
from sse_starlette.sse import EventSourceResponse

from ..auth.deps import CurrentUser, current_user
from ..metrics import record_metric
from . import events
from .access import participant_session

router = APIRouter(tags=["live"])
KEEPALIVE_SECONDS = 15


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
            yield {"comment": "connected", "retry": 3000}
            sent = last
            for event in await events.replay(sid, last):
                if events.visible(event, user.id):
                    sent = event["event_id"]
                    yield _frame(event)
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=KEEPALIVE_SECONDS)
                except TimeoutError:
                    yield {"comment": "keepalive"}
                    continue
                if event["event_id"] <= sent or not events.visible(event, user.id):
                    continue
                sent = event["event_id"]
                record_metric("relay_ms", (time.time() - event["created_at"].timestamp()) * 1000)
                yield _frame(event)
        finally:
            events.unsubscribe(sid, queue)
            if events.presence_leave(sid, role):
                await events.emit(sid, "participant.presence", {"role": role, "online": False})

    # LF line endings: ngrok re-parses event streams and, with the default CRLF, merges events and drops fields.
    return EventSourceResponse(gen(), ping=None, sep="\n",
                               headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"})
