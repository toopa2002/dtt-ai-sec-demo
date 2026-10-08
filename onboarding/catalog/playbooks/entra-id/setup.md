# Microsoft Entra ID — setup steps for the Entra administrator

Ported from `.claude/skills/sailpoint-isc-entra-connector` (commits 9b53746, b71c53c): `scripts/entra-setup.sh` and
`references/entra-permissions.md`; the permission lists come from `permissions/*.json`, the skill's profile files.
The agent gives these steps one at a time, with every value in braces filled in, and checks the pasted output
against **Expect** before moving on. Give only the steps of the capabilities chosen for this session
(capabilities: {capabilities}). The agent never runs these, never signs in to Entra and never needs the
administrator's credentials.

Run everything in a terminal signed in to the Entra tenant `{tenant_domain}` with Azure CLI. Permissions are always
resolved **by name**: never paste permission GUIDs from a web page (delegated and application ids are easy to mix up).

## Step 1 — Check your admin role (read-only)

```bash
az login --tenant {tenant_domain} --allow-no-subscriptions
az account show --query "{tenant:tenantId, user:user.name}" -o json
az rest --url "https://graph.microsoft.com/v1.0/me/memberOf/microsoft.graph.directoryRole" --query "value[].displayName" -o tsv
```

Expect: the tenant of `{tenant_domain}`, and **Global Administrator** or **Privileged Role Administrator** in the role
list. If the role is eligible through PIM, activate it first and run the last command again. Without one of these
roles, stop: admin consent to Graph application permissions needs it (Cloud Application Administrator can't consent
to Graph app roles).

## Step 2 — Check the app name is free (read-only)

```bash
az ad app list --display-name "{app_name}" --query "[].{appId:appId, tags:tags}" -o json
```

Expect: `[]`. If an app is listed, see collisions.md: never change an app that wasn't created for this onboarding.

## Step 3 — Register the SailPoint app (change)

```bash
APP=$(az ad app create --display-name "{app_name}" --sign-in-audience AzureADMyOrg --query appId -o tsv)
az rest --method PATCH --url "https://graph.microsoft.com/v1.0/applications(appId='$APP')" \
  --headers Content-Type=application/json --body '{"tags":["sailpoint-isc-entra-connector"]}'
az ad sp create --id "$APP" --query "{appId:appId, servicePrincipal:id}" -o json
```

Expect: `appId` (the **Application (client) ID**; not secret) and the service principal's object id. The agent
records the Application ID for the session. From here on the steps use `{client_id}`.

## Step 4 — Add the directory read permissions (change) — capability: directory

Microsoft Graph application permissions: `{permissions_readonly}`.

```bash
GRAPH=00000003-0000-0000-c000-000000000000
for p in {permissions_readonly}; do
  ROLE=$(az ad sp show --id $GRAPH --query "appRoles[?value=='$p' && contains(allowedMemberTypes,'Application')].id | [0]" -o tsv)
  az ad app permission add --id {client_id} --api $GRAPH --api-permissions "$ROLE=Role" --only-show-errors
done
```

Expect: no output (or a note that consent is still needed). Nothing here writes to the directory.

## Step 5 — Add the service principal read permissions (change) — capability: service_principals

Microsoft Graph application permissions: `{permissions_machine_identity}`. Custom security attributes need Entra ID P1.

```bash
GRAPH=00000003-0000-0000-c000-000000000000
for p in {permissions_machine_identity}; do
  ROLE=$(az ad sp show --id $GRAPH --query "appRoles[?value=='$p' && contains(allowedMemberTypes,'Application')].id | [0]" -o tsv)
  az ad app permission add --id {client_id} --api $GRAPH --api-permissions "$ROLE=Role" --only-show-errors
done
```

Expect: no output.

## Step 6 — Give SailPoint read access to the Foundry subscriptions (change) — capability: ai_agents

Graph `Application.Read.All`, the Azure Service Management **delegated** permission `user_impersonation`, and two
Azure roles on each subscription with Foundry agents: `{foundry_subscriptions}`. You need Owner or User Access
Administrator on those subscriptions.

```bash
GRAPH=00000003-0000-0000-c000-000000000000
ARM=797f4846-ba00-4fd7-ba43-dac1f8f63013
ROLE=$(az ad sp show --id $GRAPH --query "appRoles[?value=='Application.Read.All' && contains(allowedMemberTypes,'Application')].id | [0]" -o tsv)
az ad app permission add --id {client_id} --api $GRAPH --api-permissions "$ROLE=Role" --only-show-errors
SCOPE=$(az ad sp show --id $ARM --query "oauth2PermissionScopes[?value=='user_impersonation'].id | [0]" -o tsv)
az ad app permission add --id {client_id} --api $ARM --api-permissions "$SCOPE=Scope" --only-show-errors
SP=$(az ad sp show --id {client_id} --query id -o tsv)
for s in $(echo "{foundry_subscriptions}" | tr ',' ' '); do
  for r in "Reader" "Cognitive Services Data Contributor (Preview)"; do
    az role assignment create --assignee-object-id "$SP" --assignee-principal-type ServicePrincipal \
      --role "$r" --scope "/subscriptions/$s" --query "{role:roleDefinitionName, scope:scope}" -o json
  done
done
```

Expect: one assignment per role and subscription. Copilot Studio and Agent 365 agents are not part of this flow.

## Step 7 — Add the write permissions and the User Administrator role (change) — capability: provisioning

This lets SailPoint create, change, disable and delete users and groups. Microsoft Graph application permissions:
`{permissions_provisioning}`, plus the **User Administrator** directory role (needed to set passwords and delete).

```bash
GRAPH=00000003-0000-0000-c000-000000000000
for p in {permissions_provisioning}; do
  ROLE=$(az ad sp show --id $GRAPH --query "appRoles[?value=='$p' && contains(allowedMemberTypes,'Application')].id | [0]" -o tsv)
  az ad app permission add --id {client_id} --api $GRAPH --api-permissions "$ROLE=Role" --only-show-errors
done
SP=$(az ad sp show --id {client_id} --query id -o tsv)
UA=$(az rest --url "https://graph.microsoft.com/v1.0/roleManagement/directory/roleDefinitions?\$filter=displayName eq 'User Administrator'" --query "value[0].id" -o tsv)
az rest --method POST --url https://graph.microsoft.com/v1.0/roleManagement/directory/roleAssignments \
  --headers Content-Type=application/json \
  --body "{\"principalId\":\"$SP\",\"roleDefinitionId\":\"$UA\",\"directoryScopeId\":\"/\"}" --query id -o tsv
```

Expect: a role assignment id. Never add Directory.ReadWrite.All or Privileged Authentication Administrator: they are
broader than this flow needs.

## Step 8 — Grant admin consent (change)

```bash
az ad app permission admin-consent --id {client_id}
```

Expect: no output. If it says the service principal isn't found, wait a minute and run it again (replication).

## Step 9 — Confirm consent for every permission (read-only)

```bash
GRAPH=00000003-0000-0000-c000-000000000000
SP=$(az ad sp show --id {client_id} --query id -o tsv)
GRAPH_SP=$(az ad sp show --id $GRAPH --query id -o tsv)
for id in $(az rest --url "https://graph.microsoft.com/v1.0/servicePrincipals/$SP/appRoleAssignments" --query "value[?resourceId=='$GRAPH_SP'].appRoleId" -o tsv); do
  az ad sp show --id $GRAPH --query "appRoles[?id=='$id'].value | [0]" -o tsv
done | sort
```

Expect: exactly the Graph permissions of the chosen capabilities, each once:
directory `{permissions_readonly}`; service principals `{permissions_machine_identity}`; AI agents
`Application.Read.All`; provisioning `{permissions_provisioning}`. Compare name by name and say which ones are
missing (consent not granted, or the permission was never added): repeat step 4–7 for those, then step 8.
**Application.Read.All** is the one most often missing: without it entitlement aggregation fails on app roles.

## Step 10 — Put a client secret into the secret field (change)

The secret is written to a file only you can read, so it never shows on screen:

```bash
( umask 077; az ad app credential reset --id {client_id} --append --display-name "SailPoint ISC" --years 1 \
    --query password -o tsv > "$HOME/.sailpoint-entra.secret" )
az ad app credential list --id {client_id} --query "[?displayName=='SailPoint ISC'].endDateTime" -o tsv
```

Then open `$HOME/.sailpoint-entra.secret`, copy the **Value** into the **secret field** on your screen with the
expiry date the second command printed, and delete the file (`rm "$HOME/.sailpoint-entra.secret"`).
**Never paste the secret into the chat**: a secret pasted there is masked and must be replaced. The secret's *ID*
(a GUID) is not the Value; the secret field rejects it.

Expect: the secret field shows "Received" with the expiry date. Nothing is pasted in the chat for this step.

## Step 11 — Show the app's directory role (read-only) — capability: provisioning

```bash
SP=$(az ad sp show --id {client_id} --query id -o tsv)
az rest --url "https://graph.microsoft.com/v1.0/roleManagement/directory/roleAssignments?\$filter=principalId eq '$SP'&\$expand=roleDefinition" \
  --query "value[].roleDefinition.displayName" -o tsv
```

Expect: `User Administrator`. Together with step 9 showing the write permissions, this is the provisioning proof: the
session writes nothing to the directory, so the first real joiner is the first write.
