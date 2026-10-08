# Contract: connector type = catalog entry + playbook

Shipped with each release under `onboarding/catalog/` and read-only at run time (FR-028). Adding a connector type
means adding a catalog entry and one playbook folder. No change to the API, the web app or the agent's tool code
(FR-030, SC-009).

## `catalog.yaml` entry

```yaml
- id: aws-saas
  name: AWS SaaS
  status: available            # available | planned
  description: AWS IAM users, groups and policies across an AWS Organization, plus optional Bedrock / AgentCore agent discovery.
  owner_label: AWS owner       # how the application owner is named for this type
  owner_asks: Create a cross-account IAM role with SailPoint's trust, the tenant External ID and read-only policies.
  agent_configures: AWS SaaS source — role, management account, accounts in scope, region, change-password policy, discovery regions.
  first_step_label: AWS role ready   # FR-008 specialised label for "application ready"
  session_fields:
    - { name: source_name, label: Source name, type: string, required: true }
    - { name: source_owner, label: Source owner, type: string, required: true }
    - { name: management_account_id, label: AWS management account, type: aws_account_id, required: true }
    - { name: accounts, label: Accounts in scope, type: aws_account_id_list, required: true }
    - { name: region, label: Region, type: region, required: true, default: ap-southeast-1 }
    - { name: role_name, label: Role name, type: string, required: false, default: "SailPointISCRole-{tenant}" }
    - { name: bedrock_regions, label: Bedrock agent discovery regions, type: region_list, required: false }
    - { name: agentcore_regions, label: AgentCore agent discovery regions, type: region_list, required: false }
  playbook: playbooks/aws-saas
- { id: entra-id, name: Microsoft Entra ID, status: planned, … }
# … active-directory, gcp, azure, okta, servicenow, salesforce, workday, web-services (planned)
```

## `playbooks/<id>/` files

| File | Content | Used by |
|---|---|---|
| `setup.md` | Ordered application-side steps. Each has `title`, `read_only: true/false`, command template(s) with `{placeholders}` from session details and tenant values, and the expected output. | agent prompt (FR-011, FR-013, SC-002) |
| `settings.yaml` | `connector_script` / spec id; `creation_only` fields (sent only on create, e.g. AWS SaaS `spConnectorSpecId`, `idnProxyType`, `spConnectorSupportsCustomSchemas`); `field_map` from session details to the connector form keys; `defaults` (e.g. change-password policy ARN). | `create_source`, `configure_source` |
| `checks.yaml` | Which ISC checks map to which steps (`peek_accounts` → `connection_check`, aggregation kinds → `aggregation`, test → `test_connection`), the plan step id each milestone's `set_step` marks, and the success criteria. | agent tools, `set_step`, API milestone derivation |
| `failures.md` | Known failures: signature (text and what it looks like on screen), side (`sailpoint` / `application`), cause, read-only confirm step, fix, and whether to retry once. | agent prompt (FR-021–FR-024, SC-005) |
| `suggestions.yaml` | Default suggested messages per role and session state (`no_source`, `waiting_for_owner_output`, `check_failed`, `all_passed`, `any`), each `{text, kind}`; at least 3 per role for `any`. The application owner's list never contains `kind: order`. | API, topping up the agent's suggestions (FR-006e, research R17) |
| `plan.yaml` | The starting plan: ordered steps `{id, title, actor, kind, milestone, setup_step?}`, covering the application-side setup (keyed to `setup.md`), the IAM engineer's order, source creation and configuration, and the checks. Seeded into each new session (FR-008b, research R22). | API at session creation; agent `update_plan` |
| `collisions.md` | How to detect application-side items not created for this onboarding (AWS: role or stack name, trust naming another External ID) and the tenant-specific naming rule. | agent prompt (FR-014, FR-015) |

## AWS SaaS playbook sources (port, don't rewrite)

| Playbook file | Ported from |
|---|---|
| `setup.md` | `.claude/skills/sailpoint-isc-aws-connector/scripts/aws-setup.sh` + `references/aws-permissions.md` (CloudFormation / StackSet commands, trust with the `ciem_universal` principals 874540850173 and 706944607044, External ID, read-only and discovery policies) |
| `settings.yaml` | `scripts/isc-source.sh` (`create`, `configure`) + `references/isc-api.md` |
| `plan.yaml` | the order of `aws-setup.sh` steps plus `isc-source.sh` create / configure / peek / aggregate / test |
| `failures.md` | `references/troubleshooting.md` (trust / External ID, demo principal, `no schema provided`, `req.input is null`, AgentCore discovery permissions, change-password policy) |
