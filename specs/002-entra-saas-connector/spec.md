# Feature Specification: Microsoft Entra ID SaaS connector for the ISC Onboarding Agent

**Feature Branch**: `002-entra-saas-connector`

**Created**: 2026-10-08

**Status**: Draft

**Input**: User description: "entraID SaaS Connector. create AI Agent and ISC Onboarding app from
/sailpoint-isc-entra-connector. This is a EntraID SaaS connector type."

## Overview

Spec 001 delivered the ISC Onboarding Agent: one shared, live session in which an AI agent tells the **Application
owner** what to do in the application and acts on SailPoint Identity Security Cloud (ISC) for the **IAM engineer**.
Its first available connector type is AWS SaaS; **Microsoft Entra ID** is listed as *planned*.

This feature makes **Microsoft Entra ID** the second *available* connector type, using SailPoint's cloud
"Microsoft Entra" connector (no virtual appliance). Its behaviour comes from the live-verified
`sailpoint-isc-entra-connector` skill, the same way the AWS SaaS type came from the AWS skill. Everything spec 001
defines for all connector types — sessions, roles, threads, plan, suggested replies, audit, masking, catalog — is
reused unchanged. This spec only adds what is specific to Entra ID, and amends spec 001 where Entra needs something
the AWS type never did, chiefly a safe way for the application's **client secret** to reach ISC.

For Entra ID the application owner is the **Entra administrator** (a person who can register applications and grant
admin consent in the organization's Entra tenant). The agent never signs in to Entra and never runs anything there.

The end state of an Entra session is a working Entra source in ISC that has passed its connection check
(reading live accounts), passed Test Connection and completed entitlement and account aggregation — plus, for each
optional capability the IAM engineer chose, that capability's own proof (service principals aggregated as accounts,
AI agents aggregated from Azure AI Foundry, account creation ready for joiners).

## Clarifications

### Session 2026-10-08

- Q: Separate app or part of the existing ISC Onboarding Agent? → A: Part of the existing app, as a second available
  connector type with its own playbook.
- Q: How does the Entra application's client secret reach the ISC source, given the agent must never hold application
  credentials (001 FR-010)? → A: The Entra administrator pastes the secret's **Value** into a dedicated, write-only
  secret field in their own screen. The value goes straight to the product's vault and from there into the ISC
  source. The agent, the chat, logs, the database and both screens never contain it; the agent only learns that a
  secret was received and when it expires.
- Q: Which capabilities are in the first release? → A: All four: read-only directory, service principals (machine
  identities), AI agents from Azure AI Foundry, and provisioning (joiner/mover/leaver).
- Q: Which ISC API? → A: SailPoint's current versioned API (v2026), no beta endpoints, for the Entra type only. The
  AWS SaaS type stays as it is.
- Q: After the secret has been written into the ISC source, how long does the product keep its vault copy? → A: Until
  Test Connection passes for the source; then it is deleted from the vault and ISC holds the only copy. Any later
  rebuild or retry needs a new secret.
- Q: Where does the secret's expiry date come from? → A: The administrator enters it in the secret field, next to the
  value (required; in the future, at most 2 years ahead), copied from Entra's "Expires" column.
- Q: What happens when an aggregation is still running after 30 minutes (large tenant)? → A: The agent reports it as
  still running with elapsed time and posts the result to the session thread when it ends, even if nobody is
  watching; the step is pending, not failed. SC-101 applies to tenants of up to 5,000 accounts.
- Q: How is a capability added to an Entra source onboarded earlier with fewer? → A: A new session that targets the
  existing source ("extend source"); the duplicate-source warning offers extending it instead of creating another.
- Q: Does the provisioning proof include a live write in Entra (e.g. create and delete a test user)? → A: No. The
  proof is the account-creation definition and matching rule in place plus a read-only check that the application
  holds the write permissions and the User Administrator role; the first real write happens outside the session.

## Actors and terms

Spec 001's actors and terms apply. Additional terms:

- **Entra administrator**: the Application owner for this connector type — holds Global Administrator or Privileged
  Role Administrator in the Entra tenant (needed to grant admin consent and assign directory roles).
- **Entra tenant**: the organization's Microsoft Entra directory, identified to SailPoint by its initial
  `<name>.onmicrosoft.com` domain.
- **SailPoint application registration**: the app the Entra administrator registers in the Entra tenant for
  SailPoint; identified by its Application (client) ID, which is not secret.
- **Application secret**: the client secret Value of that registration. Secret; never shown or stored outside the vault.
- **Capability**: an optional part of the Entra onboarding chosen by the IAM engineer — *directory* (always on),
  *service principals*, *AI agents*, *provisioning*. Each capability adds owner steps, source settings and checks.
- **Dataset**: SailPoint's collection of machine identities of one kind from the source (here: Azure AI Foundry
  agents), aggregated separately from accounts.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - IAM engineer onboards an Entra tenant for governance (Priority: P1)

The IAM engineer opens the catalog, sees **Microsoft Entra ID** as available, and starts a session: they enter the
application name, the Entra tenant's initial domain and the capabilities they want (directory only, here), and invite
the Entra administrator. Once the administrator's side is ready, the IAM engineer orders the agent to create the
connector. The agent creates and configures the Entra source in ISC and proves it works, in a fixed order: read a few
live accounts, run Test Connection, aggregate entitlements, then aggregate accounts. It reports each result with
counts (accounts, entitlements) so the IAM engineer can start certifications.

**Why this priority**: This is the core value — Entra users, groups, roles, licences and app roles in ISC — and the
minimum every other capability builds on.

**Independent Test**: With a test Entra tenant and ISC tenant, run a directory-only session end to end; the source
exists, its connection check and Test Connection pass, and its account and entitlement counts are reported and
non-zero.

**Acceptance Scenarios**:

1. **Given** the catalog, **When** the IAM engineer views it, **Then** Microsoft Entra ID is shown as available with
   what the Entra administrator will be asked to do and what the agent will configure.
2. **Given** a new Entra session, **When** the IAM engineer enters a domain that is not an Entra tenant domain or
   tenant ID, **Then** the field is rejected with an example of the expected form.
3. **Given** the administrator's side is ready and the secret has been received, **When** the IAM engineer orders the
   connector, **Then** the agent creates the source, configures it and runs the four proof steps in order, stopping
   at the first failure with the failing step named.
4. **Given** the proof succeeded, **When** the agent reports, **Then** the report includes the source name,
   the Entra tenant domain, the Application (client) ID, the result of each proof step and the account and
   entitlement counts.
5. **Given** another source in the same ISC tenant already covers this Entra tenant, **When** the session starts,
   **Then** the agent tells the IAM engineer which source and owner, and offers to extend that source (FR-105)
   instead of creating another; creating a second source needs explicit confirmation.

---

### User Story 2 - Entra administrator prepares the tenant from the agent's instructions (Priority: P1)

The Entra administrator signs in and sees, in their own thread, the agent's step-by-step instructions for their part:
check their admin role, register the SailPoint application, add exactly the permissions the chosen capabilities need,
grant admin consent, create a client secret. Every value the session knows is filled in; each step is labelled
read-only or a change, and each says what output to expect. The administrator runs the steps themselves, pastes the
output back, and the agent checks it. At the secret step the administrator pastes the secret's Value into the
dedicated secret field (not the chat); the agent confirms receipt and the expiry date, without ever seeing the value.

**Why this priority**: Without the Entra side nothing can be created; it must be quick and unambiguous for an admin
who has never worked with SailPoint.

**Independent Test**: An administrator follows the instructions for a directory-only session on a test tenant;
the agent accepts each pasted output, flags a deliberately wrong one, and records the secret as received; no secret
value appears anywhere outside the vault.

**Acceptance Scenarios**:

1. **Given** the capabilities chosen by the IAM engineer, **When** the administrator opens the session, **Then** they
   see only the steps and permissions those capabilities need, read-only checks first.
2. **Given** a pasted output that shows the permission list without admin consent granted, **When** the agent checks
   it, **Then** it says consent is missing and repeats the consent step.
3. **Given** the secret step, **When** the administrator pastes a value into the chat instead of the secret field,
   **Then** the value is masked before display, storage or reaching the agent, and the administrator is pointed to
   the secret field and told to create a new secret (the pasted one is treated as exposed).
4. **Given** the secret field, **When** the administrator submits a value shaped like an identifier (a GUID) rather
   than a secret Value, **Then** it is rejected with "this is the secret's ID, not its Value".
7. **Given** the secret field, **When** the administrator submits a value without an expiry date, or with a date in
   the past or more than 2 years ahead, **Then** it is rejected and the administrator is pointed to the "Expires"
   column in Entra.
5. **Given** a secret was received, **When** either participant looks at the session, **Then** they see "secret
   received", who provided it, when, and its expiry date — never the value or any part of it.
6. **Given** the administrator's account lacks the admin roles needed for consent, **When** they paste the role-check
   output, **Then** the agent says which role is needed before any change step.

---

### User Story 3 - Service principals aggregated as accounts (Priority: P2)

The IAM engineer adds the *service principals* capability. The administrator gets the extra read permissions it
needs; the agent configures the source to aggregate the tenant's application service principals as accounts with
their role, app role, group, role-assignment, admin-consent and custom-attribute memberships, prepares the source's
account model for them, and runs a full account aggregation. The report distinguishes user accounts from service
principal accounts.

**Why this priority**: Machine identities are a growing governance target, but the directory onboarding is useful
without them.

**Independent Test**: In a test tenant with known application service principals, a session with this capability
reports a service-principal account count equal to the number of matching service principals.

**Acceptance Scenarios**:

1. **Given** the capability is selected, **When** the source is configured, **Then** service principals are in scope
   with the "application" filter and the membership options of the reference configuration, and Azure PIM / Entra PIM
   memberships are off.
2. **Given** a source configured for service principals, **When** the proof runs, **Then** the account aggregation
   reads the full set (not only changes since a previous run) and the report shows users and service principals
   separately.
3. **Given** the source's account model lacks the service-principal attributes, **When** the agent configures the
   capability, **Then** it adds only the missing attributes and never changes existing ones.

---

### User Story 4 - AI agents from Azure AI Foundry (Priority: P2)

The IAM engineer adds the *AI agents* capability and names the Azure subscriptions where Foundry agents live. The
administrator gets the steps to grant the SailPoint application read access on those subscriptions and the extra
consent Foundry discovery needs. The agent switches on Foundry agent discovery, aggregates the Foundry dataset and
turns on its scheduled aggregation, reporting how many AI agents were found. Copilot Studio and Agent 365 agent
discovery stay off, because they need further setup outside this flow.

**Why this priority**: High demo and governance value, but depends on licensing and on the tenant exposing dataset
aggregation to automation.

**Independent Test**: In a tenant with at least one Foundry agent, a session with this capability ends with the
reported AI-agent count matching the agents in the named subscriptions.

**Acceptance Scenarios**:

1. **Given** the capability without subscriptions, **When** the IAM engineer tries to save, **Then** they are asked
   for at least one subscription ID.
2. **Given** the owner steps are done, **When** the agent aggregates the dataset, **Then** it reports the number of
   AI agents found and turns on the dataset's schedule.
3. **Given** the ISC tenant does not allow dataset aggregation to be started by automation, **When** the agent tries,
   **Then** it tells the IAM engineer this is a tenant limitation (not a setup error), gives the exact place in the
   ISC interface to start it, and checks the result afterwards.
4. **Given** Copilot Studio or Agent 365 discovery is switched on in the source by someone else, **When** a dataset
   aggregation fails with their known errors, **Then** the agent explains the missing setup and offers to switch
   them off.

---

### User Story 5 - Provisioning for joiners, movers and leavers (Priority: P3)

The IAM engineer adds the *provisioning* capability. The agent warns that this lets ISC create, change, disable and
delete users and groups in the directory, and asks for the domain new accounts get and their usage location. The
administrator gets the write permissions and the User Administrator role assignment for the SailPoint application.
The agent sets up how ISC creates new accounts and how existing accounts are matched to identities. Joiner/leaver
account actions on identity profiles are prepared for the IAM engineer to review, not applied automatically.

**Why this priority**: Valuable, but higher risk and not needed for governance; most first onboardings are read-only.

**Independent Test**: With the capability, the source has an account-creation definition using the given domain
and an account-matching rule, the administrator's steps include the write permissions and the role, the agent
confirms both from the administrator's read-only output, and nothing is written to the directory during the session.

**Acceptance Scenarios**:

1. **Given** the IAM engineer selects provisioning, **When** they confirm, **Then** they have seen the write-access
   warning and the session records who accepted it.
2. **Given** the source already has an account-creation definition, **When** the agent configures provisioning,
   **Then** it keeps the existing one unless the IAM engineer explicitly asks to replace it.
3. **Given** provisioning is not selected, **When** the administrator's steps are generated, **Then** they contain
   no write permissions and no directory role.
4. **Given** provisioning is configured, **When** the proof runs, **Then** it checks the definition and matching rule
   on the source and, from the administrator's pasted read-only output, that the application holds the write
   permissions and the User Administrator role; it creates, changes or deletes nothing in the directory, and the
   report says the first real joiner is the first live write.

---

### User Story 6 - Troubleshoot Entra failures (Priority: P2)

When a step fails, either participant pastes the error text or a screenshot. The agent recognises the documented
Entra failures, says which side must act and what to do, and gives a read-only way to confirm the cause first.

**Why this priority**: Entra onboarding fails in a handful of well-known ways; recognising them is most of the
time saved.

**Independent Test**: For each documented Entra failure, a pasted sample error or screenshot gets the right cause,
side and fix.

**Acceptance Scenarios**:

1. **Given** "invalid client secret", **When** pasted, **Then** the agent says the secret's ID was probably used
   instead of its Value (or the secret belongs to another app) and asks the administrator for a new secret via the
   secret field.
2. **Given** "client secret keys are expired", **When** pasted, **Then** the agent asks for a new secret and notes the
   expiry it had recorded.
3. **Given** "Insufficient privileges" / authorization denied on one object type during aggregation, **When**
   pasted, **Then** the agent names the missing permission for that object type and the administrator step that adds
   it, noting that an admin role does not replace an application permission.
4. **Given** a failure right after consent was granted, **When** it is consent propagation, **Then** the agent says
   to wait a few minutes and retries once before diagnosing further.

---

### Edge Cases

- The administrator pastes the secret into the chat: masked everywhere, treated as exposed, new secret requested
  (US2-3).
- The secret is replaced later (rotation): the new value replaces the old in ISC; the session shows the new expiry;
  the new value is deleted from the vault once Test Connection passes with it (FR-125).
- The source must be rebuilt, or a step re-run, after the vault copy was deleted: the agent asks the administrator
  for a new secret through the secret field; it never asks for the old one.
- The secret expires during or after the session: the agent warns the IAM engineer 30 days before the recorded
  expiry date when the session is opened, and the failure is recognised (US6-2).
- An app registration with the session's application name already exists but was not created for this onboarding:
  the administrator is asked to choose a different name or confirm it is the SailPoint app; if confirmed, its
  existing permissions are kept and only missing ones added.
- The Entra tenant domain entered is a custom domain: accepted, with a note that SailPoint recommends the initial
  `.onmicrosoft.com` domain.
- Consent was requested but not granted, or granted for some permissions only: the check names exactly which ones.
- An aggregation on a large tenant runs longer than 30 minutes: the step shows as pending with elapsed time, and the
  result is posted to the thread when it ends (FR-139).
- With delta aggregation on, reading accounts returns nothing new: the proof always reads the full set; this is not
  reported as a failure.
- The ISC tenant lacks the Microsoft Entra connector or the machine-identity features: the agent says so at session
  start and disables the affected capabilities.
- The administrator is a different person from the one who started the steps (handover per 001 US8): secret status
  and step progress carry over; no value does.
- Two sessions for the same Entra tenant and ISC tenant: the second is warned at start and offered extend-source
  (US1-5, FR-105).
- An extend-source session targets a source whose application registration was since deleted or replaced in Entra:
  the first proof step fails on sign-in; the agent asks the administrator to confirm the Application ID and, if it
  changed, for a new secret.
- An AWS SaaS session runs at the same time: nothing in it changes.

## Requirements *(mandatory)*

Numbering continues from spec 001 in the FR-1xx range. Where a requirement amends spec 001 it says so.

### Functional Requirements

**Catalog and session**

- **FR-101** (amends 001 FR-029): The catalog MUST list **Microsoft Entra ID** as *available*, alongside AWS SaaS.
  Its entry states what the Entra administrator is asked to do and what the agent configures.
- **FR-102**: An Entra session MUST collect: the application (source) name; the Entra tenant domain or tenant ID;
  the capabilities (directory always; service principals, AI agents, provisioning optional); for AI agents, one or
  more Azure subscription IDs; for provisioning, the domain for new accounts and their usage location. Each value
  MUST be validated for form before the session can start.
- **FR-103**: Choosing provisioning MUST show a warning that ISC will be able to write to the directory and record
  who accepted it.
- **FR-104**: The session plan for Entra MUST be built from the chosen capabilities: only the owner steps, agent
  steps and checks of those capabilities appear.
- **FR-105** (amends 001 FR-018): The IAM engineer MUST be able to start an **extend-source** session that targets an existing Entra
  source and adds capabilities it lacks. The plan then contains only the steps of the added capabilities: the
  administrator adds the missing permissions to the existing SailPoint application registration (identified by its
  Application ID; existing permissions kept), and the agent changes only the settings of the added capabilities on
  the existing source, then runs the proof for them (entitlement and account aggregation again when the account
  model changed). No new secret is requested unless a proof step fails on sign-in. Capabilities are not removed
  this way. Per 001 FR-018 the targeted source MUST NOT be owned by someone else; this amends FR-018 only to allow
  changing (never deleting or recreating) a source created in an earlier session.

**Entra administrator side**

- **FR-110**: The agent MUST give the Entra administrator ordered, copy-ready steps for the chosen capabilities, each
  labelled read-only or change, with every known value filled in and the output to expect. Read-only checks
  (admin role, existing app registration with the same name) come first.
- **FR-111**: The permissions requested MUST be the least each capability needs: read-only directory permissions
  for the directory capability; the service-principal and custom-attribute read permissions for service principals;
  the Foundry consent plus read roles on the named subscriptions for AI agents; write permissions and the User
  Administrator role only for provisioning. No step may request broad directory-wide write access.
- **FR-112**: The agent MUST check each pasted output against what the step expects and say what is wrong when it
  does not match (e.g. consent not granted, wrong tenant, missing permission).
- **FR-113**: The agent MUST NOT run anything in Entra or Azure, MUST NOT ask for or accept the administrator's own
  credentials, and MUST NOT give instructions that print the application secret to a shared screen.

**Application secret (amends 001 FR-010 and FR-026)**

- **FR-120** (amends 001 FR-010): For connector types that require an application secret in the ISC source, the
  product MUST provide a dedicated **secret field**, available only to the Application owner, that accepts the
  secret Value write-only. The value MUST go only to the product's vault and from there into the ISC source; it MUST
  NOT reach the agent, the chat, any log, the database, either screen, an audit record or an export. The agent MUST
  only learn that a secret was received, by whom, when, and its expiry date.
- **FR-121**: The secret field MUST reject values shaped like an identifier (a GUID) with a message that the secret's
  ID was entered instead of its Value. It MUST also require the secret's expiry date, entered by the administrator
  from Entra's "Expires" column, and reject a date in the past or more than 2 years ahead. This date is the only
  source of the expiry shown in the session and of the 30-day warning; the agent MUST NOT ask for credential
  listings to find it.
- **FR-122**: The owner MUST be able to replace the secret at any time; the replacement takes effect in ISC, any
  earlier value still in the vault is removed, and the change is audited (who, when; never the value).
- **FR-123** (extends 001 FR-026): Masking MUST recognise Entra client secret values in pasted text and screenshots.
  A secret detected in the chat MUST be masked before display, storage or reaching the agent, and the owner MUST be
  told to create a new secret.
- **FR-124**: The leak scan run before every rollout MUST include Entra secret patterns.
- **FR-125**: The vault copy of the secret MUST be kept only until Test Connection passes for the source with it; the
  product MUST then delete it from the vault and record the deletion (when; never the value). From then on ISC holds
  the only copy; a rebuild or re-run that needs the secret again MUST request a new secret through the secret field.

**Agent actions in ISC (on the IAM engineer's order, 001 FR-016–FR-020)**

- **FR-130**: Before creating anything the agent MUST check, read-only: that the ISC tenant offers the Microsoft
  Entra connector; existing sources on that connector for the same Entra tenant (with owner); a same-named source
  owned by someone else (001 FR-018).
- **FR-131**: The agent MUST create the Entra source and configure its connection (tenant domain, Application ID,
  the vaulted secret, client-credentials sign-in) and the settings of the chosen capabilities, matching the
  reference configuration (base aggregation settings: all groups including Microsoft 365, delta aggregation; Teams,
  access packages and managed identities off unless a capability needs them).
- **FR-132**: For service principals, the agent MUST make sure the source's account model contains the
  service-principal attributes, adding only missing ones.
- **FR-133**: The proof MUST run in this order, each step only after the previous one succeeded: read a few live
  accounts; Test Connection; entitlement aggregation; account aggregation (always a full read); dataset aggregation
  when AI agents are chosen. The agent MUST report each step's result and the account (users and service principals
  separately), entitlement and AI-agent counts.
- **FR-139**: An aggregation still running after 30 minutes MUST be shown as *pending* (not failed) with its elapsed
  time; the product MUST keep following it without anyone watching and post its result to the session thread when
  it ends, then continue with the next proof step only if it succeeded.
- **FR-134**: For AI agents the agent MUST turn on the Foundry dataset's scheduled aggregation after the first
  successful run, and leave Copilot Studio and Agent 365 discovery off.
- **FR-135**: When the ISC tenant does not allow an action to be started by automation (e.g. dataset aggregation),
  the agent MUST report it as a tenant limitation, give the IAM engineer the exact place in the ISC interface to do
  it, and verify the result afterwards; this MUST NOT count as a failed onboarding.
- **FR-136**: For provisioning the agent MUST set the account-creation definition (using the given domain and usage
  location) and the account-matching rule, keeping existing definitions unless told to replace them; joiner/leaver
  actions on identity profiles MUST be prepared for the IAM engineer's review, not applied. The provisioning proof
  MUST NOT write to the directory: it consists of the definition and rule being present on the source and a
  read-only check, from the administrator's pasted output, that the application holds the write permissions and the
  User Administrator role.
- **FR-137**: Every change the agent makes in ISC MUST have an action record naming the IAM engineer who ordered it
  (001 FR-020).
- **FR-138**: For the Entra type the product MUST use only SailPoint's current versioned API (v2026) and no beta
  endpoints; any experimental endpoint used MUST be listed in the playbook.

**Failures**

- **FR-140** (extends 001 FR-024): The agent MUST recognise the documented Entra failures and give cause, side,
  a read-only confirmation and the fix: invalid client secret (ID instead of Value / other app); expired secret;
  application not found in tenant / wrong domain; Conditional Access blocking the application; insufficient
  privileges on an object type (missing permission, incl. app roles); consent not yet propagated; Test Connection
  "configuration already exists"; empty account read in delta mode; Agent 365 needs a user refresh token; Copilot
  Studio not set up; dataset aggregation not available to automation.

### Key Entities

- **Entra connector type**: the catalog entry and its playbook — capabilities, owner steps, source settings,
  checks and failures.
- **Capability selection**: per session, which of directory / service principals / AI agents / provisioning are on,
  plus their inputs (subscriptions; new-account domain and usage location) and the provisioning warning acceptance.
- **SailPoint application registration**: per session, display name and Application (client) ID; created or
  confirmed by the Entra administrator.
- **Application secret reference**: per session, a handle to the vaulted value, who provided it, when, expiry date,
  replaced-at, deleted-from-vault-at (state: received → in ISC → vault copy deleted); never the value.
- **Entra source**: the ISC source created (or, in an extend-source session, targeted) by the session, with its
  capabilities, proof results and counts. One source can be linked to several sessions over time.
- **Dataset run**: per AI-agents session, the Foundry dataset aggregation result, AI-agent count and schedule state.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-101**: A first-time Entra administrator completes their part of a directory-only onboarding in 15 minutes or
  less, and a full directory-only session (start to successful proof) takes 30 minutes or less for a tenant of up to
  5,000 accounts. Larger tenants are not held to the 30-minute target; their aggregations follow FR-139.
- **SC-102**: Across all test and demo sessions, no application secret value (or recognisable part of it) appears in
  any thread, log, stored record, screenshot, export or model input — verified by the leak scan and by searching
  stored data for the test secrets.
- **SC-103**: For each documented Entra failure (FR-140), the agent names the correct cause and side in at least 9 of
  10 trials using the sample errors and screenshots.
- **SC-104**: After a successful session, the reported user, service-principal, entitlement and AI-agent counts match
  the counts visible in Entra / Azure for the chosen scope.
- **SC-105**: The administrator's steps for a directory-only session request no write permission and no directory
  role (checked automatically against the playbook).
- **SC-106** (keeps 001 SC-009): Adding Entra ID changes nothing an AWS SaaS user sees or does; existing AWS SaaS
  tests pass unchanged.
- **SC-107**: 100% of agent changes in ISC during an Entra session have an action record naming the ordering IAM
  engineer.

## Assumptions

- The Entra administrator holds an active Global Administrator or Privileged Role Administrator role (activated
  first if it is eligible through PIM).
- The ISC tenant has the Microsoft Entra connector; AI agents additionally need SailPoint's machine-identity
  features (Agentic Fabric) to be licensed; custom security attributes need Entra ID P1.
- The behaviour, permissions, settings, checks and failures come from the `sailpoint-isc-entra-connector` skill,
  verified live on 2026-10-08 (directory, service principals, AI agents via the interface fallback); provisioning is
  specified from SailPoint documentation and must be tested against a real tenant before release (001 FR-028).
- Some tenants do not allow dataset aggregation to be started by automation; the interface fallback (FR-135) covers
  this.
- The secret is shown to the administrator once by Entra when created; if lost, a new one is created rather than
  recovered.
- Out of scope: Copilot Studio and Agent 365 agent discovery, Exchange Online, Teams and PIM capabilities,
  certificate-based sign-in, changes to the AWS SaaS type, moving the AWS SaaS type to the v2026 API, and applying
  joiner/leaver actions to identity profiles automatically.
- Demo and test data use fictional names (constitution).
- Tests and CI do not call paid models by default; the Entra failure evaluation (SC-103) is an explicit, costed
  opt-in run (constitution IV).
