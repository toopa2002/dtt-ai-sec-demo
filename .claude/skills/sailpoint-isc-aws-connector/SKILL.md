---
name: sailpoint-isc-aws-connector
description: Onboard AWS into SailPoint Identity Security Cloud (ISC / IdentityNow) with the "AWS SaaS" connector — creates the cross-account IAM role, trust policy (SailPoint ciem_universal + External ID) and SailPoint IAM/Organizations policies across an AWS Organization via CloudFormation/StackSets, then creates, configures, tests and aggregates the AWS source through the ISC REST API. Use this whenever someone wants to connect AWS to SailPoint, set up or fix an AWS source in ISC/IdentityNow, create the IAM role or policies SailPoint needs, wire up the External ID, aggregate AWS IAM users/groups/policies into SailPoint, enable discovery of Amazon Bedrock agents or Bedrock AgentCore agent runtimes as machine identities (Machine Identity Governance / Agentic Fabric), or troubleshoot "AWS Client creation failed" / AssumeRole errors on a SailPoint AWS source — even if they don't say "connector" or "SaaS".
---

# SailPoint ISC — AWS SaaS connector setup

Two bundled commands do the work; you collect inputs, run them in dry-run, show the plan, and only then apply.

| Command | Side | What it does |
|---|---|---|
| `scripts/aws-setup.sh` | AWS | IAM role (same name + External ID in every account) with SailPoint's policies: CloudFormation stack in the management account, service-managed StackSet to member accounts |
| `scripts/isc-source.sh` | ISC | `discover` → `create` → `configure` → `test` → `aggregate` via the REST API |

Both default to **dry-run** (read-only lookups run, writes are printed). Add `--apply` only after the user has seen
the dry-run output and agreed — these create IAM roles across an organization and a source in their tenant.

Scope: this skill targets the **SaaS** connector only (no virtual appliance; IAM role + External ID auth, no access
keys). If the user needs the VA-based connector or IAM Identity Center (separate CIEM connector), say so rather than
forcing this flow.

## Inputs to collect

- **AWS:** a CLI profile for the Organizations **management account** (the SaaS connector requires Organizations with
  all features, even for a single account); which member accounts to cover (`org`, OU ids, account ids, or `none`);
  role name (`aws-setup.sh` defaults to `SailPointAWSRole`; prefer a name not already in use); commercial or GovCloud; read-only or provisioning;
  whether to discover AI agents in Amazon Bedrock and/or Bedrock AgentCore, and in which regions.
- **ISC:** tenant as its full API host — `ISC_TENANT=acme.api.identitynow.com`, or for a demo/partner tenant
  `ISC_TENANT=acme-demo.api.identitynow-demo.com` (the UI host such as `acme.identitynow-demo.com` is accepted and
  converted). `ISC_BASE_URL` overrides it entirely (custom domains, testing). Plus a personal access token
  (`ISC_CLIENT_ID`/`ISC_CLIENT_SECRET`, needs ORG_ADMIN or SOURCE_ADMIN), source name, owner (alias, email or id).

Ask the user to put the PAT in a `chmod 600` env file (e.g. `~/.config/sailpoint-isc.env`) rather than pasting the
secret into chat. A PAT's Client ID is 32 hex chars and its Secret 64 — the token step flags them if swapped.

## Workflow

The External ID is **tenant-wide** (`GET /beta/tenant`), so the AWS side can be built before the source exists.

```bash
S=.claude/skills/sailpoint-isc-aws-connector/scripts   # from the repo root

# 1. ISC (read-only): External ID + connector field names
$S/isc-source.sh external-id
$S/isc-source.sh discover

# 2. AWS: role + policies, trusting SailPoint with that External ID (dry-run, show, then --apply)
$S/aws-setup.sh --external-id <EXTERNAL_ID> --role-name SailPointISCRole --profile mgmt --targets org
$S/aws-setup.sh --external-id <EXTERNAL_ID> --role-name SailPointISCRole --profile mgmt --targets org --apply
#    add --bedrock and/or --agentcore to grant AI-agent discovery permissions

# 3. ISC: create + configure the source, test, aggregate
$S/isc-source.sh create --name "AWS - <owner>" --owner me --apply      # me = the PAT's owner
$S/isc-source.sh configure --source "AWS - <owner>" --role-name SailPointISCRole \
  --mgmt-account-id 111122223333 --region ap-southeast-1 --accounts 111122223333,444455556666 \
  --bedrock-regions us-east-1 --agentcore-regions ap-southeast-1 --apply      # optional agent discovery
$S/isc-source.sh peek --source "AWS - <owner>" --apply          # reads a few IAM users = connection works
$S/isc-source.sh aggregate --source "AWS - <owner>" --entitlements --apply
$S/isc-source.sh test --source "AWS - <owner>" --apply          # only meaningful after the first aggregation

# 4. Optional: scheduled aggregation (Quartz cron; seconds first). No options = show schedules; --clear removes.
$S/isc-source.sh schedule --source "AWS - <owner>" --cron "0 */5 * * * ?" --types account,group --apply
```

Notes per step:

1. **Before anything else, look at what already exists** — on shared/demo tenants and AWS accounts this matters:
   list existing AWS SaaS sources (`GET /v3/sources`, `connector == "awssaas"`) with their owners, and IAM roles in
   the management account that trust a `ciem_universal` principal. Another tenant or CIEM may already use a role
   called `SailPointAWSRole`; reusing that name would break them. `aws-setup.sh` refuses to touch a role it didn't
   create, and `create` refuses to reuse a same-named source owned by someone else — pick new names instead.
2. **discover** reads the connector's form from the tenant and writes a key map
   (`~/.cache/sailpoint-isc-aws/<tenant>-keymap.json`, including `connectorType`, the SaaS spec id). On current
   tenants the connector is "AWS SaaS" (`awssaas`) with keys `roleName`, `region`, `externalId`,
   `managementAccountId`, `assumeRoleSessionName`, `cloudScope` (AWS Accounts). Show the user the mapping; a `null`
   means SailPoint changed the form — fix it from `.allFields`.
3. **aws-setup.sh** runs read-only preflight (management account, all-features Organizations, StackSets trusted
   access, role-name collision). If trusted access is off it prints the enable command — ask before running it, or
   use `--targets none` for the management account only. SailPoint's documented principal is
   `874540850173:role/ciem_universal`; demo/partner tenants (`*.identitynow-demo.com`) assume from
   `706944607044:role/ciem_universal` (confirmed in CloudTrail). When unsure, pass both with `--principals a,b` — the External ID condition still restricts access to this tenant.
4. **create** sends the SaaS connector spec (`spConnectorSpecId`, `idnProxyType: sp-connect`) so ISC provisions the
   connector instance. These can only be set at creation (PATCH strips them); a source created without them can't be
   fixed — delete it and recreate.
5. **configure** takes the role **name**, never the ARN. There is no "manage all accounts" switch on this connector:
   `--accounts` sets the AWS Accounts list (`cloudScope`). It also writes the tenant External ID onto the source.
   It also sets the form-required Change Password Policy ARN (default: AWS managed
   `arn:aws:iam::aws:policy/IAMUserChangePassword`; override with `--change-password-policy-arn`) — without it the
   UI refuses to save the source.
6. **peek, then aggregate, then test.** `peek` reads a few IAM users through the connector — success proves role,
   trust, External ID and settings. Known first-run behaviour of the AWS SaaS connector (seen on two tenants):
   - `no schema provided for account list` (peek/aggregation) or `NullPointerException … "req.input" is null`
     (test) mean the source lacks the UI defaults, above all `spConnectorSupportsCustomSchemas: true`. `create` sets
     them; for an older source, PATCH them in (see `references/isc-api.md`).
   - After changing those settings the connector instance can keep its old config for a few minutes: one
     aggregation may still fail with the schema error and succeed on the next run. Retry before digging deeper.
   - Test Connection may only report `SUCCESS` (and the source turn HEALTHY) after the first successful aggregation.
   If `peek` fails with anything else, check whether SailPoint reached AWS —
   `aws cloudtrail lookup-events --region us-east-1 --lookup-attributes AttributeKey=EventName,AttributeValue=AssumeRole`
   and look for your role ARN — and see `references/troubleshooting.md`.

**Several tenants, one AWS account.** Each tenant has its own External ID, and a role trusts the External IDs in
its trust policy, so give each tenant its own role and stack: `aws-setup.sh --role-name SailPointISCRole-<tenant>
--stack-name SailPoint-ISC-AWS-<tenant>`. Rerunning the default stack with another tenant's ID would cut off the
first tenant. `aws-setup.sh` warns when some other role already trusts the External ID — often a role built from
SailPoint's CloudFormation template for that tenant. Reusing it is possible, but check its permissions first (such
roles have been seen with `iam:*`).

## AI agent discovery (Bedrock, AgentCore)

The AWS SaaS connector can discover Amazon Bedrock Agents and Bedrock AgentCore agent runtimes as machine identities
(SailPoint Machine Identity Governance — the tenant needs SailPoint Agentic Fabric; an "Agentic Fabric" entry in the
ISC menu bar is a good sign it's licensed). Both halves must be switched on, because the source toggle alone just
produces AccessDenied in AWS:

- AWS: `aws-setup.sh --bedrock` adds `SPBedrockAgentDiscoveryPolicy` (bedrock:List*/Get* on agents, aliases,
  versions, action groups, knowledge bases, data sources, collaborators, plus `kms:Decrypt`/`GenerateDataKey` for
  CMK-encrypted agents); `--agentcore` adds `SPBedrockAgentCoreDiscoveryPolicy` (runtimes, endpoints, versions,
  workload identities, OAuth2/API-key credential providers, gateways, resource policies, tags). Both are read-only.
  The AgentCore one goes beyond SailPoint's documented policy: the connector also calls `ListGateways`,
  `GetResourcePolicy` and `ListTagsForResource`, and the dataset fails with "Permission error
  (bedrock-agentcore)" without them. If a dataset reports a permission error, CloudTrail's AccessDenied events for
  the role name the missing action.
- ISC: `configure --bedrock-regions R,R` / `--agentcore-regions R,R` turns on the matching toggle and region list.
  Discovery only looks in the listed regions, so ask where the agents actually run (e.g.
  `aws bedrock-agent list-agents --region R`, `aws bedrock-agentcore-control list-agent-runtimes --region R`).
  `--no-bedrock` / `--no-agentcore` switch discovery off again.

Capabilities tab: SailPoint fills **Tools** from Bedrock Agent action groups. For AgentCore runtimes it shows none,
whatever is declared in AWS (gateways, tags, schemas); see `references/troubleshooting.md`.

Agent datasets (`aws:bedrock`, `aws:bedrockagentcore`) aggregate on their own schedule (every 12 h by default) or
from the source's dataset page in the UI — they are not part of account aggregation.

## Read-only vs provisioning

Default is read-only (`SPAggregationPolicy` everywhere + `SPOrganizationPolicy` in the management account). Pass
`--provisioning` to `aws-setup.sh` only when the user wants ISC to create/modify IAM users, groups and policy
attachments — it adds `SPProvisioningPolicy` (IAM write). Mention that enabling provisioning on the ISC source itself
is irreversible. Details and the reason for each permission: `references/aws-permissions.md`.

## Reference files

- `references/aws-permissions.md` — every policy, trust policy, GovCloud principal, optional add-ons (Activity
  Insights, machine identity discovery), manual (no-CloudFormation) steps.
- `references/isc-api.md` — endpoints, auth, request bodies, key-map format; read when a call fails or the user wants
  to do something the script doesn't cover.
- `references/troubleshooting.md` — documented errors and fixes.

## Reporting back

Finish with: source id and name, External ID used, management account + role name, which accounts are covered,
read-only vs provisioning, test-connection result, and the aggregation task id(s). If you deleted or recreated
anything along the way, say so.
