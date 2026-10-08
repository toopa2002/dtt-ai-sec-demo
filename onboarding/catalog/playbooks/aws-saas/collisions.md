# AWS SaaS — things the agent must never take over (FR-014, FR-015)

Ported from the skill's preflight in `scripts/aws-setup.sh` and SKILL.md "Several tenants, one AWS account".

- **Role name already exists** (step 2 shows a role):
  - Its trust has `sts:ExternalId` = this tenant's External ID and the tag `ManagedBy=isc-onboarding-agent`: it was
    created for this tenant, so it may be reused. Check its policies with step 8.
  - Anything else (another External ID, no condition, another principal, no tag): it belongs to someone else,
    often another SailPoint tenant or CIEM. **Never update its trust or policies.** Propose the tenant-specific name
    `SailPointISCRole-{tenant_name}` (or another unused name) and ask the IAM engineer to change the session's role
    name.
- **Several SailPoint tenants share one AWS account**: each tenant needs its own role. One role trusts one External
  ID, so reusing a role would cut off the tenant it was made for.
- **CloudFormation stack `{stack_name}` already exists** in the account (an earlier skill or template run): leave it
  alone. The manual steps here don't use stacks, so there's no need to touch it.
- **Provisioning**: the steps only grant read-only access. If the IAM engineer explicitly asks for provisioning, say
  that enabling provisioning on the SailPoint source cannot be undone, and that it needs SailPoint's provisioning
  policy (not part of v1).
