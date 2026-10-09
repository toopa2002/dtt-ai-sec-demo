# Data model changes: Microsoft Entra ID connector (spec 002)

These are changes to the [001 data model](../001-isc-onboarding-agent/data-model.md). Unlisted collections and
fields are unchanged. No field ever holds the application secret's value.

## Connector catalog entry (`catalog.yaml`, not a collection)

The `entra-id` entry becomes `status: available`, with `playbook: playbooks/entra-id`. It gains these optional keys,
which any type may use:

| Key | Type | Rules |
|---|---|---|
| `capabilities` | `{id, label, summary, tag, owner_summary, always?, warning?, requires_fields?[]}`[] | Entra: `directory` (always), `service_principals`, `ai_agents` (requires `foundry_subscriptions`), `provisioning` (warning; requires `upn_domain`, `usage_location`). `tag` = `read_only` \| `writes`; `owner_summary` may hold `{permission_count}`, filled at load from `permissions/<file>.json` (research R15) |
| `badge` | string? | e.g. `new`, shown on the catalog card |
| `secret` | `{label, help, expires_help}`? | present → the type needs an application secret (FR-120) |
| `isc_api` | `"v2026"`? | copied from settings for display only; absent = legacy |

New `session_fields[].type` values: `entra_tenant`, `guid_list`, `domain`, `country_code`, `capabilities`. A field
may have `show_if: <capability>`, which means it is shown and validated only when that capability is chosen.

Entra `session_fields`:

| name | type | required | notes |
|---|---|---|---|
| `source_name` | string | yes | default `Entra ID - {tenant_domain}` |
| `source_owner` | string | yes | ISC alias, email or id (as AWS) |
| `tenant_domain` | entra_tenant | yes | `*.onmicrosoft.com` or tenant GUID; other domains accepted with a warning |
| `app_name` | string | yes | default `SailPoint ISC - {tenant}` (one app registration per ISC tenant, collisions.md) |
| `capabilities` | capabilities | yes | always contains `directory` |
| `foundry_subscriptions` | guid_list | if `ai_agents` | `show_if: ai_agents` |
| `upn_domain` | domain | if `provisioning` | `show_if: provisioning` |
| `usage_location` | country_code | if `provisioning` | `show_if: provisioning`, default `TH` |
| `source_mode` | enum `new` \| `extend` | yes | default `new`; radio on the form; with `extend` the agent finds the tenant's source (FR-105, research R7) |
| `client_id` | string (GUID) | no | Application (client) ID; usually filled later by the agent from the administrator's output |

## sessions: new fields

| Field | Type | Rules |
|---|---|---|
| `details.warnings_accepted` | `{capability, user_id, at}`[] | required for each chosen capability with a `warning` (FR-103) |
| `details.client_id` | string? | set by the session API from the agent's `detail` event once the administrator's output shows it; not secret |
| `mode` | `"new"` \| `"extend"` | `extend` after `adopt_source` (FR-105); default `new` |
| `source.adopted` | bool | true when the source was created in an earlier session; `delete_session_source` refuses it |
| `application_secret` | ApplicationSecret? | present only when the catalog entry has `secret` |
| `followups` | Followup[] | long-running tasks the API follows without a turn (FR-139) |
| `proof` | Proof? | latest proof counts for the IAM engineer's "What SailPoint now sees" card (research R18) |

**ApplicationSecret** (metadata only):

| Field | Type | Rules |
|---|---|---|
| `provider` | string | `onboarding-entra-<session_id>`; the AgentCore Identity API-key provider name |
| `state` | `missing` \| `received` \| `in_isc` \| `vault_deleted` | see transitions below |
| `provided_by` | ObjectId → users | the application owner who submitted it |
| `provided_at` | Date | |
| `expires_on` | Date (day) | entered with the value; today < d ≤ today + 2 years (FR-121) |
| `applied_at` | Date? | when a configure or apply action that included it succeeded |
| `replaced_at` | Date? | last replacement (FR-122) |
| `vault_deleted_at` | Date? | FR-125 |
| `fingerprint` | string? | **stub mode only**: SHA-256 of the value, for the leak test; never set on the cluster |

State transitions:

```text
missing ──submit──▶ received ──configure/apply ok──▶ in_isc ──Test Connection ok──▶ vault_deleted
   ▲                   ▲  │                                │                                 │
   │                   │  └─────── submit (replace) ◀──────┴────────── submit (new) ◀────────┘
   └─ never set back to missing
```

- Submit always replaces the vault provider (create or update) and sets `received`.
- `vault_deleted` + a sign-in failure (E1/E2) → the agent asks for a new secret, and the owner screen shows the
  field again.
- Expiry is shown in every state. `expires_soon` (≤ 30 days) is computed on read and never stored.

**Followup**:

| Field | Type | Rules |
|---|---|---|
| `action_ref` | string | the `running` action it completes |
| `kind` | `entitlement_aggregation` \| `account_aggregation` \| `dataset_aggregation` | |
| `task_ids` | string[] | |
| `plan_step` | string | plan step id it marks |
| `next_step` | string? | next proof step to continue with on success |
| `ordered_by` | `{user_id, display_name}` | the IAM engineer of the standing check order; continuation turns run as them |
| `started_at`, `last_checked_at` | Date | |
| `state` | `following` \| `done` \| `failed` | at most 3 `following` per session |

## sessions.plan: PlanStep additions

| Field | Type | Rules |
|---|---|---|
| `capability` | string? | from `plan.yaml`; at seeding, a step whose capability isn't chosen is stored as `skipped` with reason "capability not chosen" |
| `on_extend` | `"skip"`? | from `plan.yaml`; skipped with reason "existing source" when `mode` becomes `extend` |
| `pending_since` | Date? | set by the follow-up loop; the UI shows *pending · N min* once it is ≥ 30 min old |

Milestones stay the six from 001. The Entra plan maps `application_ready` to `confirm_consent`, and the dataset,
schema and provisioning steps have no milestone.

## actions: new values

| Field | New values |
|---|---|
| `action` | `adopt_source`, `ensure_schema_attributes`, `aggregate_entitlements`, `aggregate_accounts`, `aggregate_datasets`, `set_dataset_schedule`, `set_provisioning_policy`, `set_correlation`, `apply_application_secret` |
| `result` | adds `tenant_limitation` (FR-135); counts as neither ok nor failed in the outcome line |
| `request` | secret fields always `"[vaulted]"`; `delta_toggled: bool` on aggregation and peek |
| `response.counts` | `users`, `service_principals`, `entitlements`, `ai_agents` |
| `trigger` | adds `followup` (a continuation turn started by the follow-up loop for the ordering IAM engineer) |

## audit: new kinds

`application_secret_received`, `application_secret_replaced` (who, when, expires_on),
`application_secret_vault_deleted` (when, source id), and `provisioning_warning_accepted` (who, when). None carries
the value.

## Vault object (AgentCore Identity, not MongoDB)

| Object | Name | Lifetime |
|---|---|---|
| API-key credential provider | `onboarding-entra-<session_id>` | from first submit to Test Connection ok; replaced on each submit; also deleted when the session is finished or expires, and by `down.sh` teardown |

## Validation rules carried from the spec

- `capabilities` always contains `directory`. Unknown ids → 422.
- `provisioning` without its warning accepted → 422 `warnings_accepted`.
- Secret value: not a GUID; 16–128 characters; no whitespace. Expiry required and in range. The secret endpoint is
  for the session's application owner only (403 otherwise, including the IAM engineer and admins).
- The secret value is never echoed, logged, stored in MongoDB or included in any event. The request body is
  excluded from request logging for that route.
- `adopt_source` refuses a source owned by another identity, on another connector, or for another tenant domain.

## Additions from the canvas revision (research R15–R19)

**Proof** (embedded in `sessions.proof`; updated from aggregation and dataset action counts):

| Field | Type | Rules |
|---|---|---|
| `users`, `service_principals`, `entitlements`, `ai_agents` | int? | null until that aggregation has run |
| `ai_agents_state` | `counted` \| `tenant_limitation` \| `not_chosen` | drives the AI agents tile ("Start it in ISC") |
| `updated_at` | Date | |

**messages**: `kind: system_note` gains `tone` (`info` \| `success` \| `danger`; default `info`) and `code`
(`secret_exposed`, `followup_pending`, `followup_finished`, or null). `secret_exposed` notes go in the owner's
thread right after the masked message (R17).

**actions**: `summary` (string?, ≤ 80, masked): one line shown under the action name. Entra tools set it; AWS tools
don't.

**audit**: adds `secret_exposed_in_chat` (who, when; never the value).

**Playbook data** (not collections):
- `checks.yaml` `milestone_order` (the header chip order; absent = 001 order).
- Suggestion states `waiting_for_secret` and `tenant_limitation` join the allowed list.
- `playbooks/entra-id/permissions/{readonly,machine-identity,ai-agents,provisioning}.json`, from the skill.
