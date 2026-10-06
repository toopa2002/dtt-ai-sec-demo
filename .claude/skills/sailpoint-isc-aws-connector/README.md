# sailpoint-isc-aws-connector

Connects an AWS account (or a whole AWS Organization) to **SailPoint Identity Security Cloud (ISC)** using the
**AWS SaaS** connector. It covers both sides:

- **AWS:** the IAM role SailPoint assumes.
- **ISC:** the source that uses that role.

You can drive it by asking Claude Code, or by running the two scripts yourself.

It has been run end to end against two ISC demo tenants and one AWS Organization. The gotchas listed below are ones
we hit, not guesses.

---

## How it works

```
 ISC tenant                                              Your AWS management account
 ──────────                                              ───────────────────────────
 AWS SaaS source ──(SailPoint's ciem_universal role)──►  sts:AssumeRole  ──►  SailPointISCRole-<tenant>
   roleName, managementAccountId,                         condition: sts:ExternalId = <tenant's External ID>
   region, cloudScope (accounts),                                         │
   Bedrock / AgentCore regions                                            ▼
                                                          read IAM users, groups, roles, policies,
                                                          the Organization (OUs, SCPs, accounts),
                                                          and optionally Bedrock / AgentCore agents
```

- **External ID.** Each ISC tenant has one External ID, shown by `isc-source.sh external-id`. The AWS role trusts
  SailPoint's role **only** when that External ID is presented, so one role belongs to exactly one tenant.
- **SailPoint's AWS principal.** The documented one is `874540850173:role/ciem_universal`. Demo tenants
  (`*.identitynow-demo.com`) were seen assuming from `706944607044:role/ciem_universal`. The scripts can trust both,
  and the External ID still restricts access to your tenant.
- **Where the role lives:**
  - in the **management account**, from a CloudFormation stack;
  - in **member accounts**, from a service-managed StackSet, with the same role name in every account.
- **The source** is created through the ISC REST API and set up like the UI wizard does it. That includes the
  hidden settings without which the connector doesn't work (see [Gotchas](#gotchas)).
- **Permissions are read-only by default.** IAM write access (provisioning) and AI-agent discovery are opt-in flags.

### What's in the folder

| Path | Purpose |
|---|---|
| `SKILL.md` | Instructions Claude follows when the skill is used |
| `scripts/aws-setup.sh` | **Command 1, AWS side.** Builds the CloudFormation template from `assets/` and deploys the stack (plus a StackSet for member accounts) |
| `scripts/isc-source.sh` | **Command 2, ISC side.** Subcommands `external-id`, `discover`, `create`, `configure`, `peek`, `aggregate`, `test`, `show` |
| `scripts/lib.sh` | Shared helpers: dry-run handling, logging, hiding secrets, tenant host handling |
| `assets/*.json` | IAM policies: aggregation, organization, provisioning, Bedrock and AgentCore discovery. SailPoint's published copies have JSON errors and gaps; these are corrected |
| `references/` | Notes for when something breaks: `aws-permissions.md`, `isc-api.md`, `troubleshooting.md` |

**Both scripts are dry-run by default.** Read-only lookups always run; anything that would change something is
printed instead. Add `--apply` to make changes.

---

## Prerequisites

- `bash`, `jq`, `curl`, and AWS CLI v2.
- **AWS credentials for the Organizations management account.** The AWS SaaS connector requires AWS Organizations
  with all features enabled, even for a single account.
- **An ISC personal access token (PAT)** for a user with ORG_ADMIN or SOURCE_ADMIN. In the ISC UI:
  your name → Preferences → Personal Access Tokens.
- **For agent discovery:** an ISC tenant licensed for SailPoint Agentic Fabric. "Agentic Fabric" appears in the
  menu bar when it is.

Put the ISC credentials in a private env file, not in chat or shell history:

```bash
umask 077; cat > ~/.config/sailpoint-isc.env <<'EOF'
ISC_TENANT=acme.api.identitynow.com            # or acme-demo.identitynow-demo.com; the UI host is accepted
ISC_CLIENT_ID=<32 hex chars>
ISC_CLIENT_SECRET=<64 hex chars>
EOF
set -a; source ~/.config/sailpoint-isc.env; set +a
```

`ISC_TENANT` must be the full host name. If the token request fails and the ID is 64 characters and the secret 32,
the script tells you they're swapped.

---

## Using it through Claude Code

Open Claude Code in this repo. The skill is picked up from `.claude/skills/`. Then either type
`/sailpoint-isc-aws-connector` or just ask, for example:

> connect our AWS org to SailPoint tenant acme-demo, read-only, and discover AgentCore agents in ap-southeast-1

Claude then:

1. **Asks for what's missing:** tenant, accounts to cover, role name, read-only or provisioning, agent regions.
2. **Checks what already exists, without changing anything:**
   - other AWS sources in the tenant, and who owns them;
   - roles in the AWS account that already trust SailPoint or this tenant's External ID.
3. **Shows dry-runs of both sides** and waits for your OK.
4. **Applies the changes:**
   1. AWS stack
   2. create the source
   3. configure it
   4. peek (read a few accounts)
   5. aggregate
   6. test connection
5. **Reports back:** source ID, External ID, role, accounts covered, test result and aggregation task IDs.

---

## Using the scripts directly

Run these from the repo root:

```bash
S=.claude/skills/sailpoint-isc-aws-connector/scripts
set -a; source ~/.config/sailpoint-isc.env; set +a
```

### 1. ISC: External ID and connector fields (read-only)

```bash
$S/isc-source.sh external-id     # the tenant's External ID
$S/isc-source.sh discover        # finds the "AWS SaaS" connector and maps its setting names
```

`discover` caches the setting names in `~/.cache/sailpoint-isc-aws/<tenant>-keymap.json` and prints how they were
mapped. A `null` in that output means SailPoint changed the form.

### 2. AWS: role and policies

```bash
EXT=$($S/isc-source.sh external-id)
P=arn:aws:iam::874540850173:role/ciem_universal,arn:aws:iam::706944607044:role/ciem_universal

$S/aws-setup.sh --external-id "$EXT" --role-name SailPointISCRole-acme --stack-name SailPoint-ISC-AWS-acme \
  --targets org --principals "$P" --bedrock --agentcore            # dry-run: review the plan
$S/aws-setup.sh ...same flags... --apply                           # deploy
```

| Option | Meaning |
|---|---|
| `--targets` | Which member accounts get the role: `org` (all, including future ones), `ou-…,ou-…`, `111122223333,…`, or `none` (management account only) |
| `--role-name`, `--stack-name` | Use one pair per ISC tenant (see below). Default: `SailPointAWSRole` / `SailPoint-ISC-AWS` |
| `--principals` | SailPoint role ARNs to trust. Default: the documented commercial one |
| `--partition aws-us-gov` | GovCloud; uses SailPoint's GovCloud principal |
| `--provisioning` | Adds IAM write access, so ISC can create users and change group and policy membership |
| `--bedrock`, `--agentcore` | Adds read-only discovery of Bedrock Agents and AgentCore agents |
| `--profile`, `--region` | AWS CLI profile (must be the management account) and region for the stack |
| `--print-template` | Prints the CloudFormation template and exits |

Before deploying, the script checks:
- you're in the management account;
- Organizations has all features enabled;
- StackSets trusted access is on (it prints the command to enable it rather than enabling it itself);
- the role name isn't already taken by a role this stack didn't create;
- whether other roles already trust this External ID (it warns).

### 3. ISC: source

```bash
$S/isc-source.sh create --name "AWS - acme" --owner me --apply      # me = the PAT's owner
$S/isc-source.sh configure --source "AWS - acme" --role-name SailPointISCRole-acme \
  --mgmt-account-id 111122223333 --region ap-southeast-1 --accounts 111122223333,444455556666 \
  --bedrock-regions ap-southeast-1,us-east-1 --agentcore-regions ap-southeast-1 --apply
$S/isc-source.sh peek --source "AWS - acme" --apply                 # reads 5 IAM users = connection works
$S/isc-source.sh aggregate --source "AWS - acme" --entitlements --apply
$S/isc-source.sh test --source "AWS - acme" --apply
```

- **`create`** won't reuse a source with that name that belongs to someone else. It sends the connector-instance
  fields and the UI defaults, which can only be set correctly at creation.
- **`configure`** takes the role **name**, not its ARN.
  - `--accounts` sets the "AWS Accounts" selection. There is no "manage all accounts" option on this connector.
  - It also sets the External ID, and the required Change Password Policy ARN (AWS's managed
    `IAMUserChangePassword` by default).
- **`aggregate`** waits for the tasks to finish and prints the number of accounts.
- **Agent data isn't pulled by `aggregate`.** It runs every 12 hours (00:00 and 12:00 UTC), or start it from the
  source's Datasets page in the UI.

### Several ISC tenants, one AWS account

A role is tied to one tenant's External ID. Give each tenant its own `--role-name` and `--stack-name`.

Re-running a stack with another tenant's External ID replaces the trusted ID, which cuts off the first tenant.

---

## Gotchas

Each of these was found on a live tenant.

| Symptom | What it means / fix |
|---|---|
| Test Connection: `NullPointerException … "req.input" is null` | Source is missing the UI defaults, or hasn't been aggregated yet. Run `peek`; if it works, aggregate, then test |
| `no schema provided for account list` | Source lacks `spConnectorSupportsCustomSchemas: true`. `create` sets it. After adding it to an older source, the next aggregation can still fail once; retry |
| Test fails with `ConnectorProxy.getInternalConnector()` null | Source was created without its connector instance. Delete it and recreate it with `create` |
| `Permission error (bedrock-agentcore)` on the AgentCore dataset | SailPoint's documented policy misses `ListGateways`, `GetResourcePolicy` and `ListTagsForResource`. The asset policy includes them now; redeploy with `--agentcore --apply` |
| `AWS Client creation failed` | An ARN was entered instead of the role name, the role is missing from the management account, or the Management Account ID is wrong |
| Token `invalid_client` | Wrong PAT or tenant host, or the Client ID and Secret are swapped |

To find which AWS call was refused, check CloudTrail:
```bash
aws cloudtrail lookup-events --region <region> \
  --lookup-attributes AttributeKey=EventSource,AttributeValue=bedrock-agentcore.amazonaws.com \
  --query 'Events[].CloudTrailEvent' --output json \
| jq -r '.[]|fromjson|select(.errorCode=="AccessDenied")|[.eventTime,.userIdentity.arn,.eventName]|@tsv'
```
For other services, change the `EventSource` value. `AssumeRole` attempts are logged in `us-east-1`.

More detail is in `references/troubleshooting.md`.

---

## Out of scope

- **The virtual-appliance "AWS" connector.** It authenticates with access keys and needs a VA cluster.
- **IAM Identity Center.** That needs SailPoint's separate CIEM AWS connector.
- **Other machine-identity datasets:** Amazon Connect, Amazon Quick, Secrets Manager, IAM credentials. The policies
  are listed in `references/aws-permissions.md`, but the scripts don't deploy them.

## Removing a setup

```bash
aws cloudformation delete-stack --stack-name SailPoint-ISC-AWS-acme --region <region>
# with member accounts: delete the StackSet's instances first, then the StackSet
```
Then delete the source in ISC: Admin → Connections → Sources → the source → Delete.
