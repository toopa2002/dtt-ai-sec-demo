# Contract: session API ↔ AgentCore runtime (spec 002 changes)

This extends [001 agent-invocation.md](../../001-isc-onboarding-agent/contracts/agent-invocation.md). An AWS SaaS
payload and its events are unchanged.

## `turn` request: additions

```json
{
  "session": {
    "connector_type": "entra-id",
    "mode": "new",
    "details": { "tenant_domain": "contoso-demo.onmicrosoft.com", "app_name": "SailPoint ISC - acme-demo",
                 "capabilities": ["directory", "ai_agents"], "foundry_subscriptions": ["…"], "client_id": "…" },
    "source": { "id": "…", "name": "…", "adopted": false },
    "application_secret": { "provider": "onboarding-entra-6702…", "state": "received", "expires_on": "2027-10-01",
                            "provided_by": "Ploy (Entra administrator)" }
  },
  "trigger": "order",
  "secret_waiting": true
}
```

- `application_secret` is **metadata only**. `provider` lets the tool code read the value from the vault; the
  model's prompt gets `state` and `expires_on` only (the static and turn prompts omit `provider`).
- `trigger`: `order` | `application_owner_confirmation` | `followup` (continuation queued by the follow-up loop;
  `ordered_by` is the IAM engineer of the standing check order) | `secret_submitted` (the owner submitted a secret
  while a check order stands).
- `secret_waiting`: a secret in state `received` hasn't been applied yet.
- `secret_exposed`: this turn's owner message contained an Entra secret, now masked (research R17). The agent tells
  the owner to delete that secret in Entra and use the secret field. The API has already posted the system note.

## New mode: `task_check` (model-free)

```json
{ "mode": "task_check", "tenant": { "api_host": "…", "credential_provider": "…" }, "isc_api": "v2026",
  "task_ids": ["2c91…", "2c92…"] }
```

The agent returns one event:
`{ "type": "task_check", "tasks": [ { "id": "…", "completion_status": "SUCCESS" | "WARNING" | "ERROR" | "TERMINATED" | "TEMPERROR" | null, "messages": ["…"] } ] }`.
It makes no model call. Errors give `{ "type": "task_check", "error": "<masked text>" }`.

## Tool rules (agent side)

- **Offered tools** = `checks.yaml` `tools` (001 set when absent) ∩ the role gate. On an owner turn under a
  standing check order, the check tools are `peek_accounts`, `test_connection`, `start_aggregation`,
  `aggregate_datasets` and, with `trigger: secret_submitted`, `apply_application_secret`.
- **Secret injection**: `configure_source` and `apply_application_secret` read `secret_fields` from the vault
  (`IdentityClient.get_api_key(provider)`). No tool takes the secret as an input. Tool results say
  `secret_applied: true|false`, and the action `request` shows `[vaulted]`.
- **v2026**: with `isc_api: v2026`, every path comes from the v2026 table, and `X-SailPoint-Experimental: true` is
  sent only on `experimental_paths`.
- **`adopt_source`** refuses when the owner, connector or tenant field doesn't match. **`delete_session_source`**
  refuses an adopted source.

## Response events: additions

| `type` | Fields | API does |
|---|---|---|
| `action` | adds `follow: true` with `result: running`; `result: tenant_limitation`; counts `users`, `service_principals`, `entitlements`, `ai_agents` | `follow` → create a Followup (data-model.md); `tenant_limitation` → plan step `blocked` with the UI path; `test_connection` ok + secret `in_isc` → delete the vault provider (FR-125) |
| `action` (`configure_source` / `apply_application_secret`, ok, `secret_applied: true`) | | secret state `received → in_isc`, `applied_at` |
| `source` | adds `adopted: true` | `mode = extend`; skip `on_extend: skip` steps |
| `detail` | `name: client_id`, `value` (GUID) | set `details.client_id`. Only `client_id` is accepted; anything else is ignored |
| `secret_needed` | `reason` (masked, ≤ 160) | show the owner's secret field again; system note in the owner's thread |
| `task_check` | see above | follow-up loop only |

Every action event from the Entra tools also carries `summary` (one line, ≤ 80 characters, never a secret), e.g.
`clientSecret: [vaulted] · 27 fields` or `delta off → restored · 4,973 accounts` (research R18).
