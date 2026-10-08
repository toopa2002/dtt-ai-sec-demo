# Contract: live events (session API → browser, SSE)

`GET /onboarding/api/sessions/{id}/events`, `Content-Type: text/event-stream`. One stream per viewer. Each event:

```
id: <event_id, per-session increasing>
event: <type>
data: <JSON payload, already masked>
```

The first frame on every stream is a padding comment with `retry: 3000`, so proxies pass the response on at once.
Lines end in LF only (ngrok mangles CRLF event streams). On reconnect the browser sends `Last-Event-ID`, and the API replays every later event visible to that viewer from
the `events` collection, then continues live (research R7). The padding is 4 KB of spaces, followed by a `ping` event; the stream sends `ping` (no id, data `{}`) every 15 s. A page whose
stream has delivered no event for 35 s, or none at all after 5 s, falls back to long-polling until the stream delivers
again: `GET /sessions/{id}/events/poll?after=N` returns `{"events": [{"id", "type", "data"}]}`, the events after N visible
to the caller, waiting up to 25 s for the first one. TLS-inspecting corporate proxies can hold an event stream back while
the request itself succeeds; a plain JSON response gets through, at a few requests a minute (the tunnel's free plan
answers 429 to a request storm, so the page backs off 30 s on 429).

| Type | Payload | Visible to | Requirement |
|---|---|---|---|
| `message.created` | `Message` (see OpenAPI) — a participant message **together with its agent reply** (`reply_to`, `reply_state: received`, `ahead`, `status_text`; two events, same write), an agent message / relay note posted into a thread by a tool, or a `system_note`; always carries `thread` and `kind` | both | FR-006, FR-006c, FR-006h |
| `message.queue` | `{message_id, queue_state}` — `queued` → `processing` → `answered`; internal, the browser shows the reply's status instead | both | FR-006a |
| `reply.status` | `{message_id, reply_state, ahead?, status_text?}` — `received` (with `ahead`), `working` (status follows the progress line), `failed` (with the error reply text); `answered` comes with `agent.message` | both | FR-006h, SC-014 |
| `agent.delta` | `{turn_id, message_id, thread, text_delta}` — streamed reply text into the reply message created with the participant's message (`message_id` = that reply), always in the writer's thread | both | FR-006b |
| `agent.progress` | `{turn_id, thread, text}` e.g. "checking the source in SailPoint…", shown in the writer's thread; `{turn_id, thread, text: null}` clears it | both | FR-006b |
| `agent.message` | final `Message` for the turn (replaces the deltas) | both | FR-006 |
| `step.changed` | `{step, state, changed_at}` — milestones, derived from the plan | both | FR-008, FR-008c |
| `plan.updated` | `{plan: PlanStep[], done, total, next_step_id}` — the whole plan after any change | both | FR-008a-c, SC-015 |
| `action.recorded` | `Action` (see OpenAPI), including `outcome` | **IAM engineer only** | FR-020, FR-020a |
| `action.updated` | `Action` — a `running` action finished (same id) | **IAM engineer only** | FR-020a |
| `attachment.checked` | `{attachment_id, secret_check: passed}` | both | FR-026a |
| `attachment.held` | `{attachment_id, reason}` | **uploader only** | FR-026a |
| `participant.presence` | `{role, online}` | both | US3 edge case: offline participant |
| `thread.waiting` | `{waiting_on: role \| null, reason: string \| null}` — information only; the browser shows the waiting banner in both threads, worded per viewer (research R20) | both | FR-006a, FR-006g, US3 #4, #9 |
| `suggestions.updated` | `{thread, items: Suggestion[], for_event_id}` | **that thread's participant only** | FR-006e, SC-011 |
| `session.updated` | `{source?, status?, reopened_at?}` — includes finish and admin reopen | both | FR-031, FR-032 |
| `participant.changed` | `{place: role, user: {id, display_name}}` — an admin handover took effect | both (old and new participant) | FR-033 |
| `access.revoked` | `{session_id}` — sent to the previous participant only; the stream then closes and the browser leaves the session | **previous participant only** | FR-033, SC-016 |
| `turn.failed` | `{turn_id, reason}` — agent or AgentCore error; the message returns to `queued` once, then `answered` with an error reply | both | edge cases |

Ordering guarantee: events of one session are delivered in `event_id` order. Within a turn the order is
`message.queue(processing)` → `reply.status(working)` → `reply.status(received)` for later replies whose `ahead`
changed → (`agent.progress` | `agent.delta` | `plan.updated` | `step.changed` | `action.recorded` | `action.updated`)* →
`message.created` (other-thread message and relay note, if any) → `agent.message` → `thread.waiting` →
`message.queue(answered)` → `suggestions.updated` (each participant their own).

The browser shows both threads from the same stream by filtering on `thread`; only the viewer's own thread has a
message box and suggestions. The application owner's screen keeps the IAM engineer's thread collapsed until opened,
but still applies its events (research R25). A pending handover is applied after `message.queue(answered)` and
`suggestions.updated`, followed by `message.created` (system notes), `participant.changed` and `access.revoked`.
