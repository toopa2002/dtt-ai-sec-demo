# Template files

All templates are valid JSON (check with `jq empty`). `${VAR}` placeholders are filled by `render_tmpl` in
`scripts/lib.sh`: a string that is exactly `"${VAR}"` takes the variable's JSON value (so arrays/booleans keep
their type), a placeholder inside a longer string is substituted as text, and an unset variable is an error.
Keys starting with `_` are notes and ignored.

To render one by hand (e.g. to apply it yourself or give it to an admin):

```bash
source .claude/skills/sailpoint-isc-entra-connector/scripts/lib.sh
render_tmpl assets/entra/role-assignment.tmpl.json '{"SP_OBJECT_ID":"<sp-id>","ROLE_DEFINITION_ID":"<role-id>"}'
```

## Entra ID (applied by `scripts/entra-setup.sh` with the Azure CLI)

| # | File | Applied as | Placeholders | Manual equivalent |
|---|---|---|---|---|
| 1 | `assets/entra/app-registration.tmpl.json` | `POST /v1.0/applications` (new) or `PATCH` its `requiredResourceAccess` (rerun) | `APP_NAME`, `REQUIRED_RESOURCE_ACCESS` (built from the profiles below) | `az ad app create --display-name … --sign-in-audience AzureADMyOrg` then `az ad app permission add` per permission |
| 2 | `assets/entra/permissions/readonly.json` | permission set (always) | — | see entra-permissions.md |
| 3 | `assets/entra/permissions/provisioning.json` | permission set | — | 〃 |
| 4 | `assets/entra/permissions/machine-identity.json` | permission set | — | 〃 |
| 5 | `assets/entra/permissions/ai-agents.json` | permission set (incl. delegated Azure Service Management) | — | 〃 |
| 6 | `assets/entra/permissions/exchange.json` | permission set (Office 365 Exchange Online) | — | 〃 |
| 7 | `assets/entra/permissions/teams.json` | permission set | — | 〃 |
| 8 | `assets/entra/permissions/pim.json` | permission set | — | 〃 |
| 9 | `assets/entra/app-role-assignment.tmpl.json` | `POST /v1.0/servicePrincipals/{sp}/appRoleAssignments`, one per application permission (= admin consent) | `SP_OBJECT_ID`, `RESOURCE_SP_ID`, `APP_ROLE_ID` | `az ad app permission admin-consent --id <appId>` (all at once) |
| 10 | `assets/entra/oauth2-permission-grant.tmpl.json` | `POST /v1.0/oauth2PermissionGrants` (tenant-wide consent for delegated scopes) | `SP_OBJECT_ID`, `RESOURCE_SP_ID`, `SCOPES` | same `admin-consent` |
| 11 | `assets/entra/directory-roles.json` | which directory roles to assign, per profile / opt-in flag | — | — |
| 12 | `assets/entra/role-assignment.tmpl.json` | `POST /v1.0/roleManagement/directory/roleAssignments`, one per role | `SP_OBJECT_ID`, `ROLE_DEFINITION_ID` | Entra admin center → Roles and administrators → add assignment |
| 13 | `assets/entra/azure-rbac.json` | `az role assignment create` per role per `--foundry-subscriptions` (ai-agents) | `SUBSCRIPTION_ID` (in `scope`) | same command |

Not templated, done by command: service principal (`az ad sp create --id <appId>`), client secret
(`az ad app credential reset --id <appId> --append --display-name "SailPoint ISC" --years N`), Exchange
certificate (`openssl req -x509 …` + `az ad app credential reset --id <appId> --cert @cert.pem --append`).

### Permission profile format

```jsonc
{
  "profile": "name",
  "description": "…",
  "resources": [
    { "api": "Microsoft Graph", "appId": "00000003-0000-0000-c000-000000000000",
      "appRoles":  [ { "name": "User.Read.All", "why": "…" } ],      // application permissions (type Role)
      "delegated": [ { "name": "user_impersonation", "why": "…" } ] } // delegated scopes (type Scope), optional
  ]
}
```

Names are resolved to ids at run time from the resource's service principal (`appRoles[].value`,
`oauth2PermissionScopes[].value`); an unknown name stops the run. Add or remove entries freely; a new profile is
just a new file here plus an entry in `assets/isc/feature-toggles.json`.

## SailPoint ISC (applied by `scripts/isc-source.sh` via the v2026 REST API)

| # | File | Applied as | Placeholders |
|---|---|---|---|
| 14 | `assets/isc/source-create.tmpl.json` | `POST /v2026/sources` (`create`) | `SOURCE_NAME`, `SOURCE_DESCRIPTION`, `OWNER_ID`, `CONNECTOR_SCRIPT`, `CONNECTOR_SPEC_ID` (from `discover`) |
| 15 | `assets/isc/source-configure.patch.tmpl.json` | `PATCH /v2026/sources/{id}` JSON-Patch (`configure`) | `DOMAIN_NAME`, `CLIENT_ID`, `CLIENT_SECRET` (read from the secret file), `GRANT_TYPE` (`CLIENT_CREDENTIALS`) |
| 16 | `assets/isc/feature-toggles.json` | extra JSON-Patch ops per profile (`configure --profiles`) | — |
| 17 | `assets/isc/aggregate-datasets.tmpl.json` | `POST /v2026/sources/{id}/aggregate-agents` (`aggregate`/`verify`, dataset step) | `DATASET_IDS` (array; default = datasets with aggregation enabled) |
| 18 | `assets/isc/provisioning-policy-create.tmpl.json` | `POST /v2026/sources/{id}/provisioning-policies`, or `PUT …/provisioning-policies/CREATE` with `--replace` (`provisioning-policy`) | `UPN_DOMAIN`, `USAGE_LOCATION` |
| 19 | `assets/isc/correlation-config.tmpl.json` | `PUT /v2026/sources/{id}/correlation-config` (`correlation`) | `CORRELATION_CONFIG_ID`, `CORRELATION_CONFIG_NAME` (taken from the current config) |
| 20 | `assets/isc/lifecycle-state-account-actions.patch.tmpl.json` | **not scripted** — `PATCH /v2026/identity-profiles/{ip}/lifecycle-states/{ls}` | `ACCOUNT_ACTIONS` = the state's existing `accountActions` **plus** e.g. `{"action":"DISABLE","sourceIds":["<entra source id>"]}` (leaver) or `ENABLE` (joiner/rehire) |
| 21 | `assets/isc/schedule.tmpl.json` | `POST /v2026/sources/{id}/schedules` (`schedule`) | `SCHEDULE_TYPE` (ACCOUNT_AGGREGATION / GROUP_AGGREGATION), `CRON` |
| 22 | *(no file — built from the live object)* | `GET` then `PUT /v2026/sources/{id}/datasets/{datasetId}` (`dataset-schedule --on/--off`); undocumented, the UI's "Enable Schedule" call | the dataset object from the GET, with `aggregationEnabled` = true/false |
| 23 | `assets/isc/account-schema-spn-attributes.json` | `PATCH /v2026/sources/{id}/schemas/{accountSchemaId}` with `add /attributes/-` per missing attribute (`schema-spn`; run by `configure` for machine-identity) | — (entitlement `schema` names resolved to ids at run time) |
| 24 | `assets/isc/machine-classification-config.json` | `PUT /v2026/sources/{id}/machine-classification-config` (`classification`; run by `configure` for machine-identity), then `POST /v2026/sources/{id}/classify` with `--process` (and by `verify` after account aggregation) | — (UI: Machine Accounts → Classification → Enable + Customize classification) |
| — | `assets/isc/keymap.default.json` | not applied; field-name map used by `--plan-only` | — |

Paths in #15/#16 use SailPoint's field names (verified on a live form); `configure` maps them through the discovered
key map, so a tenant whose form names a field differently still works, and fields absent from the form are skipped
with a warning. #20 is left to a human because lifecycle states belong to an identity profile that other sources
share — replacing `accountActions` without the existing entries would drop other sources' leaver actions.

Manual equivalent for #14–#15 with curl (token from `POST /oauth/token`, client_credentials with the PAT):

```bash
curl -X POST "$ISC_BASE_URL/v2026/sources" -H "Authorization: Bearer $T" -H 'Content-Type: application/json' -d @create.json
curl -X PATCH "$ISC_BASE_URL/v2026/sources/$ID" -H "Authorization: Bearer $T" \
     -H 'Content-Type: application/json-patch+json' -d @configure.json
```

## Output files (not templates)

- `./sailpoint-entra-setup.json` — written by `entra-setup.sh --apply`: tenant, domain, client id, SP id, profiles,
  granted permissions, roles, secret **file path** + expiry, Exchange cert files. No secret values. Input for
  `isc-source.sh configure --from-setup`.
- `~/.config/sailpoint-entra/<tenant>-<app>.secret` (chmod 600) — the client secret value.
- `~/.config/sailpoint-entra/<tenant>-<app>-exo.{key,crt,pfx,pfx.pass,pfx.b64}` — Exchange certificate material.
- `~/.cache/sailpoint-isc-entra/<tenant>-keymap.json` — from `discover`.
