# Data Model: SailPoint ISC Application Onboarding Agent

MongoDB 8.0, database `onboarding`. Ids are `ObjectId` unless noted. All times are UTC `Date`. **No collection stores a
secret.** The SailPoint credential lives in AgentCore Identity (research R4); passwords are stored only as argon2id
hashes.

## users

| Field | Type | Rules |
|---|---|---|
| `username` | string | unique, 3–64 chars, `[a-z0-9._-]` |
| `display_name` | string | 1–100 chars |
| `role` | `"iam_engineer"` \| `"application_owner"` | required (FR-001) |
| `is_admin` | bool | may be `true` only when `role = iam_engineer` (FR-002) |
| `password_hash` | string | argon2id |
| `status` | `"active"` \| `"locked"` \| `"disabled"` | |
| `failed_attempts` | int | reset on success |
| `locked_until` | Date? | set to now + 15 min on the 5th consecutive failure (FR-004) |
| `created_at`, `updated_at` | Date | |

Indexes: `{username: 1}` unique.

## auth_sessions (browser sign-ins)

| Field | Type | Rules |
|---|---|---|
| `_id` | string | random 256-bit, also the cookie value (only its SHA-256 is stored, as `_id`) |
| `user_id` | ObjectId → users | |
| `last_seen` | Date | sliding; expired after 30 min idle (FR-004) |
| `expires_at` | Date | TTL index `expireAfterSeconds: 0` |

## tenants

| Field | Type | Rules |
|---|---|---|
| `name` | string | unique display name, e.g. `acme-demo` |
| `api_host` | string | full ISC API host, e.g. `acme-demo.api.identitynow-demo.com` (the skill's `ISC_TENANT` rule) |
| `credential_provider` | string | AgentCore Identity provider name, e.g. `onboarding-isc-acme-demo` |
| `credential_hint` | string | last 4 chars of the client id only, for display |
| `external_id` | string? | looked up from `GET /beta/tenant`; not a secret (FR-027) |
| `status` | `"usable"` \| `"credential_rejected"` \| `"unchecked"` | set by "Check now" and by agent 401s |
| `last_checked_at` | Date? | |
| `created_by` | ObjectId → users (admin) | |

## connector catalog (not a collection)

Shipped as `onboarding/catalog/catalog.yaml` plus playbooks, and read-only at run time (clarification:
fixed per release). Each entry: `id`, `name`, `status` (`available` | `planned`), `description`, `owner_asks`,
`agent_configures`, `session_fields[]` (name, label, type, required, validation), `playbook` (path). Shape:
[contracts/connector-playbook.md](contracts/connector-playbook.md).

## sessions (onboarding sessions)

| Field | Type | Rules |
|---|---|---|
| `title` | string | e.g. `Acme Org (AWS) → acme-demo` |
| `connector_type` | string | catalog id with `status = available` (FR-005, US5 #3) |
| `tenant_id` | ObjectId → tenants | tenant must be `usable` to start turns |
| `details` | object | validated against the type's `session_fields`; AWS SaaS: `source_name`, `source_owner`, `management_account_id`, `accounts[]`, `region`, `bedrock_regions[]`, `agentcore_regions[]`, `role_name` (default `SailPointISCRole-<tenant>`) |
| `iam_engineer_id` | ObjectId → users | creator; role `iam_engineer` |
| `application_owner_id` | ObjectId? → users | invited; role `application_owner` |
| `steps` | object | one entry per step: `{state, changed_at}`; steps `application_ready`, `source_created`, `configured`, `connection_check`, `aggregation`, `test_connection`; state ∈ `not_started` \| `in_progress` \| `passed` \| `failed` (FR-008) |
| `source` | object? | `{id, name}` of the ISC source created **in this session** (FR-018) |
| `turn_lock` | object? | `{turn_id, since}` — one turn at a time per session (FR-006a) |
| `waiting_on` | `"iam_engineer"` \| `"application_owner"` \| null | who the agent waits for; information only, never blocks the queue (FR-006a, research R15) |
| `waiting_reason` | string? | the awaited person's next step for the waiting banner; one line, ≤ 120 chars, masked; null with `waiting_on` (FR-006g, research R20) |
| `check_order` | object? | `{user_id, display_name, turn_id, at}` — the IAM engineer's standing order to run the checks; lets an application owner's confirmation rerun them (FR-016a, research R16); cleared when all checks pass or the IAM engineer orders something new |
| `suggestions` | object | `{iam_engineer: Suggestion[], application_owner: Suggestion[]}`, each list 3–5 items, plus `for_event_id` (FR-006e, research R17) |
| `plan` | PlanStep[] | ordered, ≤ 40; seeded from `playbooks/<id>/plan.yaml` at creation; changed only by the agent's `update_plan` and by milestone `set_step` events (FR-008a-c, research R22) |
| `status` | `"open"` \| `"finished"` | open → finished by the IAM engineer (FR-031); finished → open only by an admin reopen (FR-032) |
| `finished_at`, `expires_at` | Date? | `expires_at = finished_at + 90 days`; TTL (research R10); both cleared on reopen |
| `reopened_at` | Date? | last admin reopen (FR-032) |
| `handovers` | object[] | `{place, from_user_id, to_user_id, admin_id, at}`, appended per handover (FR-033) |
| `pending_handover` | object? | `{place, to_user_id, admin_id, requested_at}` while a turn holds `turn_lock`; applied when the turn ends (research R23) |
| `created_at` | Date | |

Indexes: `{iam_engineer_id: 1, status: 1}`, `{application_owner_id: 1, status: 1}`, TTL on `expires_at`.

**State transitions (`steps.*.state`)**: `not_started → in_progress → passed | failed`; `failed → in_progress` on a
rerun; `passed → in_progress` only when the agent reruns that check after a fix. Since the plan (R22) the API **derives** `steps.*` from the plan
steps tied to each milestone (any failed → `failed`; all done or skipped → `passed`; any done or in progress →
`in_progress`; else `not_started`), so the header always follows the plan (FR-008c). The browser never changes either.

**PlanStep** (embedded in `sessions.plan`):

| Field | Type | Rules |
|---|---|---|
| `id` | string | playbook id (`check_org`, `create_source`…) or `x1`, `x2`… for steps the agent adds |
| `title` | string | ≤ 120 chars, masked |
| `actor` | `"application_owner"` \| `"iam_engineer"` \| `"agent"` | who does it |
| `kind` | `"read_only"` \| `"change"` | |
| `state` | `"todo"` \| `"in_progress"` \| `"done"` \| `"failed"` \| `"skipped"` \| `"blocked"` | |
| `reason` | string? | ≤ 160 chars, masked; required for added, skipped, blocked and failed steps and for leaving `done` |
| `milestone` | step key? | which FR-008 milestone it counts towards |
| `added_by` | `"playbook"` \| `"agent"` | |
| `instruction`, `expected` | string? | application-owner steps: keys into the playbook's `setup.md` |
| `changed_at` | Date | |

Rules: steps are never removed; order changes only by `add` (inserted after a named step). Count shown = steps `done`
of steps not `skipped`; next step = first step not `done`/`skipped`.

## messages

| Field | Type | Rules |
|---|---|---|
| `session_id` | ObjectId → sessions | |
| `seq` | int | strictly increasing per session; the queue order (FR-006a) |
| `thread` | `"iam_engineer"` \| `"application_owner"` | whose thread it belongs to (FR-006); a participant's message is always in their own thread, set by the API from the caller's role |
| `kind` | `"message"` \| `"relay_note"` \| `"system_note"` | a relay note is the agent's one-line note about what it asked or passed on in the other thread (FR-006c); a system note is the API's line about a reopen or handover (FR-033) |
| `reply_to` | ObjectId? → messages | on an agent reply: the participant message it answers; created with that message (FR-006h, research R21) |
| `reply_state` | `"received"` \| `"working"` \| `"answered"` \| `"failed"`? | agent replies only; `received → working → answered \| failed` |
| `ahead` | int? | while `received`: messages before it in the session queue |
| `status_text` | string? | the system-written status shown until the answer streams in; cleared when `answered` |
| `relay_ref` | ObjectId? → messages | on a relay note: the message it refers to in the other thread, when there is one |
| `relayed_from` | `"iam_engineer"` \| `"application_owner"`? | on an agent message that passes on what the other participant said (edge case "tell the other person") |
| `speaker` | `"iam_engineer"` \| `"application_owner"` \| `"agent"` | a participant only ever speaks in their own thread |
| `speaker_user_id` | ObjectId? | null for the agent |
| `addressed_to` | — | **retired** (research R18): migrated into `thread` and no longer written |
| `text` | string | **after masking** (FR-026); max 8,000 chars |
| `masked` | bool | true when the masker replaced anything |
| `attachment_ids` | ObjectId[] → attachments | |
| `queue_state` | `"queued"` \| `"processing"` \| `"answered"` | participant messages only; internal queue state, no longer shown (the reply's `reply_state` is) |
| `turn_id` | string? | links an agent reply to the turn that produced it |
| `created_at`, `expires_at` | Date | TTL via the session's `expires_at` (copied at finish) |

Indexes: `{session_id: 1, seq: 1}` unique (`seq` stays session-wide: it is the one queue), `{session_id: 1, thread: 1,
seq: 1}`, TTL on `expires_at`.

**Suggestion** (embedded in `sessions.suggestions`, not a collection; never stored as a message unless sent):

| Field | Type | Rules |
|---|---|---|
| `text` | string | ≤ 200 chars; passes the masker unchanged (no secrets); may end with a colon as a starter ("Here is the output:") |
| `kind` | `"answer"` \| `"order"` \| `"question"` | `order` never in the application owner's list (FR-006e, FR-019) |
| `source` | `"agent"` \| `"default"` | agent items first, topped up from `playbooks/<id>/suggestions.yaml` to at least 3 |

## attachments

GridFS bucket `screenshots` (file data) plus this metadata collection.

| Field | Type | Rules |
|---|---|---|
| `session_id`, `uploader_id` | ObjectId | |
| `gridfs_id` | ObjectId? | **null while held** (a held image is never written to GridFS; FR-026a) |
| `content_type` | `image/png` \| `image/jpeg` \| `image/webp` | ≤ 10 MB (FR-007) |
| `secret_check` | `"pending"` \| `"passed"` \| `"held"` | |
| `created_at`, `expires_at` | Date | TTL |

Held image bytes stay only in API memory for the uploader's preview, up to 10 minutes, and are discarded when the
uploader replaces or dismisses the image.

## events (live stream journal)

| Field | Type | Rules |
|---|---|---|
| `session_id` | ObjectId | |
| `event_id` | int | per-session increasing; the SSE `id:` (research R7) |
| `type` | see [contracts/live-events.md](contracts/live-events.md) | |
| `payload` | object | already masked |
| `visible_to` | `"both"` \| `"role:iam_engineer"` \| `"role:application_owner"` \| user id | role form resolved against the current participant at delivery (survives handover, research R24): `action.*` and `suggestions.updated`; a user id only for `attachment.held` (uploader) and `access.revoked` (previous participant) |
| `created_at`, `expires_at` | Date | TTL |

Index: `{session_id: 1, event_id: 1}` unique.

## actions (SailPoint action records — FR-020, SC-006; no TTL)

| Field | Type | Rules |
|---|---|---|
| `session_id` | ObjectId | |
| `tenant_id` | ObjectId | |
| `source` | `{id, name}`? | |
| `action` | `"create_source"` \| `"configure_source"` \| `"connection_check"` \| `"aggregate"` \| `"test_connection"` \| `"delete_source"` | |
| `ordered_by` | ObjectId → users | **required**; the IAM engineer whose message started the turn, or the standing `check_order` for a check rerun after the application owner's confirmation (FR-016a) |
| `trigger` | `"order"` \| `"application_owner_confirmation"` | why the action ran; the second only for check reruns |
| `turn_id` | string | |
| `action_ref` | string | agent-made, unique within the turn; `(turn_id, action_ref)` is unique, so a `running` record is updated in place (research R24) |
| `order_message_id` | ObjectId? → messages | the participant message that started the turn ("Show in thread") |
| `request` | object | masked fields and values the agent sent (no credentials); records before R24 have `request_summary` instead |
| `result` | `"ok"` \| `"failed"` \| `"running"` | |
| `response` | object | `{task_ids[], task_states{id: state}, counts{accounts?, entitlements?}, error?}`, masked |
| `error` | string? | masked ISC error text (kept for old records; new ones also carry it in `response`) |
| `task_ids` | string[] | ISC task / aggregation ids |
| `diagnosis` | string? | the agent's diagnosis of a failure (`note_diagnosis`), ≤ 1,000 chars, masked |
| `outcome` | string | one line computed by the API: "passed · 3 accounts read", "failed · " + first error line, "running" (FR-020a) |
| `started_at`, `at` | Date | `at` = when it ended (or last update while running) |
| `duration_ms` | int? | |

Indexes: `{session_id: 1, at: 1}`, `{turn_id: 1, action_ref: 1}` unique (sparse).

Visible to the session's IAM engineer only (FR-020, research R24).

## audit (sign-ins and admin actions; no TTL)

`{at, actor_id?, username_tried?, kind: "sign_in" | "sign_in_failed" | "locked" | "user_created" | "user_disabled" |
"password_reset" | "role_changed" | "tenant_added" | "credential_replaced" | "session_reopened" | "session_handover",
target?, detail?}`. A handover's `detail` is `{session_id, place, from_user_id, to_user_id}` (FR-033).

## Validation rules carried from the spec

- Only `role = iam_engineer` may create sessions or be the ordering user of an `actions` record (FR-016, FR-019).
- A session has exactly one IAM engineer and at most one application owner at a time; only an admin handover changes
  either (FR-033), to an active user with that role who does not hold the other place.
- A finished session accepts no messages (409 `session_finished`) until an admin reopens it (FR-031, FR-032).
- Every participant message gets exactly one agent reply (`reply_to`) in the same write (FR-006h).
- `connector_type` must reference an `available` catalog entry at session creation.
- `source` is set only by the agent's `create_source` result. `delete_session_source` may target only `source.id`.
- All text fields reaching `messages`, `events`, `actions` (request, response, error, diagnosis), plan step titles and
  reasons, and logs pass through the masker first.

### Thread rules (FR-006–FR-006c, FR-016a, SC-010)

- Writes: `POST /sessions/{id}/messages` always lands in the caller's thread; there is no way to post in the other
  thread (view-only is enforced by the API, not only hidden in the browser).
- Every agent message in the other thread created by `post_to_other_thread` has a matching `relay_note` in the
  writer's thread in the same turn; the API rejects (and logs) a turn output that breaks this, posting a generic
  relay note instead.
- An application owner's turn may produce actions only through the three check tools and only while
  `check_order` is set; those actions carry `ordered_by = check_order` and `trigger = "application_owner_confirmation"`.
