# AWS SaaS — setup steps for the AWS owner

Ported from `.claude/skills/sailpoint-isc-aws-connector` (`scripts/aws-setup.sh` preflight + the "Manual setup (no
CloudFormation)" path in `references/aws-permissions.md`). The agent gives these steps one at a time, with every
value in braces filled in, and checks the pasted output against **Expect** before moving on. Policies come from
`policies/*.json` (`{policy_*}` placeholders).

Run every command in the **management account** `{management_account_id}` unless a step says otherwise. Use
`--profile <name>` if the AWS owner's CLI needs one. The agent never runs these and never needs AWS credentials.

## Step 1 — Check the organization (read-only)

```bash
aws organizations describe-organization --query 'Organization.[MasterAccountId,FeatureSet]' --output text
aws sts get-caller-identity --query Account --output text
```

Expect: `{management_account_id}  ALL`, and the caller account `{management_account_id}`.
If the feature set isn't `ALL` or the account isn't the management account, stop: the AWS SaaS connector needs
Organizations with all features, run from the management account.

## Step 2 — Check the role name is free (read-only)

```bash
aws iam get-role --role-name {role_name} --query 'Role.[Arn,AssumeRolePolicyDocument]' --output json
```

Expect: `NoSuchEntity`. If the role exists, compare its trust with the session's External ID `{external_id}`. If it
trusts another External ID, or wasn't created for this onboarding, **do not change it**. See collisions.md.

## Step 3 — Create the role with SailPoint's trust (change)

```bash
aws iam create-role --role-name {role_name} \
  --description "SailPoint ISC AWS SaaS connector for tenant {tenant_name}" \
  --tags Key=ManagedBy,Value=isc-onboarding-agent Key=SailPointTenant,Value={tenant_name} \
  --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"AWS":["{sailpoint_principal_prod}","{sailpoint_principal_demo}"]},"Action":"sts:AssumeRole","Condition":{"StringEquals":{"sts:ExternalId":"{external_id}"}}}]}'
```

Expect: JSON with `"RoleName": "{role_name}"`. The trust names both SailPoint principals (production and demo
tenants assume from different accounts); the External ID condition limits the role to this tenant.

## Step 4 — Read-only aggregation policy (change)

```bash
aws iam put-role-policy --role-name {role_name} --policy-name SPAggregationPolicy \
  --policy-document '{policy_aggregation}'
```

Expect: no output (success).

## Step 5 — Organization read policy, management account only (change)

```bash
aws iam put-role-policy --role-name {role_name} --policy-name SPOrganizationPolicy \
  --policy-document '{policy_organization}'
```

Expect: no output (success).

## Step 6 — AI-agent discovery policies, only if chosen (change)

Only when Bedrock regions (`{bedrock_regions}`) or AgentCore regions (`{agentcore_regions}`) are set for the session.

```bash
aws iam put-role-policy --role-name {role_name} --policy-name SPBedrockAgentDiscoveryPolicy \
  --policy-document '{policy_bedrock}'
aws iam put-role-policy --role-name {role_name} --policy-name SPBedrockAgentCoreDiscoveryPolicy \
  --policy-document '{policy_agentcore}'
```

Expect: no output for each (success). Give only the command(s) for the services chosen.

## Step 7 — Member accounts (change, once per member account)

Member accounts in scope: `{member_accounts}`. The role must have the **same name and the same trust** in each.
Repeat steps 3, 4 and 6 (not 5) in each member account, for example with a profile that targets it:

```bash
aws sts get-caller-identity --profile <member-profile> --query Account --output text
```

Expect: the member account id, then the same outputs as steps 3, 4 and 6. Skip this step when the only account in
scope is the management account.

## Step 8 — Confirm the role (read-only)

```bash
aws iam get-role --role-name {role_name} --query 'Role.AssumeRolePolicyDocument' --output json
aws iam list-role-policies --role-name {role_name} --output text
```

Expect: the trust shows both SailPoint principals and `sts:ExternalId` = `{external_id}`; the policy list shows
`SPAggregationPolicy`, `SPOrganizationPolicy` and any discovery policies chosen. When this matches, tell the IAM
engineer the AWS side is ready. The agent marks "AWS role ready" itself only when SailPoint's connection check
reads accounts through the role.

**Skip this step** when the IAM engineer has already ordered the checks (a standing check order) and the AWS owner
says the role is ready or a fix is done: rerun the connection check instead, because it proves the same thing
through SailPoint. Give this step only if that rerun fails, to find out why.

## Step 9 — After the first aggregation: confirm SailPoint used the role (read-only)

```bash
aws cloudtrail lookup-events --region us-east-1 \
  --lookup-attributes AttributeKey=EventName,AttributeValue=AssumeRole --max-results 20 \
  --query "Events[?contains(CloudTrailEvent, '{role_name}')].[EventTime,Username]" --output text
```

Expect: recent `AssumeRole` events for `{role_name}`.
