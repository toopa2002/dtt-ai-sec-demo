# Contract: the `entra-id` playbook and new playbook keys

This extends [001 connector-playbook.md](../../001-isc-onboarding-agent/contracts/connector-playbook.md). Every key
below is optional for other types, and a playbook without any of them behaves exactly as in 001: the AWS SaaS
playbook is unchanged.

## Catalog entry

```yaml
- id: entra-id
  name: Microsoft Entra ID
  status: available
  description: >-
    Users, groups, directory roles, licences and app roles from a Microsoft Entra tenant through SailPoint's cloud
    Microsoft Entra connector; optionally service principals, Azure AI Foundry agents and provisioning.
  owner_label: Entra administrator
  application_label: Entra ID
  first_step_label: Entra app ready
  owner_asks: >-
    Register the SailPoint application, add the permissions for the chosen capabilities, grant admin consent and
    create a client secret (pasted into the secret field, never the chat).
  agent_configures: >-
    Microsoft Entra source: tenant domain, application ID, secret, capability settings; then connection check, Test
    Connection, entitlement and account aggregation, and the capabilities' own checks.
  secret: { label: Client secret Value, help: "Certificates & secrets → the Value column, not the Secret ID",
            expires_help: "The Expires date shown next to it" }
  badge: new
  capabilities:            # summary = card line; owner_summary = "what happens" panel line (research R15)
    - { id: directory, label: Directory, always: true, tag: read_only, permissions: readonly,
        summary: "Users, groups incl. Microsoft 365, directory roles, licences, app roles.",
        owner_summary: "Directory: {permission_count} read permissions" }
    - { id: service_principals, label: Service principals as accounts, tag: read_only, permissions: machine-identity,
        summary: "Application service principals with their roles, app roles, groups and consented permissions.",
        owner_summary: "Service principals: {permission_count} read permissions" }
    - { id: ai_agents, label: AI agents (Azure AI Foundry), tag: read_only, permissions: ai-agents,
        requires_fields: [foundry_subscriptions],
        summary: "Foundry agents as machine identities, aggregated on a schedule. Copilot Studio and Agent 365 stay off.",
        owner_summary: "AI agents: Reader and Cognitive Services Data Contributor on {subscription_count} subscription(s), plus the Azure management consent" }
    - { id: provisioning, label: Provisioning, tag: writes, permissions: provisioning,
        requires_fields: [upn_domain, usage_location],
        summary: "Joiner, mover and leaver: ISC creates and changes Entra accounts.",
        owner_summary: "Provisioning: {permission_count} write permissions and the User Administrator role",
        warning: "ISC will be able to create, change, disable and delete users and groups in {tenant_domain}. The administrator also gives the SailPoint app the User Administrator role. Nothing is written during this session; the first real joiner is the first write." }
  session_fields: [ …see data-model.md… ]
  playbook: playbooks/entra-id
```

## `settings.yaml`: new keys

```yaml
connector_script: Microsoft-Entra
spec_id: b7e9374a-3c50-4b51-8880-e501f947bbf8   # re-read via get_connector_form if SailPoint changes it
isc_api: v2026                                  # absent = legacy beta/v3 paths (AWS SaaS)
experimental_paths: ['/aggregate-agents$', '/datasets(/|$)', '^/v2026/machine-identities']
tenant_match_field: domainName                  # find_connector_sources / adopt_source compare this to tenant_domain
secret_fields: { clientSecret: application_secret }   # filled from the vault in tool code, shown as [vaulted]
full_read: { field: deltaAggregationEnabled }   # switched off for peek and aggregation, then restored
creation_only: { idnProxyType: sp-connect, connectionType: direct, spConnectorSupportsCustomSchemas: true,
                 deleteThresholdPercentage: 10 }
configure:
  - { field: domainName, from: tenant_domain }
  - { field: clientID, from: client_id }
  - { field: grantType, value: CLIENT_CREDENTIALS }
  # directory (always): reference configuration (skill feature-toggles.json → readonly)
  - { field: aggregateAllGroups, value: true }
  - { field: manageO365Groups, value: true }
  - { field: deltaAggregationEnabled, value: true }
  - { field: pageSize, value: 100 }
  - { field: enableTeamsGovernance, value: false }
  - { field: enableAccessPackageManagement, value: false }
  - { field: enableManagedIdentityManagement, value: false }
  # service_principals
  - { field: manageAzureServicePrincipalAsAccount, value: true, capability: service_principals }
  - { field: spnAccountFilter, value: "servicePrincipalType eq 'Application'", capability: service_principals }
  - { field: spnManageDirectoryRole, value: true, capability: service_principals }   # UI "Manage Role Memberships"
  - { field: spnManageAppRoles, value: true, capability: service_principals }
  - { field: spnManageGroups, value: true, capability: service_principals }
  - { field: spnManageRBACRoles, value: true, capability: service_principals }       # UI "…Role Assignment Memberships"
  - { field: manageAdminConsentedPermissions, value: true, capability: service_principals }
  - { field: manageCustomSecurityAttributesForServicePrincipals, value: true, capability: service_principals }
  - { field: spnManageAzurePIM, value: false, capability: service_principals }
  - { field: spnManageAzureADPIM, value: false, capability: service_principals }
  # ai_agents
  - { field: enableAIFoundryAgent, value: true, capability: ai_agents }
  - { field: foundryAggregateLatestVersionOnly, value: true, capability: ai_agents }
  - { field: enableCopilotAIAgent, value: false, capability: ai_agents }   # out of scope: needs Power Platform setup
  - { field: enableMicrosoftAgent365, value: false, capability: ai_agents }
```

- **`capability`**: an entry is sent only when that capability is chosen. In an extend-source session, only the
  added capabilities' entries are sent, plus `clientSecret` if a new secret is waiting.
- **Note**: the reference configuration (skill) has Copilot Studio on. This flow keeps it off, because
  Copilot Studio is out of scope and fails without Power Platform setup (spec US4-4).

## `checks.yaml`: new keys

```yaml
tools: [find_connector_sources, adopt_source, find_source, get_connector_form, get_task, read_source_setup,
        create_source, configure_source, apply_application_secret, ensure_schema_attributes, peek_accounts,
        test_connection, start_aggregation, aggregate_datasets, set_dataset_schedule, set_provisioning_policy,
        set_correlation, delete_session_source]
order: [create_source, configure_source, ensure_schema_attributes, peek_accounts, test_connection,
        start_aggregation, aggregate_datasets, set_dataset_schedule]       # each only after the previous succeeded
connection_check: { tool: peek_accounts, object_type: account, max_count: 5, also_sets: application_ready }
test_connection: { tool: test_connection, run_after: connection_check }
aggregation:
  tool: start_aggregation
  sequence: [entitlements, accounts]        # default (absent): accounts + entitlements together, as AWS today
  wait_in_turn_seconds: 180                 # then hand over to the follow-up loop (FR-139)
  pending_after_minutes: 30
schema:
  service_principals: assets/account-schema-spn-attributes.json
datasets:
  ai_agents: { id: "azure:foundry", toggle: enableAIFoundryAgent, schedule: true,
               ui_path: "Sources → {source_name} → Machine Identity Datasets → Azure AI Foundry → Aggregate" }
  unavailable_signature: "endpoint is unavailable"     # 404 body → tenant_limitation (FR-135)
provisioning:
  policy: assets/provisioning-policy-create.json      # {upn_domain}, {usage_location}
  correlation: assets/correlation-config.json
  lifecycle_template: assets/lifecycle-state-account-actions.patch.json   # shown for review, never applied
milestone_order: [application_ready, source_created, configured, connection_check, test_connection, aggregation]
plan_step: { application_ready: confirm_consent, source_created: create_source, configured: configure_source,
             connection_check: connection_check, aggregation: aggregate_accounts, test_connection: test_connection }
```

## `plan.yaml`: new step keys

`capability: <id>` (skipped at seeding when that capability isn't chosen) and `on_extend: skip`. Entra starting
plan, in order:

| id | actor | kind | capability | on_extend | setup step |
|---|---|---|---|---|---|
| check_admin_role | application_owner | read_only | | | 1 |
| check_app_name | application_owner | read_only | | skip | 2 |
| register_app | application_owner | change | | skip | 3 |
| directory_permissions | application_owner | change | directory | skip | 4 |
| sp_permissions | application_owner | change | service_principals | | 5 |
| foundry_access | application_owner | change | ai_agents | | 6 |
| provisioning_permissions | application_owner | change | provisioning | | 7 |
| grant_consent | application_owner | change | | | 8 |
| confirm_consent | application_owner | read_only | | | 9 (milestone application_ready) |
| provide_secret | application_owner | change | | skip | 10 (the secret field) |
| order | iam_engineer | change | | | |
| create_source | agent | change | | skip | |
| configure_source | agent | change | | | |
| sp_schema | agent | change | service_principals | | |
| connection_check | agent | read_only | | | |
| test_connection | agent | read_only | | | |
| aggregate_entitlements | agent | read_only | | | |
| aggregate_accounts | agent | read_only | | | |
| aggregate_foundry | agent | read_only | ai_agents | | |
| foundry_schedule | agent | change | ai_agents | | |
| provisioning_policy | agent | change | provisioning | | |
| confirm_provisioning_access | application_owner | read_only | provisioning | | 11 |
| review_lifecycle_actions | iam_engineer | read_only | provisioning | | |

## `setup.md` (Entra administrator), steps

| # | Step | Kind | Expect |
|---|---|---|---|
| 1 | `az login --tenant {tenant_domain} --allow-no-subscriptions`; list active directory roles | read-only | Global Administrator or Privileged Role Administrator (activate via PIM if eligible) |
| 2 | `az ad app list --display-name "{app_name}"` | read-only | `[]`, or an app tagged `sailpoint-isc-entra-connector` (collisions.md) |
| 3 | `az ad app create` + `az ad sp create` | change | `appId` (→ the agent records `client_id`) and the SP object id |
| 4–7 | `az ad app permission add` per permission, resolved by name (`readonly`, `machine-identity`, `ai-agents`, `provisioning` sets); for AI agents also `az role assignment create` Reader + Cognitive Services Data Contributor on each `{foundry_subscriptions}` and the Azure Service Management `user_impersonation` grant; for provisioning also the User Administrator directory role | change | each command's JSON |
| 8 | Grant admin consent (appRoleAssignments per permission) | change | one assignment per permission |
| 9 | `az rest --url …/servicePrincipals/{sp}/appRoleAssignments` | read-only | every expected permission listed (the agent names any missing one) |
| 10 | Create the secret into a `umask 077` file, copy the Value into the **secret field** with its Expires date, delete the file | change | the secret field shows "received"; nothing pasted in chat |
| 11 | (provisioning) read the app's directory role assignments | read-only | User Administrator present |

## `failures.md`: E1–E11 (FR-140)

| id | Signature | Side | Retry once |
|---|---|---|---|
| E1 | AADSTS7000215 invalid client secret | application | no: new secret via the field |
| E2 | AADSTS7000222 secret expired | application | no |
| E3 | AADSTS700016 app not found / AADSTS90002 tenant not found | application (or session domain) | after consent: yes |
| E4 | AADSTS53003 / Conditional Access blocks the workload identity | application | no |
| E5 | 403 Authorization_RequestDenied on one object type (e.g. applicationRole → Application.Read.All) | application | no |
| E6 | 401/403 within ~5 min of consent (propagation) | application | yes, after 2–5 min |
| E7 | Test Connection "Provided source configuration already exists" | sailpoint | no: the skill's CIEM workaround |
| E8 | Peek returns 0 accounts with delta on | sailpoint | handled by full_read, not a failure |
| E9 | Dataset error naming the Agent 365 refresh token | sailpoint | no: switch the toggle off |
| E10 | Copilot Studio dataset fails (no Power Platform application user) | application | no: switch the toggle off |
| E11 | Dataset aggregation 404 "endpoint is unavailable" | sailpoint (tenant limitation) | no: UI path, FR-135 |

## `collisions.md`

- **App registration**: an app named `{app_name}` that isn't tagged `sailpoint-isc-entra-connector` → ask the
  administrator to choose another name, or to confirm it is the SailPoint app. If they confirm, add only the
  missing permissions; never remove any.
- **Existing source**: a source on `Microsoft-Entra` with `domainName == {tenant_domain}` → name it and its owner,
  and offer extend-source.
- **Naming rule**: one app registration per ISC tenant, `SailPoint ISC - {tenant}`.

## `suggestions.yaml`: states used by Entra

001's states plus `waiting_for_secret` (owner: "I've put the new secret in the field", "Where is the Value column?",
"What is left to do?") and `tenant_limitation` (IAM engineer: "Done, I started it in ISC", "Show the leaver actions
to review", "What is left to do?").
