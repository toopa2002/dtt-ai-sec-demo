---
name: sailpoint-isc-entra-connector
description: Onboard Microsoft Entra ID (Azure AD) into SailPoint Identity Security Cloud (ISC / IdentityNow) with the cloud "Microsoft Entra" SaaS connector. Azure CLI builds the app registration, Graph / Exchange Online permissions, admin consent, secret or certificate and directory roles; the ISC v2026 API creates and configures the source and proves it works with peek, test connection and account, entitlement and dataset (AI agent) aggregation. Ships the full template set for both sides (permission profiles for read-only, provisioning, machine identities, AI agents, Exchange, Teams, PIM; ISC source, toggles, provisioning policy, correlation, lifecycle, schedules). Use whenever someone wants to connect Entra or Azure AD to SailPoint, pick the Graph permissions or roles SailPoint needs, set up or verify an Entra source, aggregate Entra users/groups or Foundry/Copilot agents, set up joiner/mover/leaver to Entra, asks for the templates, or troubleshoots AADSTS7000215 or 403 errors on a SailPoint Entra source.
---

# SailPoint ISC — Microsoft Entra (SaaS) connector setup

Two bundled commands do the work; you collect inputs, run them in dry-run, show the plan, and only then apply.

| Command | Side | What it does |
|---|---|---|
| `scripts/entra-setup.sh` | Entra (az CLI) | App registration → service principal → API permissions + admin consent → client secret (to a chmod 600 file) → directory roles → optional Exchange cert / Azure RBAC |
| `scripts/isc-source.sh` | ISC (REST) | `discover` → `create` → `configure` → **`verify`** (peek → test → aggregate) → optional `provisioning-policy`, `correlation`, `schedule` |

Both default to **dry-run** (read-only lookups run, writes are printed). Add `--apply` only after the user has seen
the dry-run and agreed — these grant tenant-wide Graph permissions and directory roles, and create a source in
their ISC tenant. Both also take `--plan-only`: no Azure/ISC calls at all, no login or token needed, ids shown as
`<placeholders>` — use it when the user wants to see the plan first, or to hand it to the admin who will apply it.

Scope: the cloud **Microsoft Entra** connector (displayed as "Microsoft Entra", scriptName `Microsoft-Entra`;
older docs call it "Microsoft Entra SaaS"). ISC also lists the VA-based "Azure Active Directory" connector and
look-alikes ("Microsoft Entra SSO", "Activity Insights - Microsoft Entra ID", "CIEM Azure"). A source can't be
switched between connectors later, so if the user really needs the VA connector, say so rather than forcing this flow.

## ISC API: v2026 only

The ISC side uses one API version, **v2026** — the reference is
https://developer.sailpoint.com/redoc/sailpoint-api-v2026-light.html. No `/beta/`, no v3/v2025 mixing. Every v2026
endpoint the scripts call is GA except two, which need `X-SailPoint-Experimental: true` (the script adds it only to
them): dataset aggregation `POST /v2026/sources/{id}/aggregate-agents` and the machine-identity count
`GET /v2026/machine-identities`. One live-tested quirk: v2026 `/accounts` filters on `source.id` — `sourceId` (still in
the spec text) is rejected with 400. If you call the API by hand, keep to v2026 and check `references/isc-api.md`,
which lists each call with its body and where the task id is.

## Templates

Every file the two commands apply lives in `assets/` and is plain, editable JSON with `${VAR}` placeholders.
When the user asks *what will be applied* or *for the templates*, list them from `references/templates.md` (each
file, its side, the command that applies it, placeholders, manual equivalent) — don't invent a different set. Edit
a template rather than the scripts when the user wants something different.

- Entra: `assets/entra/app-registration.tmpl.json`, `permissions/<profile>.json` (7 profiles),
  `app-role-assignment.tmpl.json`, `oauth2-permission-grant.tmpl.json`, `directory-roles.json`,
  `role-assignment.tmpl.json`, `azure-rbac.json`
- ISC: `assets/isc/source-create.tmpl.json`, `source-configure.patch.tmpl.json`, `feature-toggles.json`,
  `aggregate-datasets.tmpl.json`, `provisioning-policy-create.tmpl.json`, `correlation-config.tmpl.json`,
  `lifecycle-state-account-actions.patch.tmpl.json`, `schedule.tmpl.json`

## Permission profiles

`--profiles` takes a comma list; `readonly` is always included. The same names work on both commands.

| Profile | Entra side | ISC side (toggles) |
|---|---|---|
| `readonly` | Graph User.Read.All, Group.Read.All, Organization.Read.All, RoleManagement.Read.Directory, Application.Read.All, AuditLog.Read.All | — |
| `provisioning` | + User/Group ReadWrite, invite, enable/disable, password profile, role + app-role assignment write; **User Administrator** role | CREATE provisioning policy (`provisioning-policy`) |
| `machine-identity` | Application.Read.All, DelegatedPermissionGrant.Read.All, Device.Read.All | manageAzureServicePrincipalAsAccount, managed identities |
| `ai-agents` | Application.Read.All, Azure Service Management user_impersonation (delegated); Azure RBAC (Reader + Cognitive Services Data Contributor) on `--foundry-subscriptions` | enableAIFoundryAgent on → `azure:foundry` dataset; Copilot Studio / Agent 365 toggles set **off** (they need extra setup) |
| `exchange` | Exchange Online Exchange.ManageAsApp; **Exchange Administrator** role; cert via `--exchange-cert` | manageExchangeOnline, aggregateAllGroups, cert |
| `teams` | Teams/channel/app read set | enableTeamsGovernance |
| `pim` | PIM-for-Groups + directory role schedule reads | enablePIM, spnManageAzureADPIM |

Ask which features the user actually needs — every extra profile widens what the app can read or change. Mention
that `provisioning` lets ISC write to the directory and `--manage-admin-users` (Privileged Authentication
Administrator) lets it reset admins' passwords; only add those on request. Rationale per permission, least-privilege
alternatives and manual-only steps (Copilot Studio, Agent 365, Azure-resource PIM): `references/entra-permissions.md`.

## Inputs to collect

- **Entra:** an `az login --tenant <tenant> --allow-no-subscriptions` session as **Global Administrator or
  Privileged Role Administrator** (needed to consent to Graph application permissions and assign directory
  roles; if the role is PIM-eligible, activate it first); app name (default "SailPoint ISC Entra Connector");
  profiles; secret lifetime (default 1 year); for `ai-agents`, the subscriptions where Foundry agents live.
- **ISC:** tenant as its full API host — `ISC_TENANT=acme.api.identitynow.com` (or a demo host like
  `acme-demo.api.identitynow-demo.com`; the UI host is accepted and converted; `ISC_BASE_URL` overrides). Plus a
  personal access token (`ISC_CLIENT_ID`/`ISC_CLIENT_SECRET`, ORG_ADMIN or SOURCE_ADMIN), source name, owner. For
  provisioning: the UPN domain new accounts get, and the usage location.

Ask the user to keep the PAT in a `chmod 600` env file (e.g. `~/.config/sailpoint-isc.env`, loaded with
`set -a; . ~/.config/sailpoint-isc.env; set +a`) rather than pasting it into chat. The Entra client secret never needs
to pass through the chat either: `entra-setup.sh` writes it to `~/.config/sailpoint-entra/<tenant>-<app>.secret` and
`isc-source.sh` reads it from there. Don't `cat` either file.

## Workflow

```bash
S=.claude/skills/sailpoint-isc-entra-connector/scripts   # from the repo root

# 1. Entra: dry-run (tenant, your roles, every permission by name, each grant/role call), then apply
$S/entra-setup.sh --tenant contoso.onmicrosoft.com --profiles provisioning
$S/entra-setup.sh --tenant contoso.onmicrosoft.com --profiles provisioning --apply   # -> ./sailpoint-entra-setup.json

# 2. ISC: find the connector + existing Entra sources, create, configure from the setup file
$S/isc-source.sh discover
$S/isc-source.sh create --name "Entra ID - Contoso" --owner me --apply
$S/isc-source.sh configure --source "Entra ID - Contoso" --from-setup sailpoint-entra-setup.json --apply

# 3. Prove it works: peek -> test -> aggregate (entitlements, accounts, datasets); stops at the first failure
$S/isc-source.sh verify --source "Entra ID - Contoso" --apply

# AI agents (Azure AI Foundry): Entra side + source toggle, aggregate the dataset, then schedule it
$S/entra-setup.sh --app-id <appId> --profiles ai-agents --foundry-subscriptions <subId> --apply
$S/isc-source.sh configure --source "Entra ID - Contoso" --from-setup sailpoint-entra-setup.json --apply
$S/isc-source.sh aggregate --source "Entra ID - Contoso" --only dataset --apply     # API 404 on some tenants -> UI
$S/isc-source.sh dataset-schedule --source "Entra ID - Contoso" --on --datasets azure:foundry --apply

# 4. Optional: joiner provisioning, correlation, scheduled aggregation (Quartz cron, seconds first)
$S/isc-source.sh provisioning-policy --source "Entra ID - Contoso" --upn-domain contoso.com --apply
$S/isc-source.sh correlation --source "Entra ID - Contoso" --apply
$S/isc-source.sh schedule --source "Entra ID - Contoso" --cron "0 0 */4 * * ?" --types account,group --apply
```

Notes per step:

1. **Look at what exists first.** `entra-setup.sh`'s dry-run reports the signed-in user's active roles and whether
   an app with that name exists; it won't touch an app it didn't create (tag `sailpoint-isc-entra-connector`)
   unless `--reuse`, and then it only *adds* permissions to that app. To fix an existing app the user identifies by
   its Application (client) ID — the usual troubleshooting case — pass `--app-id <appId>` (implies `--reuse`). `discover` lists existing sources on the
   connector with owner and domain — if one already covers this Entra tenant, ask before adding a second. `create`
   refuses to reuse a same-named source owned by someone else.
2. **Rerunning `entra-setup.sh` is safe**: permissions are resolved by name, existing grants and role assignments
   are skipped, and the secret file is kept unless `--rotate-secret`. For the skill's own app, `--profiles` sets the
   app's permission list to exactly those profiles, so to add one later rerun with the complete list.
3. **Consent propagates slowly.** Grants against a new service principal are retried for a minute; even after
   success Graph can answer 401/403 for a few minutes. If the first peek fails with AADSTS700016 or 403 right after
   setup, wait 2–5 minutes and retry before debugging.
4. **discover** writes a key map (`~/.cache/sailpoint-isc-entra/<tenant>-keymap.json`): connector scriptName, the
   connector spec id (`connectorType`), canonical setting → form field, and the `grantType` values on the form.
   Show it to the user; a `null` on a required key means the form changed — fix it from `.allFields`, or build a
   source in the UI and run `discover --from-source <name>`.
5. **create** sends `spConnectorSpecId` + `idnProxyType: sp-connect` so ISC provisions the connector instance; they
   can't be added later — a source created without them must be deleted and recreated.
6. **configure** uses the tenant's initial `*.onmicrosoft.com` domain (custom domains aren't supported for CIEM), the
   **Application (client) ID**, the secret **Value** (a GUID in the secret file is the secret's ID and is rejected)
   and `grantType` `CLIENT_CREDENTIALS`. It adds each profile's toggles from `feature-toggles.json`, skipping (and
   reporting) any the tenant's form doesn't have.
7. **verify = peek → test → aggregate**, in that order, each only if the previous passed:
   - *peek* reads 5 accounts through the connector — proves app, consent, secret and domain against live data.
   - *test* runs Test Connection on the whole source configuration. `Provided source configuration already
     exists` is a known connector issue (CIEM toggle workaround in `references/troubleshooting.md`).
   - *aggregate* runs **entitlements first, then accounts** (accounts then link to entitlements that already
     exist), **then datasets**. Datasets are the machine-identity / AI-agent collections — `azure:foundry`,
     `microsoft:copilot`, `microsoft:agent365` — and only those switched on on the source run (toggles
     `enableAIFoundryAgent`, `enableCopilotAIAgent`, `enableMicrosoftAgent365`, read from `GET /v2026/sources/{id}`).
     None switched on is normal for a plain directory onboarding; say so instead of treating it as a failure. (The
     dataset list's `aggregationEnabled` only means *scheduled* aggregation is on — it stays false after successful
     manual runs. Turn it on with `isc-source.sh dataset-schedule --source … --on [--datasets azure:foundry]`, which
     replays the UI's "Enable Schedule" call — `PUT /v2026/sources/{id}/datasets/{datasetId}` with the dataset object
     and `aggregationEnabled` flipped. That endpoint is not in the published v2026 spec (captured from the UI and used
     live), so tell the user it is undocumented; the frequency is ISC's default and isn't settable through it.) Seen live: a tenant that doesn't expose the API answers `aggregate-agents` with 404 "endpoint is unavailable"
     even though the UI can aggregate the same dataset — then run it from the UI; it is not a wrong path or version.
     Only switch on datasets whose setup is done: Agent 365 fails without a user refresh token
     (`agent365RefreshToken`), Copilot Studio without the Power Platform application user. Each aggregation is polled to completion and the account/entitlement totals are
     printed. Run a single kind with `aggregate --only account|entitlement|dataset` (or `--datasets ID,ID`).
8. **Joiner/mover/leaver.** `provisioning-policy` sets the CREATE policy ISC uses for new Entra accounts (UPN
   from first.last@`--upn-domain`, password via SailPoint's Create Password rule, usage location — needed before a
   licence can be assigned); a UI-built source already has one, which is kept unless `--replace`. `correlation`
   matches accounts to identities (email = userPrincipalName, then mail; put an employee-number rule first if HR
   writes employeeId to Entra). Leaver/joiner account actions belong to the identity profile's lifecycle states,
   which other sources share, so the skill only ships the template — apply it with the user, merging existing
   actions (see `references/templates.md`).

## Verification status

Run end to end on a live tenant pair (2026-10-08, `readonly` profile): `entra-setup.sh --apply` (app, SP, consent,
secret), `--app-id` re-run (idempotent, added one grant), then `create` → `configure --from-setup` → `verify`: peek,
test (SUCCESS), entitlement and account aggregation (SUCCESS) on v2026. Also checked live: connector name/scriptName/spec
id, the form fields, `grantType` values and dataset ids. Dataset aggregation: `azure:foundry` aggregated live
from the UI (2 AI-agent machine identities, counted with `GET /v2026/machine-identities`) and its schedule was
turned on with the dataset PUT that `dataset-schedule` replays; that tenant answered the
`aggregate-agents` API with 404 "endpoint is unavailable", so a successful scripted run is still unverified. The `ai-agents` Entra side (delegated consent, Reader + Cognitive Services Data Contributor) applied live
fine. Not yet exercised live: the provisioning / Exchange / PIM profiles, `provisioning-policy` and `correlation`
writes. Tell the user which of these a run relies on, and read errors literally.

## Troubleshooting

Match the AADSTS code or HTTP status in the error against `references/troubleshooting.md`. The usual suspects:
secret ID pasted instead of Value (AADSTS7000215) or secret expired (AADSTS7000222); permission requested but never
consented, or the Graph application permission missing for the operation (403 Authorization_RequestDenied — a Global
Administrator *role* on the app doesn't substitute for Graph permissions); Conditional Access blocking workload
identities; wrong Domain Name (AADSTS90002). Check what the app has actually been *granted* before changing
anything: `az rest --url https://graph.microsoft.com/v1.0/servicePrincipals/<spId>/appRoleAssignments` — or rerun
`entra-setup.sh --app-id <appId> --profiles <what it needs>` in dry-run, which marks each permission "already
granted" or prints the missing grant (and keeps the app's other permissions).

## Reference files

- `references/templates.md` — every template file, which command applies it, placeholders, manual equivalent.
- `references/entra-permissions.md` — each permission and role and why, least-privilege notes, manual az steps,
  Copilot Studio / Agent 365 / Azure PIM manual steps.
- `references/isc-api.md` — endpoint table (versions, bodies, where the task id is), key map, connector fields.
- `references/troubleshooting.md` — known errors and fixes.

## Reporting back

Finish with: Entra tenant + domain, app name, Application (client) ID, service principal object id, profiles and
the permissions/roles actually granted, where the secret file is and when the secret expires (tell them to rotate
before then), ISC source id and name, the verify results (peek count, test status, each aggregation's task id and
status, account/entitlement totals, which datasets ran or why none did), plus open manual steps (UI toggles,
Power Platform, Azure RBAC, lifecycle states). If anything was deleted, recreated or reused, say so.
