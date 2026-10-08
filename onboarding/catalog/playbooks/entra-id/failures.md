# Microsoft Entra ID — known failures (spec 002 FR-140, SC-103)

Ported from `.claude/skills/sailpoint-isc-entra-connector/references/troubleshooting.md` (commits 9b53746, b71c53c).
For each: how it shows up (text, or what it looks like on a screenshot), which side it's on, the cause, a read-only
step to confirm, the fix, and whether to retry once. Read error codes literally; check what the app has actually been
*granted* (setup step 9) before changing anything.

## E1 — Invalid client secret
- Signature: `AADSTS7000215: Invalid client secret provided` on the connection check or Test Connection; on screen,
  a red banner on the source with that code.
- Side: application (Entra).
- Cause: the secret's **ID** (a GUID) was used instead of its **Value**, or the secret belongs to another app.
- Confirm: `az ad app credential list --id {client_id} --query "[].{name:displayName, ends:endDateTime}" -o table`
  shows the "SailPoint ISC" secret exists for this app.
- Fix: a **new secret through the secret field** (setup step 10). Call `request_new_secret` with the reason. Never ask
  for the secret in the chat; the Value is shown only once in Entra, so a new one is created rather than recovered.
- Retry once: no.

## E2 — Client secret expired
- Signature: `AADSTS7000222: The provided client secret keys … are expired`.
- Side: application (Entra).
- Cause: the secret passed its expiry date (the session values show the expiry date recorded with it: `application_secret.expires_on`).
- Confirm: the `credential list` command of E1 shows `ends` in the past.
- Fix: a new secret through the secret field (setup step 10); call `request_new_secret`. Suggest a reminder before
  the next expiry.
- Retry once: no.

## E3 — Application or tenant not found
- Signature: `AADSTS700016: Application with identifier … was not found in the directory`, or `AADSTS90002: Tenant
  … not found`.
- Side: application (Entra), or the session's tenant domain.
- Cause: the Application ID is from another tenant, the tenant domain `{tenant_domain}` is mistyped or a custom domain
  that isn't verified, or the app was created moments ago.
- Confirm: setup step 1 (`az account show`) in the tenant of `{tenant_domain}`, then
  `az ad app show --id {client_id} --query appId -o tsv`.
- Fix: use the initial `<name>.onmicrosoft.com` domain (the IAM engineer changes the session value), or the right
  Application ID. Right after creation, wait 2–5 minutes.
- Retry once: yes, after 2–5 minutes, when the app was just created.

## E4 — Conditional Access blocks the workload identity
- Signature: `AADSTS53003`, `AADSTS50158`, "blocked by Conditional Access".
- Side: application (Entra).
- Cause: a Conditional Access policy for workload identities blocks SailPoint's sign-in.
- Confirm: Entra admin center → Sign-in logs → Service principal sign-ins, filtered on `{client_id}`.
- Fix: exclude the SailPoint service principal from the policy, or allow SailPoint's egress IPs for the region.
- Retry once: no.

## E5 — Insufficient privileges on one object type
- Signature: `403 Authorization_RequestDenied` / "Insufficient privileges to complete the operation", often naming one
  object type during entitlement aggregation (e.g. `Object Type applicationRole`), while accounts work.
- Side: application (Entra).
- Cause: the Graph application permission for that object type was never **granted** (requested but not consented,
  or not added). `applicationRole` needs `Application.Read.All`. An admin **role** on the app does not replace a
  Graph application permission.
- Confirm: setup step 9 and compare with the expected list.
- Fix: setup step 4 (or 5–7) for the missing permission, then step 8; wait a few minutes; aggregate again.
- Retry once: no (retry after the fix).

## E6 — Consent not yet propagated
- Signature: 401 or 403 on the first connection check within about 5 minutes of granting consent.
- Side: application (Entra), transient.
- Cause: Graph takes a few minutes to honour a new consent.
- Confirm: setup step 9 already lists every permission.
- Fix: wait 2–5 minutes.
- Retry once: yes, once, before diagnosing further.

## E7 — Test Connection "Provided source configuration already exists"
- Signature: `[cam-router]: Test connection failed: Provided source configuration already exists` (sometimes with
  `[Microsoft-Entra]: 401 Unauthorized`).
- Side: sailpoint.
- Cause: a known connector issue; the connection check reading accounts proves the credentials are fine.
- Confirm: the connection check passed.
- Fix: for the IAM engineer in ISC: source → CIEM Settings → enable CIEM, save, disable, save; then Test Connection
  again.
- Retry once: no.

## E8 — Connection check reads 0 accounts in delta mode
- Signature: the peek returns no accounts (groups still read) and aggregation adds nothing new.
- Side: sailpoint.
- Cause: Delta Aggregation returns only changes since the last run.
- Confirm: the source has `deltaAggregationEnabled: true`.
- Fix: none needed: the checks switch delta off for the call and back on (`full_read`). Not a failure.
- Retry once: yes (the tools already do it).

## E9 — Agent 365 needs a refresh token
- Signature: dataset aggregation error `Microsoft Agent 365 aggregation requires a refresh token when Connection
  Settings uses Client Credentials`.
- Side: sailpoint.
- Cause: `enableMicrosoftAgent365` was switched on (not by this flow); Agent 365's catalog API is delegated-only.
- Confirm: the source shows Enable Microsoft Agent 365 on.
- Fix: offer to switch it off (`configure_source` sends only the off toggles of `switch_off`). Agent 365 is out of
  scope for this flow.
- Retry once: no.

## E10 — Copilot Studio agents not set up
- Signature: the `microsoft:copilot` dataset fails (no Power Platform application user, or BotReader role missing).
- Side: application (Power Platform).
- Cause: `enableCopilotAIAgent` was switched on without the Power Platform application user.
- Confirm: the source shows Enable Microsoft Copilot Studio Agents on.
- Fix: offer to switch it off (as E9). Copilot Studio is out of scope for this flow.
- Retry once: no.

## E12 — The onboarding service could not read its vault
- Signature: a tool error starting `vault read failed:` (the source exists, configure stops before calling SailPoint).
- Side: the onboarding service itself, not Entra and not the administrator.
- Cause: the service's own AgentCore Identity access (deployment or permissions).
- Confirm: nothing for the participants to run.
- Fix: tell the IAM engineer the service needs an operator. **Do not** ask for a new secret: the administrator's secret
  was never tried. Once fixed, configure again; the stored secret is still waiting in the vault.
- Retry once: no.

## E11 — Dataset aggregation not available to automation
- Signature: `aggregate-agents` returns 404 "The aggregate-agents endpoint is unavailable".
- Side: sailpoint (tenant limitation, not a setup error).
- Cause: this ISC tenant doesn't expose dataset aggregation through its API, although it works in the interface.
- Confirm: the tool result says `tenant_limitation`.
- Fix: the IAM engineer starts it in ISC: Admin → Connections → Sources → {source_name} → Machine Identity
  Datasets → Azure AI Foundry → Aggregate. Then the agent checks the AI-agent count and turns on the
  schedule. Never try another API version. The onboarding has not failed (FR-135).
- Retry once: no.
