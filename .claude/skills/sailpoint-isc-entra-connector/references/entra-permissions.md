# Entra permissions and roles for the Microsoft Entra SaaS connector

Sources: SailPoint "Microsoft Entra SaaS" docs (Required Permissions, Service Principal Account Management,
Exchange Online Mailbox Management, Agent Governance Settings, Copilot Studio Agents, Agent 365 Catalog) under
https://documentation.sailpoint.com/connectors/saas/msentraid/help/saas_connectivity/microsoft_entra_id/ ;
Microsoft Graph permissions reference; `az ad app` / `az rest` docs. Items marked *(not on SailPoint's list)* are
Graph requirements for the API the feature uses, added here because the SailPoint page is silent.

## How SailPoint reaches Entra

The connector authenticates as the app registration (client credentials: Application (client) ID + secret
**Value**, or JWT certificate credentials, or refresh token for Agent 365) against the tenant's Domain Name, and
calls Microsoft Graph with **application** permissions. Application permissions only take effect after admin
consent, i.e. an app role assignment on the resource's service principal — adding them to the app registration
alone does nothing. Consent to Graph application permissions, and assigning directory roles, needs Global
Administrator or Privileged Role Administrator (Cloud Application Administrator cannot consent to Graph app roles).

## Per profile

### readonly (always)
| Permission | Why |
|---|---|
| User.Read.All | accounts, delta aggregation, get object, memberships |
| Group.Read.All | groups and memberships |
| Organization.Read.All | licences (subscribedSkus) |
| RoleManagement.Read.Directory | directory roles as entitlements |
| Application.Read.All | `applicationRole` entitlements (enterprise-app roles); without it entitlement aggregation fails with 403 on that object type (seen live) |
| AuditLog.Read.All | `signInActivity` (last sign-in); drop it if the user doesn't want sign-in data |

Broader alternative: `Directory.Read.All` covers the first four. Prefer the granular set.
Risky-user attributes (`riskLevel`, `riskState`, `riskDetail`, `riskLastUpdatedDateTime`) need
`IdentityRiskyUser.Read.All` *(not on SailPoint's list; Entra ID P2)* — or delete those attributes from the
account schema, which SailPoint recommends if aggregation 403s on them.

### provisioning
| Permission / role | Why |
|---|---|
| User.ReadWrite.All | create/update users, licences |
| Group.ReadWrite.All | create/update/delete groups, membership (incl. M365 groups) |
| User.Invite.All | B2B guest invitations |
| User.EnableDisableAccount.All | enable/disable, delete |
| User-PasswordProfile.ReadWrite.All | set/reset passwords |
| RoleManagement.ReadWrite.Directory | add/remove directory roles |
| AppRoleAssignment.ReadWrite.All | add/remove users on enterprise apps |
| **User Administrator** (directory role) | password set/reset and delete need it in addition to the Graph permissions |
| **Privileged Authentication Administrator** (opt-in `--manage-admin-users`) | only to manage users who hold admin roles; very powerful |

Not included: `Application.ReadWrite.All` (create/update service principals from ISC). Add it to
`provisioning.json` only if the user wants ISC to write service principals. `Directory.ReadWrite.All` is the
broad alternative SailPoint mentions; avoid it.

### machine-identity
Application.Read.All (service principals, app registrations, managed identities, app roles),
DelegatedPermissionGrant.Read.All (admin-consented permissions as entitlements), Device.Read.All (NHI discovery).
Turn on "Manage Azure Service Principal as Account" / managed identity management on the source (the profile's
toggles). Machine Identity Governance needs the SailPoint Agentic Fabric licence.

### ai-agents (Azure AI Foundry)
- Graph Application.Read.All.
- Windows Azure Service Management API (`797f4846-ba00-4fd7-ba43-dac1f8f63013`) **delegated** `user_impersonation`,
  granted tenant-wide by the script (oauth2PermissionGrant, consentType AllPrincipals).
- Azure RBAC on each subscription with agents: Reader + Cognitive Services Data Contributor (Preview)
  (`azure-rbac.json`, `--foundry-subscriptions`). The caller needs Owner/User Access Administrator there.
- ISC: "Enable Azure AI Foundry Agents" on Machine Identity Governance Settings (UI; field name unpublished).

Manual-only, not scriptable with az:
- **Copilot Studio agents:** in each Power Platform environment (admin center → Environments → Settings → Users +
  permissions → Application users) add the app as an application user with the "Global Discovery Service Role"
  and a custom "BotReader" security role (prvReadbot, prvWritebot, prvReadbotcomponent, prvWritebotcomponent at
  Organization level). Then enable "Microsoft Copilot Studio Agents" on the source.
- **Microsoft Agent 365 catalog:** delegated CopilotPackages.ReadWrite.All (admin consent), offline_access,
  User.Read, and the source must use the **Refresh Token** grant type; needs an Agent 365 licence.
- Entra Agent ID objects: no documented SailPoint support yet — say so if asked.

### exchange
- Office 365 Exchange Online (`00000002-0000-0ff1-ce00-000000000000`) application permission Exchange.ManageAsApp.
  The tenant must have Exchange Online, or that service principal doesn't exist.
- **Exchange Administrator** directory role on the app.
- Certificate auth (basic auth is deprecated): `--exchange-cert` generates a self-signed cert, adds the public
  part to the app, and keeps `.pfx` (+ base64 + password file) in the secret dir for the source's "Exchange
  Certificate" / "Exchange Private Key Password". Bring-your-own cert:
  `az ad app credential reset --id <appId> --cert @cert.pem --append`.
- Source: Manage Exchange Online on; Aggregate All Groups on (distribution lists / mail-enabled groups).

### teams
Channel.ReadBasic.All, ChannelMember.Read.All, ChannelSettings.Read.All, TeamSettings.Read.All, TeamsTab.Read.All,
TeamsAppInstallation.ReadFor{Team,User,Chat}.All, AppCatalog.Read.All. SailPoint's NHI/Copilot discovery list
also has content-reading permissions (ChannelMessage.Read.All, Chat.Read.All, Files.Read.All, Sites.Read.All,
AiEnterpriseInteraction.Read.All, Reports.Read.All) and TeamsAppInstallation.ReadWrite* — left out on purpose
(they read message/file content or write); add to `teams.json` only for a feature that needs them.

### pim
PrivilegedAccess.Read.AzureADGroup, PrivilegedAssignmentSchedule.Read.AzureADGroup,
PrivilegedEligibilitySchedule.Read.AzureADGroup (SailPoint); RoleEligibilitySchedule.Read.Directory,
RoleAssignmentSchedule.Read.Directory *(not on SailPoint's list — Graph PIM for directory roles)*.
Azure resource (RBAC) PIM additionally needs Owner or User Access Administrator for the app at the Tenant Root
Group or subscription (`az role assignment create --assignee-object-id <spId> --assignee-principal-type
ServicePrincipal --role "User Access Administrator" --scope /providers/Microsoft.Management/managementGroups/<tenantId>`)
— not done by the script; it's a big grant, ask first.

### Other features (no profile)
Access packages: EntitlementManagement.Read.All / .ReadWrite.All. MFA methods: UserAuthenticationMethod.Read.All /
.ReadWrite.All. Custom security attributes: directory roles Attribute Assignment Reader / Administrator.
Defender: Machine.Read.All, ThreatHunting.Read.All (WindowsDefenderATP API). Make a new profile file if needed.

## Manual az commands (what the script does, step by step)

```bash
GRAPH=00000003-0000-0000-c000-000000000000
APP_ID=$(az ad app create --display-name "SailPoint ISC Entra Connector" --sign-in-audience AzureADMyOrg --query appId -o tsv)
SP_ID=$(az ad sp create --id "$APP_ID" --query id -o tsv)
rid() { az ad sp show --id "$1" --query "appRoles[?value=='$2' && contains(allowedMemberTypes,'Application')].id | [0]" -o tsv; }
for p in User.Read.All Group.Read.All Organization.Read.All RoleManagement.Read.Directory AuditLog.Read.All; do
  az ad app permission add --id "$APP_ID" --api $GRAPH --api-permissions "$(rid $GRAPH $p)=Role"
done
az ad app permission admin-consent --id "$APP_ID"     # Global Admin / Privileged Role Admin; retry if it says the SP isn't found
umask 077; az ad app credential reset --id "$APP_ID" --append --display-name "SailPoint ISC" --years 1 --query password -o tsv > app.secret
ROLE=$(az rest --url "https://graph.microsoft.com/v1.0/roleManagement/directory/roleDefinitions?\$filter=displayName eq 'User Administrator'" --query 'value[0].id' -o tsv)
az rest --method POST --url https://graph.microsoft.com/v1.0/roleManagement/directory/roleAssignments \
  --body "{\"principalId\":\"$SP_ID\",\"roleDefinitionId\":\"$ROLE\",\"directoryScopeId\":\"/\"}"
```

Never hard-code permission GUIDs copied from web pages — the Graph reference lists separate delegated and
application ids and they are easy to mix up; resolve by name as above.
