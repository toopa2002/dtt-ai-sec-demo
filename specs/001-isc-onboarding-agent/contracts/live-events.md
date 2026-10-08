# Contract: live events (session API → browser, SSE)

`GET /onboarding/api/sessions/{id}/events`, `Content-Type: text/event-stream`. One stream per viewer. Each event:

```
id: <event_id, per-session increasing>
event: <type>
data: <JSON payload, already masked>
```

The first frame on every stream is `: connected` with `retry: 3000`, so proxies pass the response on at once.
Lines end in LF only (ngrok mangles CRLF event streams). On reconnect the browser sends `Last-Event-ID`, and the API replays every later event visible to that viewer from
the `events` collection, then continues live (research R7). A `: keepalive` comment is sent every 15 s.

| Type | Payload | Visible to | Requirement |
|---|---|---|---|
| `message.created` | `Message` (see OpenAPI) — a participant message (with `queue_state`), or an agent message / relay note posted into a thread by a tool; always carries `thread` and `kind` | both | FR-006, FR-006c |
| `message.queue` | `{message_id, queue_state}` — `queued` → `processing` → `answered` | both | FR-006a |
| `agent.delta` | `{turn_id, message_id, thread, text_delta}` — streamed reply text, always in the writer's thread | both | FR-006b |
| `agent.progress` | `{turn_id, thread, text}` e.g. "checking the source in SailPoint…", shown in the writer's thread; `{turn_id, thread, text: null}` clears it | both | FR-006b |
| `agent.message` | final `Message` for the turn (replaces the deltas) | both | FR-006 |
| `step.changed` | `{step, state, changed_at}` | both | FR-008 |
| `action.recorded` | `Action` (see OpenAPI) | both | FR-020 |
| `attachment.checked` | `{attachment_id, secret_check: passed}` | both | FR-026a |
| `attachment.held` | `{attachment_id, reason}` | **uploader only** | FR-026a |
| `participant.presence` | `{role, online}` | both | US3 edge case: offline participant |
| `thread.waiting` | `{waiting_on: role \| null, reason: string \| null}` — information only; the browser shows the waiting banner in both threads, worded per viewer (research R20) | both | FR-006a, FR-006g, US3 #4, #9 |
| `suggestions.updated` | `{thread, items: Suggestion[], for_event_id}` | **that thread's participant only** | FR-006e, SC-011 |
| `session.updated` | `{source?, status?}` | both | |
| `turn.failed` | `{turn_id, reason}` — agent or AgentCore error; the message returns to `queued` once, then `answered` with an error reply | both | edge cases |

Ordering guarantee: events of one session are delivered in `event_id` order. Within a turn the order is
`message.queue(processing)` → (`agent.progress` | `agent.delta` | `step.changed` | `action.recorded`)* →
`message.created` (other-thread message and relay note, if any) → `agent.message` → `thread.waiting` →
`message.queue(answered)` → `suggestions.updated` (each participant their own).

The browser shows both threads from the same stream by filtering on `thread`; only the viewer's own thread has a
message box and suggestions.
