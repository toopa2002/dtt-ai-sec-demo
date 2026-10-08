# ISC REST API used by isc-source.sh (v2026 only)

Reference: https://developer.sailpoint.com/redoc/sailpoint-api-v2026-light.html (OpenAPI 3.0.1, 576 paths; servers
`https://{tenant}.api.identitynow.com/v2026`). Every call below is v2026; GETs were checked on a live tenant
(2026-10-08). Auth: `POST /oauth/token` form `grant_type=client_credentials&client_id=<PAT id>&client_secret=<PAT
secret>` → `access_token` (+ `identity_id` = PAT owner, used for `--owner me` and "is this source mine"). PAT needs
ORG_ADMIN or SOURCE_ADMIN.

GA unless marked *experimental* (header `X-SailPoint-Experimental: true` required — the script sends it only there).

| Subcommand | Method + path (all `/v2026`) | Body | Result |
|---|---|---|---|
| discover | `GET /connectors?filters=name co "Entra"` (+ `"Azure"`) | — | pick scriptName `Microsoft-Entra` (`name eq` isn't accepted here → 400) |
| | `GET /connectors/{scriptName}` | — | `.type` = connector spec id |
| | `GET /connectors/{scriptName}/source-config` | — | XML form; `<Field name>` = connectorAttributes keys |
| | `GET /sources?filters=connectorName eq "Microsoft Entra"&limit=250` | — | existing sources on the connector |
| create | `GET /sources?filters=name eq "…"`, `GET /public-identities?filters=alias\|email eq "…"` | — | |
| | `POST /sources` | `source-create.tmpl.json` | `.id` |
| configure | `PATCH /sources/{id}` | `application/json-patch+json` | |
| peek | `POST /sources/{sourceId}/connector/peek-resource-objects` | `{"objectType":"account","maxCount":5}` | `.resourceObjects[]` |
| test | `POST /sources/{sourceId}/connector/test-configuration` | none | `.status` SUCCESS/FAILURE, `.details` |
| aggregate (entitlement) | `POST /sources/{sourceId}/load-entitlements` | multipart/form-data, no fields | task id **`.id`** (202) |
| aggregate (account) | `POST /sources/{id}/load-accounts` | multipart/form-data `disableOptimization=true` | task id **`.task.id`** (202) |
| aggregate (dataset) | `POST /sources/{sourceId}/aggregate-agents` *experimental* | `{"datasetIds":[…],"disableOptimization":false}` (`aggregate-datasets.tmpl.json`) | task id **`.id`** (200) |
| (polling) | `GET /task-status/{id}` | — | `.completionStatus` SUCCESS/WARNING/ERROR/TERMINATED/TEMPERROR, null while running; `.messages[].localizedText` |
| (totals) | `GET /accounts?filters=source.id eq "…"&count=true&limit=1` | — | header `X-Total-Count` (**`source.id`**, not `sourceId`) |
| | `GET /entitlements?filters=source.id eq "…"&count=true&limit=1` | — | header `X-Total-Count` |
| | `GET /machine-identities?filters=source.id eq "…"&count=true&limit=1` *experimental* | — | header `X-Total-Count`; items carry `datasetId` (e.g. azure:foundry) and `subtype` ("AI Agent") — checked live |
| provisioning-policy | `GET`/`POST /sources/{sourceId}/provisioning-policies`; `PUT …/provisioning-policies/CREATE` | `provisioning-policy-create.tmpl.json` | |
| correlation | `GET`/`PUT /sources/{id}/correlation-config` | `correlation-config.tmpl.json` | |
| schedule | `GET`/`POST /sources/{sourceId}/schedules`, `PATCH`/`DELETE …/schedules/{scheduleType}` | `{type, cronExpression}` | types: ACCOUNT_AGGREGATION, GROUP_AGGREGATION only |
| show | `GET /sources/{id}` | — | |
| schema-spn | `GET /sources/{sourceId}/schemas`, `PATCH /sources/{sourceId}/schemas/{schemaId}` | `[{"op":"add","path":"/attributes/-","value":{name,type,isMulti,isEntitlement,schema?}}]` | GA; adds only missing attributes |
| dataset-schedule | `GET` then `PUT /sources/{id}/datasets/{datasetId}` *not in the published spec; captured from the UI's "Enable Schedule", used live* | the GET body with `aggregationEnabled` set to true/false | 200; frequency is ISC's default |
| datasets (info only) | `GET /sources/{id}/datasets` *experimental, not in the published v2026 spec* | — | `[{id, name, aggregationEnabled, resources[]}]`; `aggregationEnabled` = scheduled aggregation on (stays false after manual runs). Nothing depends on it |

Notes:
- Entitlement aggregation has no per-type selector: it loads every entitlement schema on the source (groups,
  directory roles, service plans, app roles, PIM, …). Count one type with `…&filters=source.id eq "…" and type eq "group"`.
- Dataset aggregation needs datasetIds. The script takes them from the source's toggles (documented `GET
  /sources/{id}`): azure:foundry ← enableAIFoundryAgent, microsoft:copilot ← enableCopilotAIAgent,
  microsoft:agent365 ← enableMicrosoftAgent365. Dataset schedules: the published spec has nothing (`…/datasets/{id}/schedule(s)` → 404); the UI flips `aggregationEnabled` with `PUT /sources/{id}/datasets/{datasetId}`, which `dataset-schedule` replays.
- `aggregate-agents` is documented with user-auth scope `idn:mis-agents:aggregate`; a PAT is user auth. Not yet
  exercised live — if it returns 403, check the PAT owner's admin level and the Agentic Fabric licence.
- Lifecycle-state account actions (leaver = disable/delete on this source) live on the identity profile:
  `GET /identity-profiles/{identity-profile-id}/lifecycle-states/{lifecycle-state-id}`, then `PATCH` the same path
  with `lifecycle-state-account-actions.patch.tmpl.json`, where `ACCOUNT_ACTIONS` = existing actions + the new one.

## Two (and more) Entra connectors

`GET /v2026/connectors` lists several Entra-looking connectors. Seen on a live tenant:

| Name | scriptName | In scope |
|---|---|---|
| Microsoft Entra | `Microsoft-Entra` | **yes** — the cloud (SaaS) connector |
| Azure Active Directory | `azure-active-directory-angularsc` | no — VA-based |
| Microsoft Entra SSO | `Microsoft-Entra-SSO` | no |
| Activity Insights - Microsoft Entra ID | `adi-entra` | no |
| CIEM Azure (Global) | (uuid) | no |

Note "Entra" is also a substring of "Business C**entra**l" — match the whole name. A UI-built Entra source shows
the authoritative values: `connector: "Microsoft-Entra"`, `connectorAttributes.spConnectorSpecId` (=
`b7e9374a-3c50-4b51-8880-e501f947bbf8` on the tenant checked), `idnProxyType: sp-connect`, `connectionType: direct`,
`spConnectorSupportsCustomSchemas`, `deleteThresholdPercentage: 10`; `discover --from-source <name>` reads its keys.

## Key map

`~/.cache/sailpoint-isc-entra/<tenant>-keymap.json` (`assets/isc/keymap.default.json` is the built-in copy used by
`--plan-only`):

```json
{
  "connector": "Microsoft-Entra", "connectorName": "Microsoft Entra", "connectorType": "<spec id>",
  "discoveredVia": "source-config form", "grantTypeValues": ["CERTIFICATE_CREDENTIALS","CLIENT_CREDENTIALS","REFRESH_TOKEN"],
  "allFields": ["…67 field names…"],
  "keys": { "domainName": "domainName", "clientID": "clientID", "clientSecret": "clientSecret", "grantType": "grantType", "…": "…" }
}
```

`keys` maps the canonical names used in the templates to this tenant's form fields; `null` = not on the form (that
toggle is skipped). Edit by hand if a mapping is wrong; `--keymap FILE` uses another one.

## UI label → field (Feature Management / Aggregation / Machine Identity Governance)

| UI label | Field |
|---|---|
| Manage Microsoft 365 Groups / Enable Teams Governance | `manageO365Groups` / `enableTeamsGovernance` |
| Manage User- / System-Assigned Managed Identities as Accounts | `enableManagedIdentityManagement` / `enableSystemAssignedManagedIdentity` |
| Manage Microsoft Entra Service Principals as Accounts · Service Principal Account Filter | `manageAzureServicePrincipalAsAccount` · `spnAccountFilter` |
| Manage Azure PIM / Microsoft Entra PIM Role Memberships | `spnManageAzurePIM` / `spnManageAzureADPIM` |
| Manage **Role** Memberships | `spnManageDirectoryRole` (sic) |
| Manage Application Role Memberships / Group Memberships | `spnManageAppRoles` / `spnManageGroups` |
| Manage Microsoft Entra **Role Assignment** Memberships | `spnManageRBACRoles` (sic) |
| Manage Admin Consented Permission Memberships / Custom Security Attributes (SP) | `manageAdminConsentedPermissions` / `manageCustomSecurityAttributesForServicePrincipals` |
| Manage Access Packages | `enableAccessPackageManagement` |
| Aggregate All Groups / Delta Aggregation / Aggregate Group Hierarchy / Page Size | `aggregateAllGroups` / `deltaAggregationEnabled` / `aggregateGroupHierarchy` / `pageSize` |
| Enable Azure AI Foundry Agents · Always Use the Latest Available Version | `enableAIFoundryAgent` · `foundryAggregateLatestVersionOnly` |
| Enable Microsoft Copilot Studio Agents / Enable Microsoft Agent 365 | `enableCopilotAIAgent` / `enableMicrosoftAgent365` |

## Source-config fields (live form, 2026-10-08)

Auth: `domainName`, `clientID`, `clientSecret`, `grantType`, `refresh_token`, `clientCertificate`, `private_key`,
`privateKeyPassword`, `isCaeEnabled`, `agent365RefreshToken`.
Features: `manageAzureServicePrincipalAsAccount`, `enableManagedIdentityManagement`,
`enableSystemAssignedManagedIdentity`, `manageExchangeOnline`, `exoAuthenticationType`, `exchangeCertificate`,
`exchangeCertificatePassword`, `exchangeUserName`, `exchangeUserPassword`, `enablePIM`, `spnManageAzurePIM`,
`spnManageAzureADPIM`, `enableAIFoundryAgent`, `foundryAggregateLatestVersionOnly`, `enableCopilotAIAgent`,
`enableMicrosoftAgent365`, `microsoftAgent365CatalogPlatform{AgentBuilder,CopilotStudio,Foundry,Other}`,
`enableTeamsGovernance`, `enableCIEM`, `enableADI`, `enableAdministrativeUnits`, `enableAccessPackageManagement`,
`aggregateHiddenAccessPackages`, `manageAdminConsentedPermissions`, `manageCustomSecurityAttributesFor*`,
`spnManageAppRoles`, `spnManageGroups`, `spnManageDirectoryRole`, `spnManageRBACRoles`, `manageO365Groups`,
`aggregateAllGroups`, `aggregateGroupHierarchy`, `enableMailContactGovernance`, `revokeSessionOnDisable`,
`deltaAggregationEnabled`, `pageSize`.
Filters: `userFilters`, `groupFilters`, `groupMembershipFilters`, `azureADGroupsFilter`, `azureADRolesFilter`,
`azureRolesFilter`, `directoryRolesFilter`, `spnAccountFilter`, `channelFilter`, `mailContactFilter`,
`supportsAdvancedAccountFilter`, `supportsAdvancedGroupFilter`.

To set anything else: `PATCH /v2026/sources/{id}` with `[{"op":"add","path":"/connectorAttributes/<field>","value":…}]`
— show the user the body first. Creation-only attributes (`spConnectorSpecId`, `idnProxyType`) can only be set by POST.
