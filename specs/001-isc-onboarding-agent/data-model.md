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
| `status` | `"open"` \| `"finished"` | |
| `finished_at`, `expires_at` | Date? | `expires_at = finished_at + 90 days`; TTL (research R10) |
| `created_at` | Date | |

Indexes: `{iam_engineer_id: 1, status: 1}`, `{application_owner_id: 1, status: 1}`, TTL on `expires_at`.

**State transitions (`steps.*.state`)**: `not_started → in_progress → passed | failed`; `failed → in_progress` on a
rerun; `passed → in_progress` only when the agent reruns that check after a fix. Only the agent's `set_step` tool and
the API's handling of ISC task results change step states, never the browser.

## messages

| Field | Type | Rules |
|---|---|---|
| `session_id` | ObjectId → sessions | |
| `seq` | int | strictly increasing per session; the queue order (FR-006a) |
| `thread` | `"iam_engineer"` \| `"application_owner"` | whose thread it belongs to (FR-006); a participant's message is always in their own thread, set by the API from the caller's role |
| `kind` | `"message"` \| `"relay_note"` | a relay note is the agent's one-line note about what it asked or passed on in the other thread (FR-006c) |
| `relay_ref` | ObjectId? → messages | on a relay note: the message it refers to in the other thread, when there is one |
| `relayed_from` | `"iam_engineer"` \| `"application_owner"`? | on an agent message that passes on what the other participant said (edge case "tell the other person") |
| `speaker` | `"iam_engineer"` \| `"application_owner"` \| `"agent"` | a participant only ever speaks in their own thread |
| `speaker_user_id` | ObjectId? | null for the agent |
| `addressed_to` | — | **retired** (research R18): migrated into `thread` and no longer written |
| `text` | string | **after masking** (FR-026); max 8,000 chars |
| `masked` | bool | true when the masker replaced anything |
| `attachment_ids` | ObjectId[] → attachments | |
| `queue_state` | `"queued"` \| `"processing"` \| `"answered"` | participant messages only |
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
| `visible_to` | `"both"` \| user id | `held` attachment events go to the uploader only |
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
| `request_summary` | object | masked field names/values the agent sent (no credentials) |
| `result` | `"ok"` \| `"failed"` | |
| `error` | string? | masked ISC error text |
| `task_ids` | string[] | ISC task / aggregation ids |
| `at` | Date | |

Index: `{session_id: 1, at: 1}`.

## audit (sign-ins and admin actions; no TTL)

`{at, actor_id?, username_tried?, kind: "sign_in" | "sign_in_failed" | "locked" | "user_created" | "user_disabled" |
"password_reset" | "role_changed" | "tenant_added" | "credential_replaced", target?, detail?}`.

## Validation rules carried from the spec

- Only `role = iam_engineer` may create sessions or be the ordering user of an `actions` record (FR-016, FR-019).
- A session has exactly one IAM engineer and at most one application owner.
- `connector_type` must reference an `available` catalog entry at session creation.
- `source` is set only by the agent's `create_source` result. `delete_session_source` may target only `source.id`.
- All text fields reaching `messages`, `events`, `actions.error` and logs pass through the masker first.

### Thread rules (FR-006–FR-006c, FR-016a, SC-010)

- Writes: `POST /sessions/{id}/messages` always lands in the caller's thread; there is no way to post in the other
  thread (view-only is enforced by the API, not only hidden in the browser).
- Every agent message in the other thread created by `post_to_other_thread` has a matching `relay_note` in the
  writer's thread in the same turn; the API rejects (and logs) a turn output that breaks this, posting a generic
  relay note instead.
- An application owner's turn may produce actions only through the three check tools and only while
  `check_order` is set; those actions carry `ordered_by = check_order` and `trigger = "application_owner_confirmation"`.
