# Microsoft Entra ID — things the agent must never take over (FR-014, FR-015; spec 002 FR-105, FR-130)

Ported from `.claude/skills/sailpoint-isc-entra-connector` (commits 9b53746, b71c53c): `entra-setup.sh` refuses an app
it didn't create (tag `sailpoint-isc-entra-connector`) unless told to reuse it, and then only *adds* permissions;
`isc-source.sh discover` lists existing sources on the connector before a new one is created.

- **App registration name already taken** (setup step 2 lists an app):
  - Tagged `sailpoint-isc-entra-connector` and created for this ISC tenant: it may be reused. Skip step 3, use its
    Application ID, and add only the permissions step 9 shows missing; never remove any.
  - Anything else: it belongs to someone else. Ask the administrator to choose another name, or to confirm it **is**
    the SailPoint app for this ISC tenant. If they confirm, add only missing permissions; never remove any, never
    rotate or delete its existing secrets.
- **Naming rule**: one app registration per ISC tenant, `SailPoint ISC - {tenant_name}`. Two ISC tenants never share an
  app: each needs its own secret.
- **An Entra source for this tenant already exists** in SailPoint: at session start call `find_connector_sources`. If
  it finds a source whose domain is `{tenant_domain}`, name it and its owner to the IAM engineer and offer to extend
  it instead of creating a second one. Creating a second source needs the IAM engineer's explicit confirmation.
- **Extend-source** (session value `source_mode: extend`, or the IAM engineer accepts the offer): call
  `find_connector_sources`, then `adopt_source` for the match (ask which one if there are several). `adopt_source`
  refuses a source owned by someone else, on another connector or for another tenant. Then use `read_source_setup`
  and skip, with `update_plan`, the capability steps already on the source. Configure sends only the added
  capabilities; run the connection check, Test Connection and the new capabilities' checks, and entitlement and
  account aggregation again when the account model changed (service principals). Never delete an adopted source.
  No new secret is needed unless a check fails on sign-in (E1/E2).
- **Provisioning**: only when the IAM engineer chose the provisioning capability and accepted its warning. Never add
  Directory.ReadWrite.All or Privileged Authentication Administrator. Lifecycle-state (leaver) actions live on
  identity profiles that other sources share: show the IAM engineer the prepared actions for review and never apply
  them.
