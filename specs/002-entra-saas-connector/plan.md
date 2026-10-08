# Implementation Plan: Microsoft Entra ID SaaS connector for the ISC Onboarding Agent

**Branch**: `002-entra-saas-connector` (work currently on `skill/sailpoint-isc-entra-connector`) | **Date**: 2026-10-08 |
**Spec**: [spec.md](spec.md) | **Builds on**: [001 plan](../001-isc-onboarding-agent/plan.md) | **Design**: canvas
"ISC Onboarding Agent UI", row "002 · Microsoft Entra ID" (artboards New Entra ID session, Entra administrator, IAM
engineer) and the updated Connector catalog; revised 2026-10-08 to match it (see the last section)

**Input**: Feature specification from `specs/002-entra-saas-connector/spec.md`

## Summary

Make **Microsoft Entra ID** the second *available* connector type of the ISC Onboarding Agent (spec 001), using
SailPoint's cloud "Microsoft Entra" connector (scriptName `Microsoft-Entra`). Its behaviour comes from the
live-verified `.claude/skills/sailpoint-isc-entra-connector` skill, which is ported into a new playbook in the same
way the AWS SaaS playbook was ported from its skill. The playbook offers four capabilities: directory (always on),
service principals, AI agents (Azure AI Foundry) and provisioning.

Technical approach:

- **Playbook `catalog/playbooks/entra-id/`**: setup steps for the Entra administrator (`az` commands, ported from
  `entra-setup.sh` and `entra-permissions.md`), source settings by capability (ported from `feature-toggles.json` and
  the ISC templates), the proof sequence, failures, collisions, suggestions, plan, and the JSON assets (service
  principal schema attributes, provisioning policy, correlation). These are data, as in 001.
- **Application secret**, which is new for all types (R1–R3):
  - The Entra administrator pastes the Value and its expiry date into a write-only **secret field**. The session API
    stores the value as an **AgentCore Identity API-key credential provider** for that session
    (`onboarding-entra-<session>`); MongoDB holds only metadata.
  - The agent's `configure_source` tool reads the value inside tool code with the workload token and puts it into
    the source PATCH. The value never enters the model's context, the events or the action record.
  - When Test Connection passes, the API deletes the provider (FR-125).
- **ISC API v2026 for Entra only** (R4): the playbook declares `isc_api: v2026` and its experimental paths. The ISC
  client then sends `X-SailPoint-Experimental` only on those paths, and the tools build their paths from the
  version table. With no `isc_api`, AWS SaaS keeps today's beta/v3 paths and headers unchanged.
- **Generic tools, opted into per playbook** (R5):
  - New tools: `find_connector_sources`, `adopt_source`, `ensure_schema_attributes`, `aggregate_datasets`,
    `set_dataset_schedule`, `set_provisioning_policy`, `set_correlation`, `read_source_setup`.
  - `start_aggregation` gains a playbook-driven sequence: entitlements, then accounts, each a full read with delta
    switched off and then restored.
  - A playbook lists the tools it uses, so an AWS turn is offered exactly today's tools. Its prompt, cache prefix and
    eval fingerprint stay byte-identical (SC-106).
- **Long aggregations** (R6): a tool waits in the turn for up to 3 minutes, then returns `running`. The API takes
  over with a model-free `task_check` agent mode every 60 s.
  - The step shows *pending* with elapsed time after 30 minutes.
  - When the task ends, the API posts a system note in the IAM engineer's thread.
  - If the task succeeded and proof steps remain, the API queues a model turn ordered by the IAM engineer who holds
    the standing check order (Principle III).
- **Extend-source** (R7): the form's "Create a new source / Extend an existing Entra source" choice, or the agent's
  offer at session start, binds the session to an existing Entra source through `adopt_source`. That tool checks
  ownership; plan steps marked `on_extend: skip` are skipped, so only the new capabilities' steps remain.
- **Web** (R8, R15–R19, per the canvas):
  - New-session form: capability cards, conditional fields, the provisioning warning that must be accepted before
    Start session, and a "what happens" panel with permission counts.
  - Entra administrator: the secret field card above the thread, with a 4-step status strip.
  - IAM engineer: a "What SailPoint now sees" counts card and a secret card.
  - System-note tones: exposed secret, pending, finished.
  - Header chips in the playbook's milestone order.

## Technical Context

**Language/Version**: Python 3.12 (session API, agent); TypeScript 5.9 with Angular 22 (web). Unchanged from 001.

**Primary Dependencies**:
- No new libraries.
- API: `boto3` `bedrock-agentcore-control`: `create_api_key_credential_provider`, `delete_api_key_credential_provider`
  and `get_api_key_credential_provider`.
- Agent: `bedrock-agentcore` `IdentityClient.get_api_key` (workload token, as for the ISC token today) and `httpx`.

**Storage**:
- MongoDB 8.0 (unchanged). New fields on `sessions`; new kinds on `actions` and `audit` ([data-model.md](data-model.md)).
- AgentCore Identity token vault: one API-key provider per session, which exists only from receipt until Test
  Connection passes.

**Testing** (no paid model calls by default, Constitution IV):
- pytest + respx for the v2026 path table, experimental header, secret injection, delta toggle and restore, dataset
  fallback, and follow-up.
- The stub ISC gains the `/v2026` Entra routes and failure switches.
- The scripted model gains an Entra script; Playwright adds an Entra end-to-end spec on the stub.
- Opt-in, costed Entra failure evals (SC-103).
- The AWS suites run unchanged.

**Target Platform**: unchanged (the local cluster and its registry, plus AgentCore Runtime and
Bedrock in ap-southeast-1)

**Project Type**: web application plus a hosted agent (unchanged)

**Performance Goals**:
- 001 targets unchanged.
- Directory-only session ≤ 30 min for ≤ 5,000 accounts (SC-101).
- Follow-up poll every 60 s; the result note within 2 minutes of the task ending (FR-139).

**Constraints**:
- The secret Value never reaches the model, events, logs, MongoDB, the browser after submit, action records or
  exports (FR-120, SC-102).
- Entra uses v2026 only, with experimental paths listed in the playbook (FR-138).
- What AWS SaaS sees, offers, prompts and calls stays identical (SC-106).
- The agent never acts in Entra or Azure (FR-113).
- The provisioning proof writes nothing to the directory (FR-136).

**Scale/Scope**:
- 001 scale (≤ 10 concurrent sessions).
- Entra tenants up to 5,000 accounts inside the time target; larger tenants follow FR-139.
- 2 available connector types.

No NEEDS CLARIFICATION remains. The open item, whether API-key providers can be used from this runtime, has a
fallback recorded in R1.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Gated on constitution **v1.0.0** (`.specify/memory/constitution.md`).

| Principle | How this design meets it | Pre | Post |
|---|---|---|---|
| **I. Secrets stay in their vault** | The Entra secret lives only in an AgentCore Identity API-key provider (R1). It passes through the API once, as the tenant PAT does today. MongoDB holds metadata only. The agent reads it in tool code and the model never sees it (R2). The vault copy is deleted after Test Connection passes (FR-125). The one masker gains the Entra secret pattern (R3). The leak scan adds the pattern and searches the stored data for test secret fingerprints (SC-102). | ✅ | ✅ |
| **II. Least privilege and gated exposure** | The secret endpoint is owner-only, enforced by the API. The new ISC write tools are behind the existing role gate. The API's IAM user gains API-key provider create/delete/get on `apikeycredentialprovider/onboarding-entra-*`; the agent role gains `GetResourceApiKey` and read access to the matching vault secrets. The agent can read any `onboarding-entra-*` provider, not only its session's (see Complexity Tracking). No new routes or exposure. | ⚠️ | ⚠️ justified |
| **III. Human identity end to end** | Every new write tool emits an action naming the ordering IAM engineer. Secret received, replaced and deleted are audited with the owner or the system. Follow-up turns run as the IAM engineer who holds the standing check order (R6). `adopt_source` is an IAM-engineer action (R7). | ✅ | ✅ |
| **IV. Cost-bounded AI use** | Polling long tasks uses the model-free `task_check` mode; only "continue the proof" is a model turn (R6). The new static playbook text is cached as in 001 (R27). Tests use the scripted model. Entra evals are opt-in, with a printed estimate (about 33 calls and about $0.40 at 3 runs), and run only when their fingerprint changes (R12). The AWS prompt text is unchanged (T005), but shared prompt and loop files change once, so the AWS gate runs once, with its estimate stated first (T092). | ✅ | ✅ |
| **V. Reproducible, verified delivery** | The existing `make onboarding-*` targets and scripts; IAM policy changes go in `agent-deploy.sh`. Tests ship with each change. Spec 001 gets its amendment notes before code (tasks phase). The rollout is followed by tests, the leak scan and the smoke check, including an Entra stub scenario. | ✅ | ✅ |

The gates pass. The one ⚠️ is recorded below.

## Project Structure

### Documentation (this feature)

```text
specs/002-entra-saas-connector/
├── plan.md              # This file
├── research.md          # Phase 0: R1–R14
├── data-model.md        # Phase 1: changes to sessions, catalog, actions, audit; vault object
├── quickstart.md        # Phase 1: validation guide (stub, real tenant opt-in, AWS regression, leak scan)
├── contracts/
│   ├── entra-playbook.md          # the entra-id playbook and the new playbook keys (extends 001 connector-playbook.md)
│   ├── session-api.openapi.yaml   # new and changed REST paths only (extends 001 session-api.openapi.yaml)
│   ├── agent-invocation.md        # payload additions, task_check mode, new tools and events
│   └── live-events.md             # new SSE events
├── checklists/requirements.md
└── tasks.md             # Phase 2 (/speckit-tasks; not created here)
```

### Source Code (repository root)

```text
onboarding/
├── catalog/
│   ├── catalog.yaml                      # entra-id: status available, session_fields, capabilities, secret, isc_api
│   └── playbooks/entra-id/               # NEW, ported from .claude/skills/sailpoint-isc-entra-connector
│       ├── setup.md                      #   Entra administrator steps per capability (az), expected output
│       ├── settings.yaml                 #   isc_api v2026, experimental paths, creation_only, configure per capability,
│       │                                 #   secret_fields, full_read toggle
│       ├── checks.yaml                   #   proof order, tools, plan_step, dataset ids, provisioning checks
│       ├── plan.yaml                     #   starting plan with capability and on_extend markers
│       ├── failures.md                   #   E1–E11 (FR-140)
│       ├── collisions.md                 #   same-name app registration; existing source for the tenant
│       ├── suggestions.yaml
│       ├── permissions/                  #   readonly, machine-identity, ai-agents, provisioning .json (from the skill);
│       │                                 #   setup.md renders them, the catalog counts them (R15)
│       └── assets/                       #   account-schema-spn-attributes.json, provisioning-policy-create.json,
│                                         #   correlation-config.json, lifecycle-state-account-actions.patch.json
├── api/src/onboarding_api/
│   ├── catalog/catalog.py                # new field types + validation, capabilities options, secret block, warning
│   ├── secrets/                          # NEW: application secret (routes.py, service.py, store.py)
│   ├── sessions/{routes,repo,plan}.py    # extends_source, capability-filtered plan seeding, on_extend skips,
│   │                                     #   secret status in GET
│   ├── chat/turns.py                     # payload application_secret meta; source{adopted}; delete vault copy
│   │                                     #   after Test Connection; running → follow-up
│   ├── chat/followups.py                 # NEW: task follow-up loop (task_check), pending display, continue turn
│   ├── masking.py                        # Entra client secret pattern
│   └── audit/                            # kinds: application_secret_received|replaced|vault_deleted
├── agent/src/onboarding_agent/
│   ├── isc/client.py                     # api version, experimental path list, count() via X-Total-Count, multipart
│   ├── isc/paths.py                      # NEW: path table legacy (beta/v3) vs v2026
│   ├── isc/tools.py                      # secret injection, aggregation sequence + full read, new generic tools
│   ├── isc/vault.py                      # NEW: get_api_key via workload token (stub: fixed placeholder)
│   ├── loop.py                           # tools offered = playbook tools ∩ role gate; check order from checks.yaml
│   ├── main.py                           # mode task_check
│   ├── playbooks.py                      # assets/, capabilities, plan filtering helpers; AWS _policies unchanged
│   └── prompts/en/system.md              # secret rules (never ask for it in chat; point to the secret field)
├── web/src/app/
│   ├── catalog/catalog.component.ts      # one card per available type: capability chips, "new" badge, counts
│   ├── session/new-session.component.ts  # connector type cards, capability cards, conditional fields,
│   │                                     #   provisioning warning + acceptance, new/extend radio, "what happens" panel
│   ├── shared/secret-field.component.ts  # NEW: owner-only write-only Value + expiry, inline errors, status strip
│   ├── shared/secret-status.component.ts # NEW: IAM-side secret card (provided, in SailPoint, vault deleted, expiry)
│   ├── shared/proof-counts.component.ts  # NEW: "What SailPoint now sees" (users, service principals,
│   │                                     #   entitlements, AI agents or "Start it in ISC")
│   ├── shared/status-chips.component.ts  # milestone order from the playbook (Entra: test before aggregation)
│   └── shared/thread.component.ts        # system-note tones: exposed secret, pending, finished
├── deploy/
│   ├── stub-isc/stub_isc.py              # /v2026 Entra routes + _stub switches
│   └── scripts/{agent-deploy.sh,leak-scan.sh,smoke.py,evals.sh}
└── agent/tests/evals/entra_failures/     # NEW: cases.yaml + sample screenshots (fictional tenant)
```

**Structure Decision**: Extend the existing `onboarding/` product. All Entra-specific knowledge lives in
`catalog/playbooks/entra-id/`. Code changes are generic and opted into per playbook: the version table, the new
tools, the secret field and long-task follow-up. A later connector type that needs a secret or v2026 reuses them
without code changes. The skill stays the source of truth for the commands and settings. The playbook header names
the skill files and commits (`9b53746`, `b71c53c`) it was ported from.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| Principle II: the agent role can read every `onboarding-entra-*` API-key provider, not only the session's. | The agent is one runtime serving all sessions. IAM can't scope a static role to "this session's" provider. | (a) A per-session IAM policy written by the API needs `iam:PutRolePolicy` for the API, a far bigger privilege. (b) Sending the secret in the invoke payload puts it on the wire and in the agent's request context, against Principle I. Mitigations: the tool reads only the provider named by the API for that session; a provider exists only from receipt until Test Connection passes (normally minutes); each read is logged in CloudTrail. |

## Revision 2026-10-08 (canvas): what the 002 artboards add

The 002 row of the design canvas makes several things concrete that the first design left open. Design: research
R15–R19. No spec requirement changes; each item serves an existing FR.

| Area | Canvas shows | Change | Spec |
|---|---|---|---|
| Catalog | An Entra card next to AWS, with capability chips (provisioning tagged "writes"), a "new" badge and "2 available · 8 planned" | Catalog entry `capabilities[].tag` (`read_only` \| `writes`) and `badge`; the counts are computed from the catalog | FR-101 |
| New session | Connector type as two radio cards; capability cards with one-line descriptions; Foundry subscriptions and provisioning fields shown inside their capability; the warning with an "I understand and accept" checkbox; **Start session disabled** until it is ticked; "Create a new source / Extend an existing Entra source" radio; a side panel "The Entra administrator will be asked to" (permission counts per capability, owner step count, "1 capability writes") and "The agent will, on your order" (the proof order) | `capabilities[].summary` and `owner_summary` (with `{permission_count}`) in the catalog; permission counts computed from `playbooks/entra-id/permissions/*.json` at catalog load (R15); `session_fields[].show_if`; `details.source_mode` radio replaces the free-text `extend_source` field (the agent finds the source, R7) | FR-102–FR-105 |
| Entra administrator | A **secret field card above the conversation**, owner-only, with the Value, the Expires date, "Send to the vault", an inline error, and a 4-step status strip (Not received → Received → In SailPoint → Vault copy deleted); a red system note when a secret was pasted in chat; the "Your next step" card naming the secret step; the session values card including Client ID | Card placement and states (R16); new system-note kind `secret_exposed`, written by the API when the masker hits the Entra secret pattern in an owner message (R17) | FR-120–FR-123, US2-3/4/5 |
| IAM engineer | A **"What SailPoint now sees"** card (users, service principals, entitlements, AI agents, or "Start it in ISC"); a secret card (provided, in SailPoint, vault copy deleted, expires, 30-day warning); action rows with a one-line summary (`clientSecret: [vaulted] · 27 fields`, `delta off → restored · 4,973 accounts`, `tenant limitation`); blue *pending · 30 min* and green *finished after 41 min* system notes; header text "API v2026"; the owner thread collapsed to a bar "Show side by side · 2 new" | `sessions.proof` (latest counts by kind, R18); action `summary` line built by the agent tool (masked); system-note tones `pending` / `finished` from the follow-up loop; the IAM screen keeps 001's side-by-side threads but may collapse the owner thread (per-viewer, browser-only, R19) | FR-133, FR-135, FR-139 |
| Header | Milestone chips in Entra's order: Entra app ready, Source created, Configured, Connection check, **Test Connection, Aggregation** | `checks.yaml` `milestone_order`; default is 001's order (AWS unchanged) | FR-133, SC-106 |
| Suggestions | "I've put the new secret in the field", "Where is the Value column?", "Done, I started it in ISC", "Show the leaver actions to review" | Suggestion states `waiting_for_secret` and `tenant_limitation` added to the allowed list; AWS keeps its states | 001 FR-006e |

Constitution Check after this revision: unchanged, all gates as above. Every new text (summaries, system notes,
counts) goes through the one masker; the secret card shows metadata only; the collapsed thread is browser state.
