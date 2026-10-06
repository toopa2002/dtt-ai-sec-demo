# ISC REST API for the AWS SaaS source

Specs: `github.com/sailpoint-oss/api-specs` (v3, beta, v2025). Base URL `https://$ISC_TENANT`, where `ISC_TENANT` is the full API host: `{tenant}.api.identitynow.com` for
production, `{tenant}.api.identitynow-demo.com` for demo/partner tenants. The script turns a UI host
(`{tenant}.identitynow-demo.com`) or pasted URL into the API host; `ISC_BASE_URL` overrides it entirely.

## Auth

```
POST /oauth/token
Content-Type: application/x-www-form-urlencoded
grant_type=client_credentials&client_id=<PAT id>&client_secret=<PAT secret>
```
Use `access_token` as `Authorization: Bearer …`. The PAT owner needs ORG_ADMIN or SOURCE_ADMIN
(scopes `idn:sources:manage`, `idn:source-connector:manage`, `idn:entitlement:manage`).

## Calls used by isc-source.sh

| Step | Call | Notes |
|---|---|---|
| external ID | `GET /beta/tenant` | `.products[] \| select(.productName=="idn") \| .attributes.externalId` — tenant-wide, same for every AWS source |
| find connector | `GET /v3/connectors?filters=name co "AWS"` | live tenants: `"AWS SaaS"`, scriptName `awssaas`, `type` = spec id (UUID). Docs call it "Amazon Web Services SaaS". Ignore "AWS" (VA), "CIEM AWS", "AWS Secrets Manager" |
| form fields | `GET /v3/connectors/{scriptName}/source-config` | XML; each `<Field name="…">` is a `connectorAttributes` key |
| connector instance | `GET /beta/connector-instances/{spConnectorInstanceId}` | `{id, connectorSpecId, name, config}`. ISC creates it when the source is created with `spConnectorSpecId` |
| find source | `GET /v3/sources?filters=name eq "AWS"` | |
| owner | `GET /v3/public-identities?filters=alias eq "jane.doe"` (or `email eq`) | |
| create | `POST /v3/sources` | body below |
| read | `GET /v3/sources/{id}` | |
| update | `PATCH /v3/sources/{id}`, `Content-Type: application/json-patch+json` | `[{"op":"add","path":"/connectorAttributes/<key>","value":…}]` |
| test | `POST /beta/sources/{id}/connector/test-configuration` | returns `status`, `elapsedMillis`, `details`; also `.../connector/check-connection` |
| delete | `DELETE /v3/sources/{id}` | 202 + task; the name is free again once `GET` returns 404 |
| aggregate accounts | `POST /beta/sources/{id}/load-accounts` (form; `disableOptimization=true`) | 202 + task. Not in v3; also `/v2025/...` |
| aggregate entitlements | `POST /beta/sources/{id}/load-entitlements` | 202 + task |
| schemas | `GET/POST /v3/sources/{id}/schemas`, `PUT/DELETE …/schemas/{schemaId}` | |
| schedules | `GET/POST /v2025/sources/{id}/schedules`, `GET/PATCH/DELETE …/schedules/{ACCOUNT_AGGREGATION\|GROUP_AGGREGATION}` | header `X-SailPoint-Experimental: true`; body `{type, cronExpression}` (Quartz, e.g. `0 */5 * * * ?`). Agent datasets (Bedrock/AgentCore) have their own schedule, set on the source's Datasets page |

Create body (verified on a live tenant):
```json
{
  "name": "AWS - jane",
  "description": "AWS SaaS",
  "owner": { "type": "IDENTITY", "id": "<identity id>" },
  "connector": "awssaas",
  "connectorAttributes": {
    "spConnectorSpecId": "<connector .type, e.g. 6e47875b-73f1-481d-a613-59d4deca0c6a>",
    "idnProxyType": "sp-connect",
    "connectionType": "direct",
    "spConnectorSupportsCustomSchemas": true,
    "templateApplication": "AWS SaaS",
    "EnableAccessKeys": true, "EnableHTTPSCredentials": true, "EnableSSHKeys": true,
    "healthCheckTimeout": 60, "assumeRoleDurationInSeconds": 3600, "pageSize": 10,
    "externalId": "<tenant external id>"
  }
}
```
No `cluster` — the SaaS connector doesn't use a virtual appliance. The second group are the UI wizard's defaults;
`spConnectorSupportsCustomSchemas` is required in practice (without it the connector gets no schemas). Unlike the
`sp*` instance fields it can be PATCHed onto an existing source. Without `spConnectorSpecId` the source gets no
`spConnectorInstanceId` and every test fails with a NullPointerException (`ConnectorProxy.getInternalConnector()`);
`sp*` attributes are dropped from PATCH requests, so such a source must be deleted and recreated. Creating an
instance yourself via `POST /beta/connector-instances` and patching its id in doesn't work for the same reason.

## Key map

SailPoint doesn't publish the `connectorAttributes` keys for this connector, so `discover` writes
`~/.cache/sailpoint-isc-aws/<tenant>-keymap.json`:
```json
{
  "connector": "<scriptName>",
  "connectorType": "<spec id, sent as spConnectorSpecId on create>",
  "discoveredVia": "source-config form",
  "allFields": ["…every field name found…"],
  "keys": {
    "roleName": "…", "region": "…", "externalId": "…",
    "managementAccountId": "…", "roleSessionName": "…", "accounts": "…"
  }
}
```
Live mapping (AWS SaaS, 2026-10): roleName→`roleName`, region→`region`, externalId→`externalId`,
managementAccountId→`managementAccountId`, roleSessionName→`assumeRoleSessionName`, changePasswordPolicyArn→`changePasswordPolicyARN` (required by the form),
accounts→`cloudScope`
(array of account-id strings; there is no manage-all field), bedrockEnabled→`enableDiscoverBedrockAgent` (bool),
bedrockRegions→`AgentAwsRegion` (array), agentCoreEnabled→`enableDiscoverBedrockAgentCore` (bool),
agentCoreRegions→`AgentCoreAwsRegion` (array). Other form fields: `pageSize`,
`assumeRoleDurationInSeconds`, `maxRetries`, `baseDelay`, `throttledBaseDelay`,
`maxBackoffTime`, `enableCIEM`, `cloudTrailARN`, `cloudTrailBucketAccountID`, `enableADI`.
Edit `keys` by hand if a mapping is wrong. Most reliable source of truth: create the source once in the UI, fill in
Connection Settings, then `discover --from-source "<name>"` — the keys come straight from that source's
`connectorAttributes`. The `accounts` value is sent as an array of account-id strings; if the tenant stores it in a
different shape, `show` an existing source to see the format and adjust.

UI ↔ concept (SaaS connector): IAM Role Name (name, not ARN), Region (default us-east-1), External ID
(auto-generated, read-only), Management Account ID, AWS Accounts (multi-select, `cloudScope`),
Page Size (1–1000, default 10), Role Session Name / Duration (3600–43200 s).
Timeout keys confirmed in docs: `healthCheckTimeout`, `aggregateTimeout`, `provisioningTimeout`.
