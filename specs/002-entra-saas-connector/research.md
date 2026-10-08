# Research: Microsoft Entra ID SaaS connector (spec 002)

Phase 0 decisions. Numbering is local to this feature (R1–R14). References like "001 R4" point to
[001 research](../001-isc-onboarding-agent/research.md). Source of truth for Entra behaviour: the skill
`.claude/skills/sailpoint-isc-entra-connector` (verified live 2026-10-08, commits `9b53746`, `b71c53c`).

## R1. Where the application secret is vaulted (FR-120, FR-125)

- **Decision**: Store the secret as an **AgentCore Identity API-key credential provider**, one per session, named
  `onboarding-entra-<session_id>`.
  - The session API creates it with `create_api_key_credential_provider(name, apiKey)` and deletes it with
    `delete_api_key_credential_provider`.
  - The agent reads it with `IdentityClient.get_api_key(provider_name, agent_identity_token)`, using the same
    workload token it already uses for the ISC M2M token (001 R4).
  - MongoDB stores only `{provider, state, provided_by, provided_at, expires_on, replaced_at, vault_deleted_at}`.
- **Rationale**:
  - It is the same vault and access path as the ISC PAT (001 R4), on the list in constitution I.
  - The API already holds control-plane rights on that token vault.
  - There is one deletion call, and CloudTrail logs every read.
- **Fallback** (same as 001 R4's open item): if API-key providers are unavailable to this runtime, use a Secrets
  Manager secret `onboarding-entra-<session_id>` that the agent role reads. Only `vault.py` and `secrets/store.py`
  change.
- **Local stub mode** (`CREDENTIAL_STORE=discard`):
  - The API keeps the value, and its SHA-256 fingerprint, in a local-only in-memory store (`LocalApiKeyStore`).
  - The local agent reads it through `GET /_local/apikey/{name}`. That route exists only when
    `CREDENTIAL_STORE=discard` and `ONBOARDING_ISC_BASE_URL` points to the stub, and it is bound to the
    cluster-internal service.
  - The API refuses to start with this store on the cluster.
  - The stub's PATCH therefore receives the exact submitted value, so the leak test can prove the
    vault-to-ISC path.
- **Alternatives considered**:
  - A Kubernetes Secret read by the API, which then calls ISC itself: the API has no ISC credential (001 R4), and
    giving it one adds a second ISC-writing component.
  - Passing the value in the invoke payload: it would be on the wire, in AgentCore request logs, and one bug away
    from the model's context.
  - MongoDB, encrypted: rejected in 001 R4.

## R2. Getting the secret into the ISC source without the model seeing it (FR-120, FR-122)

- **Decision**:
  - `settings.yaml` declares `secret_fields: {clientSecret: application_secret}`.
  - `configure_source` and the new `apply_application_secret` tool resolve that value inside tool code from the
    vault (R1) and add the `/connectorAttributes/clientSecret` op. The model can't name the secret as a tool input.
  - The action `request` shows `"clientSecret": "[vaulted]"`, and the tool result returns only `secret_applied: true`.
  - The turn payload carries the secret's **metadata** (`state`, `expires_on`, `provided_by`), never the value.
- **Rotation**:
  - The owner submits a new value through the secret field. The API replaces the provider and sets state
    `received`.
  - With a standing check order (001 FR-016a), that submission triggers a turn whose owner gate adds
    `apply_application_secret` to the check tools; the agent applies the secret, then reruns peek and Test
    Connection.
  - Without a standing check order, the agent tells the IAM engineer a new secret is waiting, and it is applied on
    their order. Owners still can't make other ISC changes (001 FR-019).
- **Vault deletion**:
  - The API watches the `test_connection` action event.
  - It deletes the provider when that action is `ok`, the session's secret state is `in_isc`, and the source is the
    session's.
  - It then sets `vault_deleted_at` and writes audit `application_secret_vault_deleted`.
  - A later need for the secret (rebuild, or a sign-in failure) → the agent asks for a new secret through the field.
- **Alternatives considered**: a model-visible placeholder such as `{{secret}}` that the tool substitutes. It is
  rejected because it lets the model place the secret in any field, including a description or a name.

## R3. Recognising Entra secrets (FR-121, FR-123, FR-124)

- **Decision**:
  - **Masker**: add the pattern for Entra client secret Values to `masking.py`. Current Values are 40 characters:
    three characters, a digit, the marker `Q~`, then 34 characters from `[A-Za-z0-9_.~-]`. The pattern is
    `(?<![A-Za-z0-9_.~-])[A-Za-z0-9_.-]{3}\dQ~[A-Za-z0-9_.~-]{31,34}(?![A-Za-z0-9_.~-])`. Older 32–44-character
    Values without `Q~` are caught by the existing generic `secret: <value>` rule in key/value context. A bare
    one is not, and that is accepted, because the secret field is the way in.
  - **Secret field validation**:
    - Reject a GUID (`^[0-9a-f]{8}-…$`, case-insensitive) with "this is the secret's ID, not its Value".
    - Reject values outside 16–128 characters or containing whitespace.
    - Require `expires_on` with today < date ≤ today + 2 years (FR-121).
  - **Screenshots**: the 001 `secret_check` prompt (FR-026a) adds "an Entra *Certificates & secrets* page with a
    Value column showing characters". No new model call; it is the same check.
  - **Leak scan**: `leak-scan.sh` adds the pattern. The Entra end-to-end test seeds a known test secret and asserts
    that its fingerprint and its text appear nowhere in MongoDB, events, logs or the stub's request log, except the
    one stub PATCH that is expected to contain it.
- **Rationale**: the `Q~` marker exists so secret scanners can find these Values. A GUID is the most common
  mistake (AADSTS7000215 in the skill's troubleshooting).
- **Alternatives considered**: a bare 40-character detector without the marker. It is rejected because it masks
  ordinary identifiers and base64 output that the administrator needs to see (001 FR-027).

## R4. ISC API v2026 for Entra only (FR-138, SC-106)

- **Decision**:
  - `settings.yaml` has `isc_api: v2026` and `experimental_paths` (regexes):
    - `/aggregate-agents$`
    - `/datasets(/|$)`
    - `^/v2026/machine-identities`
  - A new `isc/paths.py` maps logical operations (`sources`, `source`, `peek`, `test`, `load_accounts`,
    `load_entitlements`, `task`, `accounts`, `entitlements`, `connector`, `connector_form`, `public_identities`,
    `schemas`, `provisioning_policies`, `correlation`, `datasets`, `aggregate_agents`, `machine_identities`,
    `tenant`) to paths. There are two tables:
    - `legacy`: today's beta/v3 paths, byte-identical.
    - `v2026`.
  - `IscClient(experimental=None)` keeps today's behaviour: the header on every call. With a list, it sends the
    header only when the path matches.
  - `count(path, filters)` reads `X-Total-Count` with `count=true&limit=1`; the v2026 filter is `source.id eq "…"`,
    because `sourceId` returns 400.
  - Bodies follow the skill's `references/isc-api.md`:
    - `load-accounts`: multipart `disableOptimization=true`, task id at `.task.id`.
    - `load-entitlements`: multipart with no fields, task id at `.id`.
    - `aggregate-agents`: `{"datasetIds":[…],"disableOptimization":false}`.
  - `get_tenant_external_id` isn't needed by Entra (no External ID). The tenant check stays on beta for every type,
    because it belongs to the tenant, not the type.
- **Rationale**: the user asked for v2026 for Entra and an unchanged AWS type. A path table keeps one code path
  per operation and makes AWS's calls provably unchanged: a unit test snapshots the legacy table.
- **Alternatives considered**: moving AWS to v2026 at the same time. That is out of scope (spec assumptions) and it
  would invalidate the AWS evals and live verification.

## R5. Generic tools, opted into per playbook (FR-130–FR-136; 001 FR-030, SC-009)

- **Decision**:
  - `checks.yaml` gets a `tools:` list. The tools offered to the model are that list (the 001 tool set when the key
    is missing) intersected with the role gate. New tools, all generic and driven by playbook data:

    | Tool | Kind | Driven by |
    |---|---|---|
    | `find_connector_sources` | read | `settings.connector_script`, `settings.tenant_match_field` (`domainName`): lists sources on the connector whose tenant field equals the session's tenant, with owner (FR-130, US1-5) |
    | `adopt_source(source_id)` | write-gated bind, no ISC change | checks owner == session source owner and the connector script; binds the session's source (R7) |
    | `read_source_setup` | read | returns the source's capability toggles, schema attribute names, provisioning policy and correlation presence (provisioning proof R11, extend-source R7) |
    | `ensure_schema_attributes(capability)` | write | `assets/account-schema-spn-attributes.json`: PATCH only missing attributes (FR-132) |
    | `aggregate_datasets` | write | dataset ids from capability toggles (`checks.datasets`); handles the 404 "endpoint is unavailable" as `tenant_limitation` (R10) |
    | `set_dataset_schedule(dataset_id, on)` | write | GET then PUT `…/datasets/{id}` with `aggregationEnabled` (FR-134) |
    | `set_provisioning_policy(replace=false)` | write | `assets/provisioning-policy-create.json` rendered with `upn_domain`, `usage_location`; keeps an existing CREATE policy unless `replace` (FR-136) |
    | `set_correlation` | write | `assets/correlation-config.json` |
    | `apply_application_secret` | write | R2 |

  - `start_aggregation` reads `checks.aggregation.sequence` (default `[accounts, entitlements]` together, as today;
    Entra uses `[entitlements, accounts]` in sequence) and `settings.full_read: {field: deltaAggregationEnabled}`.
    It switches delta off, aggregates, and restores the previous value in a `finally`, with one action record that
    lists `delta_toggled`.
  - `peek_accounts` uses the same `full_read` (the skill's fix for an empty peek in delta mode).
  - `configure_source` reads `configure` entries with a new `capability:` key, applied only when the capability is
    chosen. The reference toggle values are ported from the skill's `feature-toggles.json`.
  - The owner-rerun gate text and `CHECK_ORDER` come from `checks.order`. The AWS string renders identically.
- **Rationale**: 001 FR-030 and SC-009 want connector knowledge in data. These tools are connector-neutral (any SaaS
  connector with schemas, datasets or provisioning policies can use them), and opting in keeps the AWS prompt
  identical. A unit test asserts that the AWS `offered_tools` set and the system prompt hash are unchanged. The eval
  gate fingerprint does change once, because it also hashes the shared prompt files and `loop.py` (R12).
- **Alternatives considered**:
  - An `entra_tools.py`: duplicates the action and plan plumbing and breaks SC-009's spirit.
  - Exposing a raw "PATCH anything" tool: too broad for least privilege, and the model could write any attribute.

## R6. Long-running aggregations (FR-139, SC-101)

- **Decision**:
  - Tools poll in the turn for up to `checks.aggregation.wait_in_turn_seconds` (Entra 180; AWS keeps today's 600
    from `poll_limit` 60 × 10 s).
  - If a task is still running, the tool returns `{running: true, task_ids, started_at}` and emits the action as
    `running` with `follow: true`. The API's `chat/followups.py` records `sessions.followups[]`:
    `{action_ref, task_ids, kind, started_at, next_step, ordered_by}`.
  - A background loop in the API invokes the agent in a new **model-free** mode
    `{"mode": "task_check", "tenant", "task_ids"}` every 60 s. The agent calls the task-status path and returns
    `{type: task_check, tasks: [{id, completion_status, messages}]}`.
  - At 30 minutes the plan step's reason becomes "still running — 32 min" and the UI shows **pending**, refreshed
    each minute.
  - When the task ends, the API:
    1. Completes the action record.
    2. Posts a `system_note` in the IAM engineer's thread with the result and counts.
    3. Sets the plan step.
    4. If it succeeded and `next_step` exists, queues a system-originated turn with `ordered_by` = the
       follow-up's IAM engineer. That is the one holding the standing check order; with no standing order, it posts
       the note and waits for an order.
  - The loop survives API restarts because it rescans `followups` on start.
- **Rationale**: holding a turn for 30+ minutes blocks the session queue (001 FR-006a). Polling with the model costs
  money for nothing (Principle IV). A `task_check` mode mirrors the existing model-free `tenant_check`.
- **Alternatives considered**:
  - Raising the turn timeout: blocks the queue.
  - The browser polling: nobody may be watching (FR-139).
  - The API polling ISC itself: the API has no ISC credential.

## R7. Extend-source sessions (FR-105, US1-5)

- **Decision**:
  - The new-session form has a radio (canvas): "Create a new source" or "Extend an existing Entra source with the
    capabilities above", stored as `details.source_mode` = `new` | `extend`. No source name is typed.
  - At session start, `find_connector_sources` finds the sources on the connector for that tenant. The agent then:
    - with `extend`, names the one it found, or asks which one if several were found;
    - with `new`, still names an existing source and offers to extend it instead (US1-5).
  - On the IAM engineer's order, `adopt_source(source_id)`:
    1. Reads the source.
    2. Requires `connector == Microsoft-Entra`, the tenant field to match, and the owner to equal the identity
       resolved from `source_owner`. Otherwise it refuses (001 FR-018).
    3. Emits `{type: source, id, name, adopted: true}`.
  - The API sets `sessions.source`, `sessions.mode = "extend"`, and skips the plan steps that `plan.yaml` marks
    `on_extend: skip` (create the app registration, create the secret, create the source), with reason "existing
    source".
  - Capability steps already on the source (from `read_source_setup`) are skipped by the agent with `update_plan`.
  - `delete_session_source` refuses on an adopted source: extend never deletes (FR-105).
  - Configure sends only the added capabilities' entries. The proof runs for those capabilities, and runs
    entitlements and accounts again when the account model changed.
  - Removing a capability isn't offered.
- **Rationale**: matches the clarification (B). Ownership stays enforced in tool code, not the prompt
  (Principle II).
- **Alternatives considered**: reopening the old session (rejected in clarification); letting the model skip plan
  steps freely, which isn't deterministic.

## R8. Session fields and screens (FR-102, FR-103, FR-120, US2-5/7)

- **Decision**:
  - New catalog field types, validated in `catalog.py`; the AWS types are unchanged:
    - `entra_tenant`: `^[a-z0-9-]+\.onmicrosoft\.com$`, a GUID, or another domain accepted with
      `warning: "SailPoint recommends the initial .onmicrosoft.com domain"`.
    - `guid_list`: Azure subscription IDs.
    - `domain`: the UPN domain.
    - `country_code`: ISO 3166-1 alpha-2 usage location.
    - `capabilities`: a multi-select from the entry's `capabilities:` list, each `{id, label, always?, warning?,
      requires_fields?}`.
  - `requires_fields` makes `foundry_subscriptions` required when `ai_agents` is chosen, and `upn_domain` and
    `usage_location` required for `provisioning`.
  - Choosing provisioning shows its `warning`; saving needs `accept_warnings: ["provisioning"]`, stored as
    `details.warnings_accepted = [{capability, user_id, at}]` (FR-103).
  - A catalog entry key `secret: {label, help, expires_help}` marks a type that needs an application secret. The
    session then has `application_secret` metadata, and the owner screen shows the **secret field card** while the
    plan step `provide_secret` is current or the secret state is `missing`, `vault_deleted` (needed again) or
    `expired`.
  - The field is a password-type input with autocomplete off and paste allowed, plus a date input. Submit posts to
    `PUT /sessions/{id}/application-secret` and the field is cleared at once. It is never in the composer, threads,
    suggestions or local storage.
  - Both screens show the secret status chip: received · in ISC · vault copy deleted, with provider, time and
    expiry. The API computes `expires_soon` (≤ 30 days) when the session is opened, and both screens show a
    warning.
- **Rationale**: keeps the value off every surface that 001 already masks, so masking is a second line of defence
  rather than the only one.
- **Alternatives considered**: a secret "message kind" in the owner's composer. It is rejected because it is too
  easy to send to the wrong place, and the composer content goes through the queue and history.

## R9. Proof order and delta (FR-133, US1-3)

- **Decision**:
  - Entra `checks.order`: `[create_source, configure_source, ensure_schema_attributes?, peek_accounts,
    test_connection, start_aggregation, aggregate_datasets?, set_dataset_schedule?]`, where `?` means by capability.
    Each runs only after the previous one succeeded.
  - `start_aggregation` runs entitlements, then accounts, each polled.
  - The report counts come from `count()`:
    - accounts total;
    - service principals: the count of accounts with the SP schema's type attribute, when that capability is on;
    - users = total − service principals;
    - entitlements;
    - AI agents: the machine-identities count with `datasetId eq "azure:foundry"`.
  - Test Connection "Provided source configuration already exists" maps to failure E7 (the skill's CIEM workaround).
- **Rationale**: the order and the delta handling are what the skill verified live (the 64 users + 161 SPs run).

## R10. Dataset aggregation and its fallback (FR-134, FR-135, US4)

- **Decision**:
  - `aggregate_datasets` uses the toggles read from the source: only `azure:foundry` is turned on by this flow, and
    Copilot Studio and Agent 365 stay off.
  - A 404 whose body says the endpoint is unavailable → result `tenant_limitation` with the UI path from
    `checks.datasets.ui_path` ("Sources → {source_name} → Machine Identity Datasets → Azure AI Foundry → Aggregate"),
    the plan step `blocked` with reason "start it in ISC (tenant limitation)", and the waiting banner set to the IAM
    engineer.
  - The IAM engineer confirms ("done") → the agent re-checks with the machine-identities count; more than zero AI
    agents → done.
  - This doesn't fail the onboarding (FR-135).
  - After success, `set_dataset_schedule(azure:foundry, on)`.
  - Errors naming the Agent 365 refresh token or Copilot Studio → failures E9 and E10. The agent offers to switch
    those toggles off through `configure_source` with the capability defaults.
- **Rationale**: the skill observed the 404 on a live tenant and confirmed the UI path works there.

## R11. Provisioning without a live write (FR-136, US5)

- **Decision**:
  - The agent writes the CREATE provisioning policy (UPN `first.last@{upn_domain}`, SailPoint's Create Password
    rule, `usageLocation`) and the correlation config from the playbook assets.
  - Lifecycle-state account actions are rendered as a reviewable block in the IAM engineer's thread from
    `assets/lifecycle-state-account-actions.patch.json`; there is no tool for them.
  - Proof:
    - `read_source_setup` shows that the CREATE policy and correlation exist.
    - The administrator's read-only setup step (`az rest … /servicePrincipals/{sp}/appRoleAssignments` and
      `… /roleManagement/directory/roleAssignments?$filter=principalId eq '{sp}'`) is checked by the agent against
      the expected permissions and the User Administrator role.
  - No account is created.
  - The report says the first real joiner is the first live write, and the plan keeps the step "Verify the first
    joiner" for the IAM engineer (not done in the session).
- **Rationale**: clarification answer A; provisioning hasn't been verified live (001 FR-028 still gates the release).

## R12. Testing and cost (Constitution IV, V)

- **Decision**:
  - **Unit (agent)**:
    - Path table snapshot (AWS legacy unchanged).
    - Experimental header only on listed paths.
    - Secret injected and shown as `[vaulted]` in the action.
    - `full_read` restores delta on error.
    - Dataset 404 → `tenant_limitation`.
    - `adopt_source` ownership refusal.
    - Offered tools for AWS unchanged.
    - AWS static prompt hash unchanged.
  - **Unit (API)**:
    - New field types and capability rules.
    - Secret field validation (GUID, expiry range).
    - Masker Entra pattern, with the existing masker tests unchanged.
    - Vault delete on Test Connection ok.
    - Follow-up state machine.
    - Owner-only secret endpoint (403 for the IAM engineer).
  - **Integration**: the session API against the stub ISC and the scripted model. Directory-only, service
    principals, AI agents with dataset fallback, provisioning, extend-source, rotation.
  - **Stub ISC**: `/v2026` Entra routes and switches `_stub/entra/{secret_invalid,dataset_unavailable,
    slow_aggregation,delta_empty_peek,foreign_entra_source}`.
  - **e2e (Playwright, scripted model)**: `entra-onboarding.spec.ts` (two browsers; secret field; status chip;
    proof counts) and `entra-secret-leak.spec.ts` (seeded secret not found anywhere).
  - **Evals (opt-in)**: `tests/evals/entra_failures/cases.yaml`, 11 cases (E1–E11) × 3 runs ≈ 33 Haiku calls,
    about $1.30 for text and screenshots (the runner's own estimate: ~5 model calls per turn × 11 cases × 2 modes × 3 runs ≈ 330 calls; `--mode text` halves it). The estimate is printed first, and `--gate` (10 runs) runs only when the Entra
    fingerprint changes.
  - **Fingerprints are per suite**: the suite's cases, `playbooks/<its connector type>/`, `prompts/` and `loop.py`.
    Shared prompts and `loop.py` still count, so the first Entra change re-runs the AWS gate **once**. That run is
    stated with its estimate (about 100 calls, about $1.20 at 10 runs × ~10 cases) before it starts (tasks T092).
  - **Setup-check evals (opt-in)**: `tests/evals/entra_setup_checks/cases.yaml`, 4 cases × 3 runs ≈ 60 calls
    (~$0.15), for FR-112.
  - **Real-tenant check (opt-in)**: quickstart §4 on a test Entra tenant and ISC tenant. This uses the real model
    (stated cost about 30–60 calls, about $0.50–$1) and is required before release for provisioning (001 FR-028).

## R13. IAM and deploy changes (Principle II, V)

- **Decision** (all in `agent-deploy.sh`, idempotent):
  - **API IAM user**: add `bedrock-agentcore:CreateApiKeyCredentialProvider`, `DeleteApiKeyCredentialProvider`,
    `GetApiKeyCredentialProvider` on `token-vault/default/apikeycredentialprovider/onboarding-entra-*`, plus
    `secretsmanager:CreateSecret`, `PutSecretValue` and `DeleteSecret` on
    `bedrock-agentcore-identity!default/apikey/onboarding-entra-*`.
  - **Agent role**: add `bedrock-agentcore:GetResourceApiKey` on the workload identity directory and token vault,
    plus `secretsmanager:GetSecretValue` on `…/apikey/onboarding-entra-*`.
  - The teardown in `down.sh` deletes leftover `onboarding-entra-*` providers.
  - There is no new Kubernetes object, route or NetworkPolicy.
- **Rationale**: the same shape as the existing OAuth2 provider grants, with the prefix as tight as IAM allows (see
  plan Complexity Tracking).

## R14. Porting the skill into the playbook

- **Decision**: the port is a copy plus a rewrite into playbook format, with each file's header naming its
  source:

  | Playbook file | From the skill |
  |---|---|
  | `setup.md` | `scripts/entra-setup.sh` (role check, app create/reuse, permissions by name, consent via appRoleAssignments, secret, directory roles, Azure RBAC for Foundry, oauth2 grant) + `references/entra-permissions.md` + `assets/entra/permissions/{readonly,machine-identity,ai-agents,provisioning}.json` |
  | `settings.yaml` | `assets/isc/source-create.tmpl.json`, `source-configure.patch.tmpl.json`, `feature-toggles.json` (the screenshot-matched reference values), `keymap.default.json` |
  | `checks.yaml` | `scripts/isc-source.sh` `verify` / `aggregate` / `datasets` / `dataset-schedule` / `schema-spn` + `references/isc-api.md` |
  | `assets/*.json` | `assets/isc/account-schema-spn-attributes.json`, `provisioning-policy-create.tmpl.json`, `correlation-config.tmpl.json`, `lifecycle-state-account-actions.patch.tmpl.json` |
  | `failures.md` | `references/troubleshooting.md` |
  | `plan.yaml` | the SKILL.md workflow order per profile |

  - The administrator runs every `az` command. The secret step creates the secret with
    `az ad app credential reset … --query password -o tsv > "$HOME/.sailpoint-entra.secret"` (`umask 077`). It
    tells the administrator to open the file, copy the Value into the secret field, and delete the file. The step
    never prints the value to the terminal or asks for it in the chat (FR-113).
  - Permissions are resolved by name, not GUID (the skill's rule).
  - Exchange, Teams and PIM profiles aren't ported (out of scope).
- **Rationale**: 001 R6's "port, don't rewrite": the skill's steps are the ones verified live.

## R15. "What happens" panel and capability summaries (canvas: New Entra ID session)

- **Decision**:
  - Catalog capabilities gain `summary` (one line on the card), `tag` (`read_only` | `writes`) and `owner_summary`, a
    template such as "Service principals: {permission_count} read permissions".
  - `{permission_count}` is computed when the catalog loads, from `playbooks/entra-id/permissions/<capability>.json`.
    These are the skill's profile files, copied: readonly → directory, machine-identity → service_principals,
    ai-agents → ai_agents, provisioning → provisioning. The same files feed `setup.md` steps 4–7, so the panel and
    the steps can't disagree.
  - The panel lists, in order:
    - the registration and consent line;
    - one line per chosen capability;
    - the secret line;
    - two chips: owner step count (the plan steps with `actor: application_owner` left after capability
      filtering) and "N capabilities write" (`tag: writes`).
  - "The agent will, on your order" is the agent's plan steps after the same filtering, by title.
  - The panel is computed in the browser from the catalog response and the form state. It costs nothing, needs no
    API call per keystroke, and the API recomputes the same plan when the session is created.
- **Rationale**: the canvas shows counts ("6 read permissions", "7 write permissions and the User Administrator
  role"), and counting from the permission files keeps them true when the skill's lists change.
- **Alternatives considered**: hand-written counts in `catalog.yaml`. These drift from `setup.md`.

## R16. The secret field card (canvas: Entra administrator)

- **Decision**:
  - The card sits **above the conversation** on the owner screen, with a 2 px owner-blue border.
  - It is shown while the plan step `provide_secret` isn't done, or the secret state is `vault_deleted` with a
    pending `secret.needed`, or `expires_soon`. Otherwise it collapses to the status strip.
  - Controls:
    - a password input with `autocomplete="off"`, labelled "Client secret Value";
    - a date input "Expires";
    - a submit button, "Send to the vault".
  - Errors from the API's 422 show inline under the inputs in `role="alert"`, with the exact texts from FR-121, plus
    the help line "Certificates & secrets → the Value column, shown only once".
  - On submit the input is cleared before the request resolves, and it is never put into Angular state, local
    storage, the composer or a suggestion.
  - The status strip has four steps, Not received · Received · In SailPoint · Vault copy deleted, mapping to
    `missing`, `received`, `in_isc` and `vault_deleted`. The current step is highlighted, with a note: "The vault
    copy is deleted as soon as Test Connection passes."
  - The IAM engineer gets a read-only **Application secret** card in the side column instead: provided by and at, in
    SailPoint at, vault copy deleted at, and expires with the 30-day warning. They have no input (FR-120).
- **Rationale**: these are the canvas's placement and wording. Keeping the field out of the thread is what makes
  "never the chat" visible.

## R17. Exposed-secret system note (canvas: masked paste)

- **Decision**:
  - When the masker's Entra pattern (R3) matches an owner message, the API:
    1. Stores the masked text.
    2. Writes a `system_note` with `tone: danger`, `code: secret_exposed` in the owner's thread right after it:
       "A client secret was masked before it was saved, shown or sent to the agent. Treat it as exposed: delete it
       in Entra and create a new one."
    3. Adds `secret_exposed: true` to the turn payload, so the agent tells the owner to delete that secret and use
       the field.
  - The owner gets suggestion state `waiting_for_secret`.
  - The note costs no model call. An audit kind `secret_exposed_in_chat` (who, when; no value) is written.
- **Rationale**: US2-3 says the pasted secret counts as exposed. A deterministic note doesn't depend on the model
  noticing.

## R18. Proof counts card and action summaries (canvas: IAM engineer)

- **Decision**:
  - The API keeps `sessions.proof = {users, service_principals, entitlements, ai_agents, ai_agents_state,
    updated_at}`, updated from `action.response.counts` of the aggregation and dataset actions.
    `ai_agents_state` is `counted` | `tenant_limitation` | `not_chosen`.
  - The IAM engineer's "What SailPoint now sees" card shows the four tiles. AI agents shows "Start it in ISC" when the
    state is `tenant_limitation`, and the tile is hidden for `not_chosen`. A `proof.updated` event refreshes it.
  - The owner screen doesn't show the card: the counts are SailPoint results, and the IAM engineer passes them on.
  - Every action event gains `summary`: one masked line, ≤ 80 characters, built by the tool. Examples:
    `clientSecret: [vaulted] · 27 fields`, `delta off → restored · 4,973 accounts`, and
    `tenant limitation · 404 endpoint unavailable`. The action row shows it under the action name, and Details opens
    the 001 dialog. AWS tools don't set it, so their rows look as before.
  - The follow-up loop's notes use `tone: info` (pending, with minutes) and `tone: success` or `tone: danger`
    (finished).
- **Rationale**: the canvas's IAM screen leads with the counts, which are what SC-104 measures.

## R19. Header chips, owner-thread bar and suggestions (canvas: both Entra screens)

- **Decision**:
  - **Header chips**: `checks.yaml` `milestone_order` sets the chip order; when absent, 001's order is kept for AWS.
    Entra uses `[application_ready, source_created, configured, connection_check, test_connection, aggregation]`,
    and the first label comes from `first_step_label` ("Entra app ready").
  - **Owner-thread bar**: the IAM screen keeps 001's side-by-side threads by default. The canvas's collapsed bar
    ("Entra administrator ↔ Agent · view only · 2 new · Show side by side") is a per-viewer toggle, kept in
    `localStorage` and wrapped in try/catch. It counts the owner messages that arrived since it was collapsed. It is
    browser-only, like 001 R25 and R26.
  - **Presence**: the header shows the other participant online or away from the existing presence signal; nothing
    new.
  - **Suggestions**: add the states `waiting_for_secret` (owner: "I've put the new secret in the field", "Where is
    the Value column?") and `tenant_limitation` (IAM engineer: "Done, I started it in ISC"). The validation in
    `catalog.py` accepts them, and AWS's file doesn't use them.
- **Rationale**: matches the artboards without changing what AWS sessions show (SC-106).
