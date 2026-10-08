# Contract: live events, session API → browser (spec 002 additions)

These extend [001 live-events.md](../../001-isc-onboarding-agent/contracts/live-events.md). They go to both
participants of the session unless noted. None carries the secret value.

| Event | Payload | When |
|---|---|---|
| `secret.updated` | `SecretStatus` (see session-api.openapi.yaml) | submit, replace, applied in ISC, vault copy deleted |
| `secret.needed` | `{reason}` | the agent asks for a new secret (E1/E2, rebuild after vault deletion) |
| `followup.updated` | `{plan_step, kind, state, started_at, minutes}` | each follow-up check (once a minute) and when it ends |
| `session.mode` | `{mode: "extend", source: {id, name}}` | after `adopt_source` |
| `proof.updated` | `Proof` (data-model.md), **IAM engineer only** | after an aggregation or dataset action with counts |

The existing events also carry the new fields: `plan.updated` has `capability` and `pending_since` on plan steps,
and `action.*` has `tenant_limitation` and the new counts. `action.*` still goes only to the IAM engineer (001 R24).

`message.created` for a `system_note` carries `tone` and `code`. The `secret_exposed` note goes to the owner's thread
and, like every message, is visible view-only to the IAM engineer. Action events carry `summary`.
