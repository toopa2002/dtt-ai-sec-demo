---
description: "Task list for the Microsoft Entra ID SaaS connector type (spec 002)"
---

# Tasks: Microsoft Entra ID SaaS connector for the ISC Onboarding Agent

**Input**: Design documents from `specs/002-entra-saas-connector/`

**Prerequisites**: plan.md, spec.md, research.md (R1–R19), data-model.md, contracts/, quickstart.md; the design
canvas "ISC Onboarding Agent UI", row 002 (New Entra ID session, Entra administrator, IAM engineer; updated
Connector catalog).

**Tests**: Included. Constitution V requires tests with every change, and these success criteria can only be checked
by tests:
- SC-102: no secret leaks;
- SC-105: no write permission in a directory-only plan;
- SC-106: AWS SaaS unchanged;
- SC-103: diagnosis evals, opt-in and paid.

Every automated test uses the stub ISC and the scripted model (Constitution IV).

**Organization**: by user story (spec.md US1–US6), in priority order. Extend-source (FR-105) and long-running
aggregations (FR-139) are separate phases labelled with the story they extend.

**Source of truth for Entra behaviour**: `.claude/skills/sailpoint-isc-entra-connector/`. Every playbook file names
the skill file it was ported from in its header.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an unfinished task)
- **[Story]**: US1–US6 from spec.md
- Paths follow plan.md: `onboarding/api`, `onboarding/agent`, `onboarding/web`, `onboarding/catalog`, `onboarding/deploy`

---

## Phase 1: Setup

**Purpose**: Record the spec 001 amendments before code (Constitution V), and lay out the playbook folder with the
skill's data files.

- [X] T001 Add an "Amended by spec 002" note to `specs/001-isc-onboarding-agent/spec.md`, under each of FR-010, FR-018, FR-024, FR-026 and FR-029. Each note is one line naming the 002 requirement: FR-120; FR-105; FR-140; FR-123/FR-124; FR-101.
- [X] T002 Create `onboarding/catalog/playbooks/entra-id/` with empty `setup.md`, `settings.yaml`, `checks.yaml`, `plan.yaml`, `failures.md`, `collisions.md`, `suggestions.yaml` and the folders `permissions/` and `assets/`. Each file gets a header comment naming its skill source (research R14) and the skill commits `9b53746` and `b71c53c`.
- [X] T003 [P] Copy the permission profile files from `.claude/skills/sailpoint-isc-entra-connector/assets/entra/permissions/{readonly,machine-identity,ai-agents,provisioning}.json` to `onboarding/catalog/playbooks/entra-id/permissions/` unchanged. Do not copy exchange, teams or pim (out of scope).
- [X] T004 [P] Copy the skill's ISC assets into `onboarding/catalog/playbooks/entra-id/assets/`, replacing the skill's `${VAR}` placeholders with playbook `{placeholder}` syntax:
  - `account-schema-spn-attributes.json` (copied as is);
  - `provisioning-policy-create.json` (from `provisioning-policy-create.tmpl.json`; placeholders `{upn_domain}`, `{usage_location}`);
  - `correlation-config.json` (from `correlation-config.tmpl.json`);
  - `lifecycle-state-account-actions.patch.json` (from `lifecycle-state-account-actions.patch.tmpl.json`).

---

## Phase 2: Foundational (blocking prerequisites)

**Purpose**: The generic pieces every Entra story needs: the v2026 path table, playbook opt-in for tools, catalog
fields and capabilities, plan filtering, the vault for the application secret, the masker pattern, the stub ISC, and
the AWS regression guard.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete. The AWS SaaS e2e and unit suites must
still pass at the end of it.

### AWS regression guard (write first, keep green)

- [X] T005 [P] Write `onboarding/agent/tests/unit/test_aws_unchanged.py` before any code change. It asserts, for the `aws-saas` playbook:
  - the exact set from `offered_tools(role, check_order, has_source)` for all 6 role/state combinations;
  - the SHA-256 of `static_system()` for a fixed AWS session fixture;
  - the method and path of every ISC call made by `create_source`, `configure_source`, `peek_accounts`, `start_aggregation` and `test_connection` against respx, which must stay on today's `/beta/…` and `/v3/…` paths, with `X-SailPoint-Experimental: true` on every call.

  Record the current values as the expected snapshot (SC-106).

### ISC client and path table (agent)

- [X] T006 Create `onboarding/agent/src/onboarding_agent/isc/paths.py` with two tables mapping logical operations to path templates (research R4):
  - `LEGACY`: exactly today's paths in `isc/tools.py`.
  - `V2026`: every path under `/v2026/`, bodies and task-id locations as in `.claude/skills/sailpoint-isc-entra-connector/references/isc-api.md`.

  Operations: `tenant`, `connector`, `connector_form`, `sources`, `source`, `public_identities`, `search`, `peek`, `test`, `load_accounts`, `load_entitlements`, `task`, `accounts`, `entitlements`, `schemas`, `schema`, `provisioning_policies`, `provisioning_policy`, `correlation`, `datasets`, `dataset`, `aggregate_agents`, `machine_identities`.

  Add `for_playbook(settings) -> table`, which returns `V2026` when `settings.get("isc_api") == "v2026"` and `LEGACY` otherwise.
- [X] T007 Extend `onboarding/agent/src/onboarding_agent/isc/client.py`:
  - `IscClient(..., experimental: list[str] | None = None)`. With `None`, send `X-SailPoint-Experimental: true` on every call (today's behaviour). With a list, send it only when the path matches one of the regexes.
  - `count(path, filters) -> int`: GET with `count=true&limit=1`, return `int(X-Total-Count)`.
  - `post_multipart(path, fields: dict)`: multipart/form-data.
  - Keep `IscError` token stripping.
- [X] T008 Refactor `onboarding/agent/src/onboarding_agent/isc/tools.py` so every ISC call goes through `self.paths = paths.for_playbook(pb.settings)`, with no behaviour change for AWS (T005 stays green):
  - The task id is read from `.task.id` or `.id` (both tables).
  - The v2026 accounts filter is `source.id eq "…"`, because `sourceId` returns 400.
  - `load-accounts` sends multipart `disableOptimization=true` and `load-entitlements` sends multipart with no fields when the table is `V2026`.
- [X] T009 Pass `experimental=pb.settings.get("experimental_paths")` when building `IscClient` in `onboarding/agent/src/onboarding_agent/main.py` `_run_turn`. The AWS settings have no such key, so AWS behaviour is unchanged.
- [X] T010 [P] Write `onboarding/agent/tests/unit/test_isc_paths.py`:
  - every `V2026` path starts with `/v2026/`;
  - the experimental header is sent only for `/aggregate-agents$`, `/datasets(/|$)` and `^/v2026/machine-identities`;
  - `count()` reads `X-Total-Count`;
  - the accounts filter uses `source.id`.

### Playbook opt-in (agent)

- [X] T011 Extend `onboarding/agent/src/onboarding_agent/playbooks.py`:
  - load `assets/*.json` into `Playbook.assets` (dict by stem) and `permissions/*.json` into `Playbook.permissions`;
  - add `chosen_capabilities(session)`: `details.capabilities`, always including `directory`;
  - add `session_values` keys `tenant_domain`, `app_name`, `client_id`, `upn_domain`, `usage_location`, `foundry_subscriptions`, `subscription_count`.

  Leave `_policies()` and `member_accounts` unchanged for AWS.
- [X] T012 Change `offered_tools()` in `onboarding/agent/src/onboarding_agent/loop.py`:
  - base set = `pb.checks.get("tools")` when present, else today's `TOOL_SPECS` keys;
  - then apply the existing role gate;
  - make `CHECK_TOOLS`, `CHECK_ORDER` and the owner-rerun gate sentence come from `pb.checks["order"]`, rendering exactly today's sentence for AWS (T005);
  - add the new tool names to `WRITE_TOOLS` in `isc/tools.py`: `adopt_source`, `ensure_schema_attributes`, `aggregate_datasets`, `set_dataset_schedule`, `set_provisioning_policy`, `set_correlation`, `apply_application_secret`.
- [X] T013 Add an `action` field `summary` (≤ 80 chars, masked) to `IscTools._action()` in `onboarding/agent/src/onboarding_agent/isc/tools.py` (research R18). It is optional and omitted when `None`, so AWS action events are unchanged.

### Catalog, fields and plan (API)

- [X] T014 Extend `onboarding/api/src/onboarding_api/catalog/catalog.py` (data-model.md "Connector catalog entry"):
  - `FIELD_TYPES` += `entra_tenant`, `guid_list`, `domain`, `country_code`, `capabilities`;
  - `PUBLIC_KEYS` += `capabilities`, `secret`, `badge`, `isc_api`;
  - validate `capabilities[]` items `{id, label, summary, tag ∈ read_only|writes, owner_summary, always?, warning?, requires_fields?[], permissions?}`;
  - at load, fill `{permission_count}` in `owner_summary` from the length of `playbooks/<id>/permissions/<permissions>.json` (research R15);
  - `SUGGESTION_STATES` += `waiting_for_secret`, `tenant_limitation`;
  - a chosen capability with `enabled: false` is rejected with 422 "not available yet".
- [X] T015 Extend `validate_details()` in `onboarding/api/src/onboarding_api/catalog/catalog.py`:
  - `entra_tenant`: `^[a-z0-9-]+\.onmicrosoft\.com$` or a GUID; another domain is accepted with the warning "SailPoint recommends the initial .onmicrosoft.com domain".
  - `guid_list`: each item a GUID.
  - `domain`: a DNS name.
  - `country_code`: `^[A-Z]{2}$`.
  - `capabilities`: known ids only (422 otherwise), always containing `directory`.
  - `show_if: <capability>`: the field is validated and required only when that capability is chosen.
  - `requires_fields`: each listed field must be non-empty.
  - Return warnings alongside the cleaned details.
- [X] T016 Extend plan seeding in `onboarding/api/src/onboarding_api/sessions/plan.py`:
  - store `capability` and `on_extend` from `plan.yaml` on each PlanStep;
  - a step whose `capability` isn't chosen is stored `skipped` with reason "capability not chosen";
  - add `skip_on_extend(session)`, which skips every `on_extend: skip` step with reason "existing source";
  - `milestones()` uses `checks.yaml` `milestone_order` when present.
- [X] T017 Add `"milestone_order"` to the session GET and to `step.changed` events in `onboarding/api/src/onboarding_api/sessions/routes.py`. Make `onboarding/web/src/app/shared/status-chips.component.ts` render chips in that order (default: 001 order).
- [X] T018 [P] Write `onboarding/api/tests/unit/test_entra_catalog.py`. It checks every rule in T014 and T015, plus:
  - a planned type still can't start a session;
  - the `{permission_count}` values equal the JSON array lengths;
  - the AWS session fields validate exactly as before.

### Application secret vault (API + agent)

- [X] T019 Create `onboarding/api/src/onboarding_api/secrets/store.py` (research R1). The interface is `put(name, value)` / `delete(name)`, with three implementations:
  - `AgentCoreApiKeyStore`: `bedrock-agentcore-control` `create_api_key_credential_provider(name, apiKey)`; on conflict, `update_api_key_credential_provider`; `delete_api_key_credential_provider`, with ResourceNotFound ignored.
  - `SecretsManagerApiKeyStore`: the fallback; secret `onboarding-entra-<session>`.
  - `LocalApiKeyStore`: stub mode only; keeps the value and its `sha256` in memory. Add the guarded route `GET /_local/apikey/{name}`, present only when `CREDENTIAL_STORE=discard` and `ONBOARDING_ISC_BASE_URL` points to the stub, and refuse to start with this store on the cluster (research R1).

  The store is selected by the existing `CREDENTIAL_STORE` setting. Errors raise `CredentialStoreError` with the AWS error code only. The value is never logged.
- [X] T020 Create `onboarding/api/src/onboarding_api/secrets/service.py` (data-model.md ApplicationSecret):
  - `submit(session, user, value, expires_on)` validates, stores and sets `application_secret` = `{provider: "onboarding-entra-<session_id>", state: "received", provided_by, provided_at, expires_on, replaced_at?}`, then writes audit `application_secret_received` or `application_secret_replaced`.
  - Validation: reject a GUID with "this is the secret's ID, not its Value"; length "16–128 characters"; "no whitespace"; `expires_on` "today < date ≤ today + 2 years".
  - `mark_applied(session)` sets state `in_isc` and `applied_at`.
  - `delete_vault_copy(session)` deletes the provider and sets state `vault_deleted` and `vault_deleted_at`, then writes audit `application_secret_vault_deleted`.
  - `status(session)` returns metadata plus `expires_soon` (≤ 30 days).
  - Also delete the provider when the session is finished or expires.
- [X] T021 Create `onboarding/api/src/onboarding_api/secrets/routes.py` per `contracts/session-api.openapi.yaml`:
  - `PUT /sessions/{id}/application-secret`, for **this session's application owner only**: 403 for the IAM engineer and admins, 409 when the session is finished, 422 with the field messages, 502 when the vault refuses.
  - `GET /sessions/{id}/application-secret`, for both participants, returning metadata only.
  - Exclude this route's body from request logging in `onboarding/api/src/onboarding_api/logging.py`.
  - Emit `secret.updated`.

  Register the router in `onboarding/api/src/onboarding_api/main.py`.
- [X] T022 Create `onboarding/agent/src/onboarding_agent/isc/vault.py`. `get_application_secret(provider) -> str` calls `IdentityClient.get_api_key(provider_name=provider, agent_identity_token=<workload token>)`, using the same workload-token path as `agentcore_token_fn` in `isc/client.py`. With `ONBOARDING_ISC_TOKEN` set (stub mode), it reads the value from the API's `GET /_local/apikey/{provider}`. The value is never logged or returned to the model.
- [X] T023 Add `application_secret` metadata (`provider`, `state`, `expires_on`, `provided_by`) and the flags `secret_waiting` and `secret_exposed` to the turn payload in `onboarding/api/src/onboarding_api/chat/turns.py` `_context()` (contracts/agent-invocation.md). In `onboarding/agent/src/onboarding_agent/loop.py` `turn_system()`, strip `provider` from the session values shown to the model.
- [X] T024 [P] Write `onboarding/api/tests/unit/test_application_secret.py`:
  - validation messages exactly as in T020;
  - owner-only access: 403 for the IAM engineer and admins;
  - the value is absent from MongoDB, audit, events and logs (search for the test value);
  - state transitions `missing → received → in_isc → vault_deleted` and replace from each state;
  - `expires_soon`.

### Masker, deploy, stub and scripted model

- [X] T025 Add the Entra client-secret pattern to `onboarding/api/src/onboarding_api/masking.py` (research R3): `(?<![A-Za-z0-9_.~-])[A-Za-z0-9_.-]{3}\dQ~[A-Za-z0-9_.~-]{31,34}(?![A-Za-z0-9_.~-])`. Expose `contains_entra_secret(text) -> bool` for R17. Extend `onboarding/api/tests/unit/test_masking.py` with masked samples and unmasked look-alikes (GUIDs, base64 command output), keeping every existing case.
- [X] T026 [P] Add the Entra pattern to `onboarding/deploy/scripts/leak-scan.sh`. In stub mode, add a search of MongoDB, events and logs for the fingerprints `LocalApiKeyStore` holds (SC-102).
- [X] T027 [P] Extend `onboarding/deploy/scripts/agent-deploy.sh` (research R13):
  - API IAM user: `bedrock-agentcore:CreateApiKeyCredentialProvider`, `UpdateApiKeyCredentialProvider`, `DeleteApiKeyCredentialProvider`, `GetApiKeyCredentialProvider` on `token-vault/default/apikeycredentialprovider/onboarding-entra-*`, and `secretsmanager:CreateSecret`, `PutSecretValue`, `DeleteSecret` on `bedrock-agentcore-identity!default/apikey/onboarding-entra-*`.
  - Agent role: `bedrock-agentcore:GetResourceApiKey`, and `secretsmanager:GetSecretValue` on `…/apikey/onboarding-entra-*`.
  - Teardown deletes leftover `onboarding-entra-*` API-key providers.

  Keep it idempotent.
- [X] T028 Add the `/v2026` Entra routes to `onboarding/deploy/stub-isc/stub_isc.py`:
  - `GET /v2026/connectors`, `/v2026/connectors/Microsoft-Entra`, `/v2026/connectors/Microsoft-Entra/source-config`;
  - `GET/POST /v2026/sources` (accepting `filters=connectorName eq "Microsoft Entra"`), `GET/PATCH/DELETE /v2026/sources/{id}` (record PATCH bodies, including `clientSecret`, in `_stub/requests` for the leak test);
  - `POST …/connector/peek-resource-objects` and `POST …/connector/test-configuration`;
  - multipart `POST …/load-entitlements` (task id `.id`) and `POST …/load-accounts` (task id `.task.id`);
  - `GET /v2026/task-status/{id}`;
  - `GET /v2026/accounts` and `/v2026/entitlements` with `X-Total-Count` (reject a `sourceId` filter with 400);
  - `GET/PATCH …/schemas`;
  - `POST …/aggregate-agents`, `GET/PUT …/datasets/{id}`, `GET /v2026/machine-identities` (X-Total-Count);
  - `GET/POST/PUT …/provisioning-policies` and `GET/PUT …/correlation-config`.

  Also add switches `/_stub/entra/{secret_invalid,dataset_unavailable,slow_aggregation?minutes=N,delta_empty_peek,foreign_entra_source,existing_policy}` and extend `/_stub/reset`.
- [X] T029 [P] Add Entra scripts to `onboarding/agent/tests/fake_model/script.yaml` for the scenarios in quickstart.md §2: directory, secret in chat, service principals, AI agents with fallback, provisioning, long aggregation, extend-source, rotation. Each script is keyed by `connector_type: entra-id` and the triggering message.

**Checkpoint**: T005 is green, the AWS e2e passes unchanged, and an Entra session can be created on the stub (planned → available is done in US1).

---

## Phase 3: User Story 1 - IAM engineer onboards an Entra tenant for governance (Priority: P1) 🎯 MVP

**Goal**: Entra ID is available in the catalog. A directory-only session creates and configures the source with the
vaulted secret, then proves it in order: read accounts, Test Connection, entitlements, then accounts (full read),
with counts (FR-101–FR-104, FR-130–FR-133, FR-125, FR-137, FR-138).

**Independent Test**: On the stub, seed the secret through `PUT …/application-secret` as the owner. The IAM engineer
orders the connector. Expect:
- the plan runs create → configure → connection check → Test Connection → entitlements → accounts;
- the "What SailPoint now sees" card shows users and entitlements;
- the secret state ends `vault_deleted`;
- every Entra ISC call is under `/v2026/` (quickstart §2 scenario 1).

### Tests for User Story 1

- [X] T030 [P] [US1] Write `onboarding/agent/tests/unit/test_entra_tools.py` (respx, `V2026` table):
  - `configure_source` puts the vault value in the `/connectorAttributes/clientSecret` op; the action `request` shows `"clientSecret": "[vaulted]"`; the tool result has `secret_applied: true` and no value;
  - `start_aggregation` with `sequence: [entitlements, accounts]` runs them in that order, switches `deltaAggregationEnabled` off and restores it on success **and** on error;
  - `peek_accounts` uses the same full-read toggle;
  - `find_connector_sources` matches `domainName` to `tenant_domain`;
  - each write emits exactly one final `action` event with `summary`.
- [X] T031 [P] [US1] Write `onboarding/api/tests/integration/test_entra_directory.py` against the stub and scripted model:
  - the quickstart §2 scenario 1 flow;
  - `test_connection` ok with the secret `in_isc` deletes the vault copy;
  - `sessions.proof` holds users and entitlements;
  - a directory-only plan has no write permission and no directory role (SC-105, checked against `permissions/readonly.json` and plan.yaml).

### Implementation for User Story 1

- [X] T032 [P] [US1] Write the `entra-id` catalog entry in `onboarding/catalog/catalog.yaml` exactly as in `contracts/entra-playbook.md` "Catalog entry":
  - `status: available`, `badge: new`, `first_step_label: Entra app ready`;
  - capability key `enabled`: in the MVP, `service_principals`, `ai_agents` and `provisioning` ship as `enabled: false` (shown greyed as "coming soon");
  - `secret`, the 4 `capabilities` with `summary`, `tag`, `permissions` and `owner_summary`;
  - the `session_fields` from data-model.md: `source_name`, `source_owner`, `tenant_domain`, `app_name` (default `SailPoint ISC - {tenant}`), `capabilities`, `foundry_subscriptions` (`show_if: ai_agents`), `upn_domain` and `usage_location` (`show_if: provisioning`, default `TH`), `source_mode` (`new` | `extend`, default `new`), `client_id`;
  - `playbook: playbooks/entra-id`.
- [X] T033 [P] [US1] Write `onboarding/catalog/playbooks/entra-id/settings.yaml` per `contracts/entra-playbook.md`:
  - `connector_script: Microsoft-Entra`, `spec_id: b7e9374a-3c50-4b51-8880-e501f947bbf8`, `isc_api: v2026`, `experimental_paths`, `tenant_match_field: domainName`, `secret_fields: {clientSecret: application_secret}`, `full_read: {field: deltaAggregationEnabled}`, `creation_only`;
  - the `configure` entries, with the directory reference values from the skill's `assets/isc/feature-toggles.json` (readonly).
- [X] T034 [P] [US1] Write `onboarding/catalog/playbooks/entra-id/checks.yaml` per the contract: `tools`, `order`, `connection_check`, `test_connection` (`run_after: connection_check`), `aggregation` (`sequence: [entitlements, accounts]`, `wait_in_turn_seconds: 180`, `pending_after_minutes: 30`), `milestone_order`, `plan_step`.
- [X] T035 [P] [US1] Write `onboarding/catalog/playbooks/entra-id/plan.yaml` with the 23 steps of the contract table "plan.yaml" (`id`, `title`, `actor`, `kind`, `capability`, `on_extend`, `setup_step`, `milestone`).
- [X] T036 [US1] Implement secret injection in `configure_source` in `onboarding/agent/src/onboarding_agent/isc/tools.py`:
  - for each `settings.secret_fields` entry, when the session's `application_secret.state` is `received`, read the value with `vault.get_application_secret(provider)` and add the op;
  - mask it as `[vaulted]` in `request`;
  - return `secret_applied`;
  - with no secret received, refuse with "the Entra administrator hasn't put the secret in the secret field yet".

  Also apply the `configure` entries' `capability:` filter (only chosen capabilities). Set `summary` to `clientSecret: [vaulted] · N fields`.
- [X] T037 [US1] Implement `find_connector_sources` in `onboarding/agent/src/onboarding_agent/isc/tools.py`: `GET /v2026/sources?filters=connectorName eq "Microsoft Entra"&limit=250` (as in the skill's `references/isc-api.md`), keeping sources whose `connectorAttributes[tenant_match_field]` equals `tenant_domain`, returning `[{id, name, owner, created_in_this_session}]`. Add its `TOOL_SPECS` entry in `loop.py` (read, progress "looking for existing Entra sources in SailPoint…").
- [X] T037a [US1] Add the read tool `check_tenant_features` in `onboarding/agent/src/onboarding_agent/isc/tools.py`, with its TOOL_SPECS entry in `loop.py`:
  - confirm `GET /v2026/connectors/Microsoft-Entra`;
  - when `ai_agents` is chosen, probe `GET /v2026/machine-identities?limit=1` (experimental); a 403 or 404 means "machine identity features not licensed";
  - return `{connector: bool, machine_identities: bool}`.

  Add a rule to the Entra prompt block (T040): call it at session start; if a capability isn't possible, tell the IAM engineer and skip that capability's plan steps with `update_plan`, reason "not available on this ISC tenant". Add the tool to `checks.yaml` `tools` (T034).
- [X] T038 [US1] Implement the aggregation sequence and full read in `start_aggregation` and `peek_accounts` in `onboarding/agent/src/onboarding_agent/isc/tools.py` (research R5, R9):
  - read `checks.aggregation.sequence` (absent = today's AWS behaviour);
  - for Entra, emit separate `aggregate_entitlements` and `aggregate_accounts` actions, each polled;
  - wrap each in `full_read` (PATCH the field to `false`, then restore the previous value in `finally`; `request.delta_toggled: true`; summary `delta off → restored · N accounts`);
  - counts via `count()`: `entitlements`, `accounts`, plus `users` = accounts − `service_principals` (0 until US3).
- [X] T039 [US1] Handle new action outcomes in `onboarding/api/src/onboarding_api/chat/turns.py`:
  - `configure_source`/`apply_application_secret` ok with `secret_applied` → `secrets.service.mark_applied`;
  - `test_connection` ok while the secret is `in_isc` and the source is the session's → `delete_vault_copy` (FR-125);
  - store `action.summary`;
  - update `sessions.proof` (`users`, `service_principals`, `entitlements`, `ai_agents`, `ai_agents_state` `counted|tenant_limitation|not_chosen`, `updated_at`) from `response.counts`, and emit `proof.updated` to the IAM engineer only;
  - accept a `detail` event only for `name: client_id` with a GUID value, setting `details.client_id`.
- [X] T040 [US1] Add the Entra session rules to `onboarding/agent/src/onboarding_agent/prompts/en/system.md`, in the static block so they stay cached:
  - never ask for the secret in the chat; point to the secret field;
  - report the proof counts in the order users, service principals, entitlements, AI agents;
  - at session start, call `find_connector_sources` and name any existing source and its owner (US1-5).

  Rules that apply only to secret types are wrapped in a playbook-supplied block, so AWS's static prompt is unchanged (T005).
- [X] T041 [P] [US1] Update `onboarding/web/src/app/catalog/catalog.component.ts` to match the canvas Connector catalog artboard:
  - one card per available type, with the capability chips (`tag: writes` styled amber "· writes") and the `badge`;
  - the header count "N available · M planned" computed from the catalog;
  - the planned table without Entra.
- [X] T042 [US1] Rework `onboarding/web/src/app/session/new-session.component.ts` to match the canvas New Entra ID session artboard:
  - connector-type radio cards;
  - capability cards (directory checked and disabled);
  - fields with `show_if` inside their capability panel;
  - a "Source" radio for `source_mode`;
  - the side panel "The Entra administrator will be asked to" (owner_summary lines, an owner step count from the filtered plan, "N capabilities write") and "The agent will, on your order" (agent plan step titles), computed in the browser (research R15);
  - field errors and warnings from the 422 response.
- [X] T043 [P] [US1] Create `onboarding/web/src/app/shared/proof-counts.component.ts` ("What SailPoint now sees", IAM engineer only): tiles for users, service principals, entitlements and AI agents. AI agents shows "Start it in ISC" for `tenant_limitation` and is hidden for `not_chosen`. It is fed by session GET `proof` and `proof.updated`. Place it at the top of the IAM side column in `onboarding/web/src/app/session/session-screen.component.ts`.
- [X] T044 [P] [US1] Create `onboarding/web/src/app/shared/secret-status.component.ts` (IAM engineer side card, metadata only): provided by and at, in SailPoint at, vault copy deleted at, expires, plus the 30-day warning when `expires_soon`. It is fed by `secret.updated`. Place it in the IAM side column.
- [X] T045 [US1] Show `action.summary` under the action name in the SailPoint actions list and the action dialog in `onboarding/web/src/app/shared/action-dialog.component.ts` and `session-screen.component.ts`. Add the `tenant_limitation` outcome style (amber lock icon).
- [X] T046 [US1] Write `onboarding/web/e2e/entra-onboarding.spec.ts` (two browsers, stub, scripted model): quickstart §2 scenario 1 plus the catalog and New-session checks of scenario 11.

**Checkpoint**: The Entra directory-only onboarding works end to end on the stub; the AWS e2e is unchanged.

---

## Phase 4: User Story 2 - Entra administrator prepares the tenant from the agent's instructions (Priority: P1)

**Goal**: The administrator gets ordered, filled-in, labelled steps for the chosen capabilities, and the agent checks
each pasted output. The secret goes only through the secret field. A secret pasted in chat is masked, flagged as
exposed and replaced (FR-110–FR-113, FR-120–FR-124, US2-1…7).

**Independent Test**: Run a directory-only session on the stub as the administrator.
- Steps 1–10 appear in order, with values filled in.
- A pasted consent output missing Application.Read.All is flagged.
- A GUID in the secret field is rejected with the exact message.
- A `Q~` secret in chat shows `[masked]`, the red exposed note appears, and the agent asks for a new one.
- The search for the seeded secret finds nothing (quickstart §2 scenarios 1, 2, 9).

### Tests for User Story 2

- [X] T047 [P] [US2] Write `onboarding/api/tests/integration/test_entra_secret_exposed.py`: an owner message containing a `Q~` secret is stored masked. A `system_note` `{tone: danger, code: secret_exposed}` follows it in the owner's thread, with the exact text "A client secret was masked before it was saved, shown or sent to the agent. Treat it as exposed: delete it in Entra and create a new one." The next turn payload has `secret_exposed: true`, audit `secret_exposed_in_chat` is written, and no model call is made for the note.
- [X] T048 [P] [US2] Write `onboarding/web/e2e/entra-secret-leak.spec.ts` (SC-102). Submit a known test secret via the field, run the flow, then search MongoDB (all collections), the SSE journal, API and agent logs, attachments and the stub's request log. Expect zero hits for the value and its fingerprint, except the stub's PATCH body for `clientSecret`, which must equal the submitted value exactly.

### Implementation for User Story 2

- [X] T049 [P] [US2] Write `onboarding/catalog/playbooks/entra-id/setup.md`, steps 1–11 per `contracts/entra-playbook.md` "setup.md", ported from `scripts/entra-setup.sh` and `references/entra-permissions.md`:
  - each step has a title, "read-only" or "change", `az` commands with `{placeholders}`, and **Expect**;
  - steps 4–7 render the permission names from `permissions/*.json` (resolved by name, never GUIDs);
  - step 10 writes the secret to a `umask 077` file and tells the administrator to copy the Value into the secret field and delete the file, never printing it or pasting it in chat (FR-113);
  - step 9 is the appRoleAssignments read the agent checks against the expected permission list.
- [X] T050 [P] [US2] Write `onboarding/catalog/playbooks/entra-id/collisions.md` per the contract: a same-name app registration not tagged `sailpoint-isc-entra-connector` (choose another name, or confirm it and add only missing permissions); an existing source for the tenant (name, owner, offer extend-source); the naming rule `SailPoint ISC - {tenant}`.
- [X] T051 [P] [US2] Write `onboarding/catalog/playbooks/entra-id/suggestions.yaml`. It has 001's states plus `waiting_for_secret` (owner: "I've put the new secret in the field", "Where is the Value column?", "What is left to do?") and `tenant_limitation` (IAM engineer: "Done, I started it in ISC", "Show the leaver actions to review", "What is left to do?"). Include at least 3 `any` items per role, and no `kind: order` for the owner.
- [X] T052 [US2] Implement the exposed-secret note in `onboarding/api/src/onboarding_api/chat/messages.py` (research R17): when `masking.contains_entra_secret()` is true for an application owner's message in a session whose type has `secret`, write the T047 system note right after it, set `secret_exposed` for the next turn, write audit `secret_exposed_in_chat`, and set the owner's suggestion state to `waiting_for_secret`. Add `tone` and `code` to system notes in `onboarding/api/src/onboarding_api/chat/events.py`.
- [X] T053 [US2] Extend the screenshot check prompt in `onboarding/agent/src/onboarding_agent/loop.py` `secret_check()`: an Entra "Certificates & secrets" page whose Value column shows characters is held (FR-123). This adds no model call.
- [X] T054 [US2] Implement `apply_application_secret` in `onboarding/agent/src/onboarding_agent/isc/tools.py`: PATCH only the `secret_fields` from the vault onto the session's source; action with summary `clientSecret: [vaulted]`. Add its TOOL_SPECS entry in `loop.py`. Owner gate: offer it on an owner turn only when `check_order` is set and `trigger == "secret_submitted"`, then rerun `peek_accounts` and `test_connection` (research R2).
- [X] T055 [US2] Queue a turn with `trigger: secret_submitted` in `onboarding/api/src/onboarding_api/secrets/routes.py` after a successful PUT, when a standing `check_order` exists and the source exists. Otherwise post a system note in the IAM engineer's thread: "A new secret is waiting; order 'apply the new secret'." (FR-122)
- [X] T056 [P] [US2] Create `onboarding/web/src/app/shared/secret-field.component.ts` to match the canvas Entra administrator artboard (research R16):
  - a card above the conversation with a 2 px owner-blue border, titled "Secret field";
  - a password input "Client secret Value" (`autocomplete="off"`, never bound to a stored signal), a date input "Expires", and a button "Send to the vault";
  - the input is cleared before the request resolves;
  - the 422 messages inline in `role="alert"`, with the help line "Certificates & secrets → the Value column, shown only once";
  - a 4-step status strip: Not received · Received · In SailPoint · Vault copy deleted;
  - shown while `provide_secret` isn't done, on `secret.needed`, or when `expires_soon`; otherwise only the strip.
- [X] T057 [US2] Place the secret field card above the owner's conversation in `onboarding/web/src/app/session/session-screen.component.ts`, only for the application owner and only when the catalog entry has `secret`. Render system-note tones (`danger` red, `info` blue, `success` green) in `onboarding/web/src/app/shared/thread.component.ts`. Update the composer hint for secret types to "Client secrets and tokens are masked here. A screenshot that shows a secret Value is held. The secret goes in the secret field, never the chat." in `onboarding/web/src/app/shared/composer.component.ts`.
- [X] T058a [P] [US2] Create `onboarding/agent/tests/evals/entra_setup_checks/cases.yaml` (FR-112) with 4 cases:
  - an appRoleAssignments output missing Application.Read.All → the agent names it and repeats step 8;
  - a role list without Global Administrator or Privileged Role Administrator → it names the needed role before any change step;
  - an `az account show` on another tenant → it flags the wrong tenant;
  - correct output → it accepts and moves to the next step.

  At 3 runs that is about 60 calls (~$0.25), run with `--suite entra_setup_checks` (needs T076).
- [X] T058 [US2] Externalise every new English string from T041–T057 in `onboarding/web/src/locale/messages.xlf` (`@angular/localize`, as in 001).

**Checkpoint**: US1 + US2 make a complete directory-only Entra onboarding with no secret exposure; this is the MVP.

---

## Phase 5: User Story 1 (FR-139) - Long-running aggregations

**Goal**: An aggregation still running after the in-turn wait is followed by the API without a model call. It shows
*pending · N min* after 30 minutes, posts its result to the IAM engineer's thread, and continues the proof as the
ordering IAM engineer.

**Independent Test**: With `/_stub/entra/slow_aggregation?minutes=35` and a test clock, the plan step shows
"pending · 30 min". The green "finished after N min" note arrives with nobody's message, and the next proof step runs
as the check-order IAM engineer (quickstart §2 scenario 6).

- [X] T059 [P] [US1] Write `onboarding/api/tests/unit/test_followups.py`, covering:
  - running → following → done/failed;
  - at most 3 `following` per session;
  - restart rescan;
  - `pending_since` set at 30 min;
  - the continuation turn queued with `trigger: followup` and `ordered_by` = the check-order IAM engineer;
  - with no check order, only the note is posted.
- [X] T060 [US1] Add `mode: task_check` to `onboarding/agent/src/onboarding_agent/main.py`: input `{tenant, isc_api, task_ids}`, output one event `{type: task_check, tasks: [{id, completion_status, messages}]}`, no model call, masked errors.
- [X] T061 [US1] Make the aggregation tools in `onboarding/agent/src/onboarding_agent/isc/tools.py` wait in the turn only up to `checks.aggregation.wait_in_turn_seconds`. Leave AWS on today's `poll_limit` × `poll_seconds` when the key is absent. When the wait runs out, emit the action as `running` with `follow: true` and `next_step`, and return `{running: true, task_ids}`.
- [X] T062 [US1] Create `onboarding/api/src/onboarding_api/chat/followups.py` (data-model.md Followup):
  - on an action `follow: true`, insert `{action_ref, kind, task_ids, plan_step, next_step, ordered_by, started_at, last_checked_at, state: following}`;
  - a background loop started in `main.py` lifespan invokes `task_check` every 60 s;
  - at 30 min, set PlanStep `pending_since`, write a `system_note` `{tone: info, code: followup_pending}` "… is pending · N min …" and emit `followup.updated` each minute;
  - on completion, complete the action, post `{tone: success|danger, code: followup_finished}` with counts, set the plan step, update `proof`, and queue the continuation turn per T059.
- [X] T063 [US1] Show "pending · N min" on plan steps with `pending_since` ≥ 30 min in `onboarding/web/src/app/shared/plan-panel.component.ts`, refreshed by `followup.updated`.

---

## Phase 6: User Story 3 - Service principals aggregated as accounts (Priority: P2)

**Goal**: With the service principals capability, the administrator gets the extra read permissions; the agent adds
the missing SP attributes to the account model, enables the SP settings, and reports users and service principals
separately (FR-111, FR-132, US3-1…3).

**Independent Test**: With service principals chosen and the stub seeded with 2 of the SP attributes already present,
only the missing ones are PATCHed. The account aggregation is a full read, and the report shows users and service
principals separately (quickstart §2 scenario 3).

- [X] T064 [P] [US3] Add tests to `onboarding/agent/tests/unit/test_entra_tools.py`: `ensure_schema_attributes` PATCHes only the attributes from `assets/account-schema-spn-attributes.json` that are missing from the account schema, and never `replace` or `remove`; the service-principal count is filtered on the SP type attribute; `users` = total − service principals.
- [X] T065 [US3] Implement `ensure_schema_attributes(capability)` in `onboarding/agent/src/onboarding_agent/isc/tools.py`:
  - GET schemas, find the account schema, compute the missing names from `checks.schema[capability]`;
  - send one PATCH of `{"op":"add","path":"/attributes/-","value":…}` ops;
  - action summary `N attributes added`.

  Add its TOOL_SPECS entry in `loop.py`.
- [X] T066 [US3] Add the `service_principals` `configure` entries to `onboarding/catalog/playbooks/entra-id/settings.yaml` from the skill's `feature-toggles.json` machine-identity block:
  - `manageAzureServicePrincipalAsAccount: true`;
  - `spnAccountFilter: "servicePrincipalType eq 'Application'"`;
  - `spnManageDirectoryRole`, `spnManageAppRoles`, `spnManageGroups`, `spnManageRBACRoles`, `manageAdminConsentedPermissions` and `manageCustomSecurityAttributesForServicePrincipals` all `true`;
  - `spnManageAzurePIM` and `spnManageAzureADPIM` both `false`;
  - all with `capability: service_principals`.

  Add `schema.service_principals: assets/account-schema-spn-attributes.json` to `checks.yaml`.
- [X] T067 [US3] Count service principals in `start_aggregation` in `onboarding/agent/src/onboarding_agent/isc/tools.py` when `service_principals` is chosen: `count(accounts, source.id eq … and <SP type attribute> …)`, with the attribute named in `checks.schema`. Fill `counts.service_principals` and `counts.users`.
- [X] T068 [US3] Extend `onboarding/api/tests/integration/test_entra_directory.py` with the service principals scenario (quickstart §2 scenario 3). Then set `enabled: true` for `service_principals` in `onboarding/catalog/catalog.yaml`.

---

## Phase 7: User Story 4 - AI agents from Azure AI Foundry (Priority: P2)

**Goal**: With AI agents chosen and subscriptions named, the administrator gets the Foundry steps. The agent turns
Foundry on, aggregates the dataset (or reports the tenant limitation with the ISC path), turns on the schedule and
reports the AI-agent count. Copilot Studio and Agent 365 stay off (FR-134, FR-135, US4-1…4).

**Independent Test**: With `_stub/entra/dataset_unavailable`, the Foundry step is `blocked` with the UI path and the
AI agents tile shows "Start it in ISC". After "done", the count is read and the schedule is on. Without the switch,
the dataset aggregates directly (quickstart §2 scenario 4).

- [X] T069 [P] [US4] Add tests to `onboarding/agent/tests/unit/test_entra_tools.py`:
  - `aggregate_datasets` sends `{"datasetIds":["azure:foundry"],"disableOptimization":false}` with the experimental header;
  - a 404 body containing "endpoint is unavailable" returns `result: tenant_limitation` with `ui_path` rendered with `{source_name}`, and doesn't fail;
  - `set_dataset_schedule` does GET then PUT with `aggregationEnabled: true`;
  - the AI-agent count uses `machine-identities` with `datasetId eq "azure:foundry"`.
- [X] T070 [US4] Implement `aggregate_datasets` and `set_dataset_schedule(dataset_id, on)` in `onboarding/agent/src/onboarding_agent/isc/tools.py`:
  - read the dataset ids from the source's toggles (`checks.datasets`);
  - handle `unavailable_signature` as above;
  - summaries `tenant limitation · 404 endpoint unavailable` and `N AI agents · schedule on`;
  - counts `ai_agents`.

  Add both TOOL_SPECS entries in `loop.py`, and add `aggregate_datasets` to the owner-rerun check tools.
- [X] T071 [US4] Add the `ai_agents` `configure` entries to `onboarding/catalog/playbooks/entra-id/settings.yaml`: `enableAIFoundryAgent: true`, `foundryAggregateLatestVersionOnly: true`, `enableCopilotAIAgent: false`, `enableMicrosoftAgent365: false`, all with `capability: ai_agents`. Add `datasets.ai_agents` and `unavailable_signature` to `checks.yaml`. The skill's reference has Copilot on; this flow keeps it off (contract note).
- [X] T072 [US4] Handle `result: tenant_limitation` in `onboarding/api/src/onboarding_api/chat/turns.py`: set the plan step `blocked` with reason "start it in ISC (tenant limitation)", set the waiting banner on the IAM engineer with "start the Foundry aggregation in ISC (plan step N), then say \"done\"", set `proof.ai_agents_state: tenant_limitation`, and set the IAM engineer's suggestion state to `tenant_limitation`. Don't count it as a failed onboarding (FR-135).
- [X] T073 [US4] Extend `onboarding/api/tests/integration/test_entra_directory.py` with both AI agents paths (quickstart §2 scenario 4). Then set `enabled: true` for `ai_agents` in `onboarding/catalog/catalog.yaml`.

---

## Phase 8: User Story 6 - Troubleshoot Entra failures (Priority: P2)

**Goal**: The agent recognises E1–E11 from text or screenshots and gives cause, side, a read-only confirmation and
the fix (FR-140, SC-103).

**Independent Test**: The scripted e2e covers E1 (`/_stub/entra/secret_invalid` → a new secret is requested through
the field) and E8 (`delta_empty_peek` handled, not a failure). The opt-in eval suite `entra_failures` reaches ≥ 9/10
per case at the gate.

- [X] T074 [P] [US6] Write `onboarding/catalog/playbooks/entra-id/failures.md` with E1–E11 from `contracts/entra-playbook.md`, ported from the skill's `references/troubleshooting.md`. Each entry has signature (text and on-screen look), side, cause, a read-only confirm step (e.g. setup step 9 or `az rest …/appRoleAssignments`), the fix, and whether to retry once. For E1 and E2 the fix is "a new secret through the secret field" plus a `secret_needed` event.
- [X] T075 [US6] Emit `{type: secret_needed, reason}` from the agent on E1/E2 in `onboarding/agent/src/onboarding_agent/tools/session.py`, as a new session tool `request_new_secret(reason)`. In `onboarding/api/src/onboarding_api/chat/turns.py`, handle it by emitting `secret.needed`, showing the owner's secret field, and posting a system note in the owner's thread.
- [X] T076 [P] [US6] Add `--suite` to `onboarding/agent/tests/evals/run_evals.py` (default `aws_saas_failures`), with the fingerprint and gate state kept per suite so an Entra change never re-runs the AWS gate. The fingerprint for a suite hashes only `playbooks/<type>/` of that suite's connector type (from `cases.yaml` `connector_type`), plus `prompts/` and `loop.py`. `--estimate` prints the calls and cost per suite (Constitution IV).
- [X] T077 [P] [US6] Create `onboarding/agent/tests/evals/entra_failures/cases.yaml`: 11 cases E1–E11, each with a text sample, the expected cause keyword, and the side. Add fictional-tenant screenshots `E1.png`, `E5.png`, `E7.png`, `E11.png` from the stub UI or redrawn (no real tenant names). Expect ~330 calls (~$1.30) at 3 runs, text and screenshot (the runner prints it).
- [X] T078 [US6] Extend `onboarding/web/e2e/entra-onboarding.spec.ts` with the E1 and E8 scripted cases.

---

## Phase 9: User Story 5 - Provisioning for joiners, movers and leavers (Priority: P3)

**Goal**: Provisioning requires the accepted warning. The administrator gets the write permissions and the User
Administrator role. The agent sets the CREATE provisioning policy and correlation (keeping existing ones), proves it
read-only, and prepares leaver actions for review without writing to the directory (FR-103, FR-136, US5-1…4).

**Independent Test**: Saving with provisioning is refused until the warning is accepted. The policy and correlation
are set, and a pre-existing stub policy is kept. The stub records no account create. Lifecycle actions appear only
as a review block (quickstart §2 scenario 5).

- [X] T079 [P] [US5] Add tests to `onboarding/agent/tests/unit/test_entra_tools.py`:
  - `set_provisioning_policy` POSTs the rendered `assets/provisioning-policy-create.json` when no CREATE policy exists;
  - it keeps an existing one unless `replace=true`;
  - `set_correlation` PUTs `assets/correlation-config.json`;
  - `read_source_setup` reports `{toggles, schema_attributes, provisioning_policy: bool, correlation: bool}`;
  - no tool calls an account-create path.
- [X] T080 [US5] Implement `set_provisioning_policy(replace=false)`, `set_correlation` and `read_source_setup` in `onboarding/agent/src/onboarding_agent/isc/tools.py`, driven by `checks.provisioning`. Add their TOOL_SPECS entries in `loop.py`.
- [X] T081 [US5] Require `accept_warnings` on POST `/sessions` in `onboarding/api/src/onboarding_api/sessions/routes.py` for each chosen capability with a `warning`: 422 `warnings_accepted: "accept the provisioning warning"`. Store `details.warnings_accepted = [{capability, user_id, at}]` and write audit `provisioning_warning_accepted`.
- [X] T082 [US5] Add the provisioning warning panel to `onboarding/web/src/app/session/new-session.component.ts` per the canvas: the amber box with the warning text rendered with `{tenant_domain}`, the "I understand and accept this for the session" checkbox, the fields "Domain for new accounts" and "Usage location", and **Start session disabled** until it's ticked, with the hint "Accept the provisioning warning to continue."
- [X] T083 [US5] Add the provisioning parts to `onboarding/catalog/playbooks/entra-id/setup.md`: step 7 (write permissions from `permissions/provisioning.json` and the User Administrator role assignment) and step 11 (a read-only directory role assignment check, expecting User Administrator). Add `checks.provisioning` to `checks.yaml`. Add a playbook prompt rule: render the lifecycle actions from `assets/lifecycle-state-account-actions.patch.json` as a review block for the IAM engineer, never applied, and say the first real joiner is the first live write.
- [X] T084 [US5] Extend `onboarding/api/tests/integration/test_entra_directory.py` with quickstart §2 scenario 5, asserting the stub's request log has no account create. Then set `enabled: true` for `provisioning` in `onboarding/catalog/catalog.yaml`.

---

## Phase 10: User Story 1 (FR-105) - Extend an existing Entra source

**Goal**: A session targets an existing Entra source owned by the source owner and adds capabilities. Only the new
capabilities' steps remain, no new secret is asked for, and the source is never deleted.

**Independent Test**: A second session for the same tenant with `source_mode: extend` and service principals. The
agent names the existing source, `adopt_source` binds it, and the create, secret and registration steps are
skipped. Only the SP steps run, and `delete_session_source` is refused (quickstart §2 scenario 7).

- [X] T085 [P] [US1] Add tests to `onboarding/agent/tests/unit/test_entra_tools.py`: `adopt_source` refuses another owner, another connector or another `domainName` (`/_stub/entra/foreign_entra_source`); on success it emits `{type: source, id, name, adopted: true}`; `delete_session_source` refuses an adopted source; `configure_source` in extend mode sends only the added capabilities' entries.
- [X] T086 [US1] Implement `adopt_source(source_id)` in `onboarding/agent/src/onboarding_agent/isc/tools.py` (research R7) and add its TOOL_SPECS entry. Make `delete_session_source` refuse when `session.source.adopted`.
- [X] T087 [US1] Handle `source.adopted` in `onboarding/api/src/onboarding_api/chat/turns.py`: set `sessions.mode = "extend"` and `source.adopted = true`, call `plan.skip_on_extend()`, and emit `session.mode`.
- [X] T088 [US1] Add the extend-source rule to the Entra playbook prompt text (`onboarding/catalog/playbooks/entra-id/collisions.md`). With `source_mode: extend`, call `find_connector_sources` and propose `adopt_source` for the match (ask which one if several). Skip capability steps already on the source using `read_source_setup` and `update_plan`. Run entitlements and accounts again when the account model changed.

---

## Phase 11: Polish & cross-cutting

- [X] T089 [P] Add an IAM-screen owner-thread toggle in `onboarding/web/src/app/session/session-screen.component.ts` (research R19): side by side by default, or collapsed to the bar "Entra administrator ↔ Agent · view only · N new · Show side by side". The choice is kept in `localStorage` inside try/catch, and the bar counts owner messages since it was collapsed.
- [X] T090 [P] Add `--scenario entra-directory` to `onboarding/deploy/scripts/smoke.py` (scripted model, stub ISC).
- [X] T091 [P] Document the Entra type in `onboarding/README.md` and `docs/DEMO-GUIDE.md`:
  - what the Entra administrator needs (Global Administrator or Privileged Role Administrator);
  - the secret field;
  - the v2026 note;
  - which commands are paid: the evals estimate and the real-tenant run at about $0.50–$1.
- [X] T092 Run the full free suite: agent and API `uv run pytest`, `make onboarding-e2e`, `make onboarding-leak-scan`. Confirm T005 (AWS unchanged) is still green. Then run `run_evals.py --suite aws_saas_failures --estimate`, state the estimate, and run the AWS gate once only if its fingerprint changed (expected after T012, T040 and T053).
- [X] T093 Run quickstart §4 against a test Entra tenant and a test ISC tenant (opt-in, paid; state the estimate first), including provisioning (001 FR-028). Record the counts against `az ad user list` and `az ad sp list`, and the CloudTrail `GetResourceApiKey` and `DeleteApiKeyCredentialProvider` entries. Record the SC-101 times too: the administrator's time from first instruction to secret received (target ≤ 15 min), the session time from start to proof passed (target ≤ 30 min), and the account count (the target applies at ≤ 5,000). Write them in `specs/002-entra-saas-connector/quickstart.md` §4 as the observed result. Then clean up the test source, app registration and role assignments.

---

## Dependencies & Execution Order

- **Setup (T001–T004)** → **Foundational (T005–T029)** → stories. T005 is written first and stays green throughout.
- **US1 (T030–T046)** needs Foundational. **US2 (T047–T058)** needs Foundational and shares the catalog entry (T032), so start it after T032. US1 and US2 together are the MVP.
- **FR-139 follow-ups (T059–T063)** need US1 (T038).
- **US3 (T064–T068)**, **US4 (T069–T073)** and **US6 (T074–T078)** each need US1. They are independent of each other and can run in parallel; T066 and T071 touch the same `settings.yaml`, so make those two edits one after the other.
- **US5 (T079–T084)** needs US1; T082 touches the same form file as T042.
- **Extend-source (T085–T088)** needs US1 and US3, because its test adds service principals.
- **Polish (T089–T093)** comes last; T093 needs everything.

Within a story: tests, then playbook data, then agent tools, then API handling, then web, then e2e.

## Parallel examples

- **Foundational:** T005, T010, T018, T024, T026, T027 and T029 are separate files and can run together once their subject task exists (T010 after T006/T007; T018 after T014/T015; T024 after T019–T021).
- **US1:** T030 and T031 (tests); T032, T033, T034 and T035 (four playbook files); T041, T043 and T044 (three web files).
- **US2:** T047 and T048 (tests); T049, T050 and T051 (playbook files); T056 (web) alongside T052–T055 (API and agent).
- **After US1:** US3, US4 and US6 by three developers, with the `settings.yaml` edits serialised.

## Implementation strategy

1. **MVP = Setup + Foundational + US1 + US2.** This is a directory-only Entra onboarding with the vaulted secret, the canvas screens for the catalog, the new session, the Entra administrator and the IAM engineer counts, and the leak test. Demo it on the stub, then on a real test tenant (T093 directory part).
2. **Add FR-139** before trying a tenant above about 2,000 accounts.
3. **Add US3 and US4** (machine identities: the high demo value), then **US6** (evals, opt-in).
4. **Add US5** last. It needs the real-tenant check before release (001 FR-028).
5. **Extend-source** once US3 exists.

Each increment keeps the AWS SaaS suites green (SC-106).

## Implementation notes (2026-10-08)

Done: every task. T093, the real-tenant run, was done on 2026-10-08 with all four capabilities. Results and the bugs it
found are in quickstart §4. The test source, the Entra app (soft-deleted, in the Entra recycle bin for 30 days), its
Azure role assignments and the test accounts were removed afterwards.

Where the code differs from the task text:
- **Tests**:
  - The secret, exposed-secret, follow-up and capability tests (T024, T031, T047, T059, T068, T073, T084) live in
    `onboarding/api/tests/integration/test_entra_directory.py`.
  - The tools are also run against the stub in process (`test_entra_stub.py`).
  - Whole scripted turns run in `onboarding/agent/tests/unit/test_entra_scripted.py`, which needs FastAPI as an agent
    dev dependency.
- **The exposed-secret note** (T052) is written in `chat/routes.py`, next to the message it follows, not in
  `chat/messages.py`. When a turn starts from an exposed secret, the agent's `request_new_secret` posts no second note.
- **The slow-aggregation stub switch** (T028) is `/_stub/entra/slow_aggregation?polls=N` (N "still running" reads), not
  minutes, so tests don't depend on the clock.
- **Extra agent tools**:
  - `record_application_id`: how the client ID from the administrator's output reaches `details.client_id`.
  - `count_ai_agents`: the re-check after the ISC interface fallback.
  - `lifecycle_review`: the leaver actions for review.
- **The Entra prompt rules** live in a playbook file, `catalog/playbooks/entra-id/prompt.md`, appended to the static
  prompt only for playbooks that have one. The AWS prompt is byte-identical (T005).
- **Machine Account Classification** (added after the canvas, from a UI-configured source): with service principals,
  `set_machine_classification` turns it on (`CRITERIA`: managed identities, service principals Application/Legacy) and
  runs Process Classification; the skill gained `isc-source.sh classification` with the same asset.
- **The catalog API** returns a `plan_preview` per capability type for the new-session "what happens" panel (R15).
- **Eval cost** is higher than first estimated. `--suite entra_failures` is about 330 calls (about $1.30) at 3 runs for
  text and screenshot. The screenshots are rendered from the case text on the first (paid) run. None was run here.
- **The AWS eval gate** fingerprint changes once (`loop.py`), as planned. The gate hasn't been re-run; run
  `run_evals.py --estimate`, then `--gate`, when you want it.
