# Contract: session API ↔ AgentCore runtime

Runtime `isc_onboarding_agent` (ap-southeast-1). Invoked only by the session API with
`bedrock-agentcore:InvokeAgentRuntime` (SigV4, IAM user `onboarding-api`, research R2).
`runtimeSessionId` = `onb-<session_id>` (an affinity hint only; the agent is stateless per turn, research R3).

## Request (one per queued participant message)

```json
{
  "mode": "turn",                       // or "secret_check" (screenshots, FR-026a), "tenant_check" (below)
  "turn_id": "t_01J…",
  "lang": "en",
  "ordered_by": { "user_id": "…", "role": "iam_engineer", "display_name": "…" },
  "session": {
    "id": "…",
    "connector_type": "aws-saas",
    "tenant": { "api_host": "acme-demo.api.identitynow-demo.com", "credential_provider": "onboarding-isc-acme-demo", "external_id": "…" },
    "details": { "source_name": "AWS - Acme Org", "management_account_id": "111122223333", "accounts": ["…"], "region": "ap-southeast-1", "role_name": "SailPointISCRole-acme-demo", "bedrock_regions": [], "agentcore_regions": ["ap-southeast-1"] },
    "steps": { "application_ready": "passed", "source_created": "not_started", "…": "…" },
    "source": null
  },
  "history": [ { "seq": 41, "thread": "application_owner", "kind": "message", "speaker": "agent", "text": "…" } ],
  "message": { "seq": 42, "thread": "iam_engineer", "speaker": "iam_engineer", "text": "Create the connector…" },
  "waiting_on": "application_owner",
  "waiting_reason": "run step 4 and paste the output",
  "check_order": { "user_id": "…", "display_name": "…" },   // standing order (FR-016a); null when none
  "suggestion_defaults": { "iam_engineer": ["…"], "application_owner": ["…"] },
  "workload_name": "isc-onboarding-agent",
  "images": [ { "media_type": "image/png", "data_b64": "…" } ]
}
```

- `history`: the last 40 messages of **both threads** (masked), in queue order, each tagged with `thread` and
  `kind`, plus a summary line for earlier ones (FR-006a).
- `workload_name`: the standalone AgentCore workload identity the agent uses to get its SailPoint token.
- `images`: present only for **passed** attachments in `turn` mode, or the single image under check in
  `secret_check` mode.
- No credential is ever in the request. The agent fetches an ISC token itself through AgentCore Identity using
  `credential_provider` (research R4).

### `tenant_check` mode (admin "Check now")

```json
{ "mode": "tenant_check", "tenant": { "api_host": "acme-demo.api.identitynow-demo.com", "credential_provider": "onboarding-isc-acme-demo" } }
```

The agent fetches a token through AgentCore Identity and calls `GET /beta/tenant`. It returns one event:
`{ "type": "tenant_check", "status": "usable" | "credential_rejected" | "unchecked", "external_id": "…" | null }`
(`credential_rejected` for a token or 400/401/403 failure; `unchecked` for anything else). The API updates the
tenant's status and External ID.

## Response: streamed events (one JSON object per chunk)

| `type` | Fields | API does |
|---|---|---|
| `delta` | `text` | mask → `agent.delta` in the writer's thread |
| `progress` | `text` or `null` | → `agent.progress` |
| `set_step` | `step`, `state` | validate transition (data-model) → `steps` → `step.changed` |
| `action` | `action`, `source`, `request_summary`, `result`, `error`, `task_ids` | add `ordered_by` from the request → `actions` → `action.recorded` |
| `source` | `id`, `name` | set `sessions.source` (only after a successful `create_source`) |
| `application_step` | `text`, `read_only` | store in the turn's message metadata (owner's step list) |
| `final` | `text` | mask → `messages` (writer's thread) → `agent.message` |
| `other_thread` | `text?`, `relay_note`, `relayed_from?` | mask → agent message in the other thread (if `text`) + relay note in the writer's thread → `message.created` ×1–2 (FR-006c) |
| `waiting` | `on: role \| null`, `reason?` (one line, ≤ 120 chars) | mask, cap → `sessions.waiting_on`, `waiting_reason` → `thread.waiting` |
| `suggestions` | `thread`, `items[{text, kind}]` | validate, top up, cap (research R17) → `sessions.suggestions` → `suggestions.updated` |
| `secret_check` | `result: passed \| held`, `reason` | `secret_check` mode only |
| `tenant_check` | `status`, `external_id` | `tenant_check` mode only |
| `error` | `code`, `message` | → `turn.failed` |

## Agent-side rules (tested in `onboarding/agent/tests`)

- **Role gate (FR-016, FR-016a, FR-019)**: if `ordered_by.role != iam_engineer`, ISC write tools (`create_source`,
  `configure_source`, `delete_session_source`) are never offered. The check tools (`peek_accounts`,
  `start_aggregation`, `test_connection`) are offered to an application owner's turn **only** when `check_order` is
  set and a source exists; their `action` events are recorded under `check_order`. Otherwise the agent explains that
  the IAM engineer orders SailPoint changes.
- **Thread tools (FR-006c)**: `post_to_other_thread(text?, relay_note)`, `notify_other_thread(relay_note)`,
  `set_waiting(on, reason?)` (reason: the awaited person's next step, for the waiting banner), `suggest_replies(thread, items)`. The reply itself always streams into the writer's thread; the
  model never chooses where its streamed text goes.
- **No AWS tools exist (FR-010).** Application-side work is only ever text instructions from the playbook's
  `setup.md`.
- **Collision rule (FR-018)**: `create_source` first calls `find_source(name)`. An existing source not equal to
  `session.source` ends the tool with `owned_by` and no change.
- **Bounded loop**: ≤ 8 tool rounds per turn. Then the agent sends `final` with what is still pending.
- **Every ISC write emits exactly one `action` event**, success or failure (SC-006).
- **Transient failure** ("no schema provided", "req.input is null" right after configuring): retry once, then
  report (FR-022).
