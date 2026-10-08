# AWS SaaS — known failures (FR-021–FR-024, SC-005)

Ported from `.claude/skills/sailpoint-isc-aws-connector/references/troubleshooting.md`. For each: how it shows up
(text, or what it looks like on a screenshot), which side it's on, the cause, a read-only step to confirm, the fix,
and whether to retry once.

## F1 — Trust doesn't accept this tenant's External ID
- Signature: `AccessDenied` / `is not authorized to perform: sts:AssumeRole` / "AWS Client creation failed" on the
  connection check; the role exists.
- Side: application (AWS).
- Cause: the role's trust has a different or missing `sts:ExternalId` condition.
- Confirm: setup step 8 (`aws iam get-role … AssumeRolePolicyDocument`) and compare with `{external_id}`.
- Fix: if the role was created for this onboarding, run `aws iam update-assume-role-policy` with the step 3 trust
  document. Otherwise create a new tenant-specific role (collisions.md).
- Retry once: no.

## F2 — Wrong SailPoint principal (production vs demo tenant)
- Signature: same `sts:AssumeRole` denial; the External ID in the trust is right, but the principal is only
  `874540850173` (or only `706944607044`).
- Side: application (AWS).
- Cause: demo/partner tenants (`*.identitynow-demo.com`) assume from `706944607044`, production from
  `874540850173`.
- Confirm: setup step 8.
- Fix: `aws iam update-assume-role-policy --role-name {role_name} --policy-document '<step 3 trust>'`. The step 3
  trust names both principals, and the External ID still restricts access to this tenant.
- Retry once: no.

## F3 — Role missing, ARN given instead of name, or wrong management account
- Signature: "AWS Client creation failed" on the connection check; or `NoSuchEntity` in step 8.
- Side: application (role missing), or SailPoint (role name is an ARN, wrong management account id).
- Confirm: setup step 8 in `{management_account_id}`; check the source's role name is a plain name.
- Fix: create the role (setup steps 3–6), or correct the session's role name / management account and rerun
  `configure_source`.
- Retry once: no.

## F4 — "no schema provided for account list" / "EntitlementList.getSchema() is null"
- Signature: the connection check or aggregation error text.
- Side: SailPoint.
- Cause: the source lacks `spConnectorSupportsCustomSchemas: true` and the other UI defaults. Sources created by
  `create_source` have them. Right after a configuration change, the connector instance can keep its old config for a
  few minutes.
- Fix: rerun the failed step once (stale config). If it fails again on a source not created by this agent, the
  source must be recreated, because creation-only fields can't be added later.
- Retry once: **yes**.

## F5 — Test Connection: `NullPointerException … "req.input" is null`
- Signature: Test Connection fails like this, and the source is "Not Responding", before the first aggregation.
- Side: SailPoint (expected behaviour, not a configuration error).
- Fix: run `peek_accounts`, then `start_aggregation`, then `test_connection` again. It reports SUCCESS after the
  first aggregation.
- Retry once: yes, after aggregation.

## F6 — `NullPointerException … ConnectorProxy.getInternalConnector()` / "Value of 'cluster' cannot be null"
- Side: SailPoint.
- Cause: the source has no connector instance (created without `spConnectorSpecId`).
- Fix: only for a source created in this session, on the IAM engineer's order: `delete_session_source`, then
  `create_source`. Never delete someone else's source.
- Retry once: no.

## F7 — "Permission error (bedrock-agentcore)" or "(bedrock)" on a discovery dataset
- Signature: `Permission error (bedrock-agentcore): n/n accounts, n/n regions`.
- Side: application (AWS).
- Cause: the role lacks an action the connector calls. SailPoint's documented AgentCore policy misses
  `ListGateways`, `GetResourcePolicy`, `ListTagsForResource` and the gateway reads; the playbook policy includes them.
- Confirm:
  `aws cloudtrail lookup-events --region <region> --lookup-attributes AttributeKey=EventSource,AttributeValue=bedrock-agentcore.amazonaws.com`
  and look for `AccessDenied` for `{role_name}`.
- Fix: re-run setup step 6 for the service, which carries the complete policy.
- Retry once: no.

## F8 — Change Password Policy ARN required
- Signature: the SailPoint UI refuses to save the source, "Change Password Policy ARN is required".
- Side: SailPoint.
- Fix: `configure_source` sets `changePasswordPolicyARN` to `arn:aws:iam::aws:policy/IAMUserChangePassword`;
  rerun it.
- Retry once: no.

## F9 — Token `invalid_client` / "Full authentication is required"
- Side: SailPoint (tenant credential).
- Fix: the agent can't fix it. Tell the IAM engineer an admin must replace the tenant's credential (Admin →
  Tenants). Make no further SailPoint changes.
- Retry once: no.

## F10 — `AWSOrganizationsNotInUseException` / Organizations `AccessDenied`
- Side: application (AWS).
- Cause: the account isn't in an Organization, or the management-account role lacks `SPOrganizationPolicy`, or the
  management account id points at a member account.
- Confirm: setup step 1.
- Fix: enable Organizations with all features, or add `SPOrganizationPolicy` (setup step 5), or correct the
  management account id and rerun `configure_source`.
