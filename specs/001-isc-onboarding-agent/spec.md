# Feature Specification: SailPoint ISC Application Onboarding Agent

**Feature Branch**: `001-isc-onboarding-agent`

**Created**: 2026-10-07 (revised 2026-10-07: generalised to any connector type; actor renamed to Application owner;
revised 2026-10-07: one thread per person, per the design canvas; revised 2026-10-07: suggested replies;
revised 2026-10-07: scrolling threads and a prominent waiting banner, per the updated design canvas)

**Status**: Draft

**Input**: User description: "An AI onboarding agent, hosted on AWS Bedrock AgentCore, that guides two people through onboarding an application into SailPoint Identity Security Cloud (ISC). The app onboards any application through SailPoint connectors; connector types are added over time, and the AWS SaaS connector is the first one, as the proof of concept — the same outcome as the sailpoint-isc-aws-connector skill, but as a shared, multi-user web experience. Actors: the Application owner (for the AWS connector: the AWS owner; has access to the application and the agent never touches the application itself) and the IAM engineer for SailPoint (owns the ISC tenant; the agent acts on ISC for them), working in the same live onboarding session. The agent gives the application owner copy-ready setup instructions and checks the output; the IAM engineer orders the agent to create the connector (the order is the approval) and the agent creates, configures, tests and aggregates the source in ISC, troubleshooting from pasted errors and screenshots. Web app with separate local logins and a separate screen per actor, each with its own chat to the agent and a live view of the other side, plus a list of the connector types that can be set up. Out of scope v1: connector types other than AWS SaaS being available, the agent acting on the application itself, SSO login."

## Overview

Onboarding an application into SailPoint Identity Security Cloud (ISC) needs two kinds of access: someone who can
change the **application** (grant SailPoint a service account, a role, an API key) and someone who administers the
**SailPoint tenant** (create and configure the source). In most organizations these are two different people. This
feature gives both of them one shared, live onboarding session with an AI onboarding agent:

- The agent **never acts on the application**. It tells the **Application owner** exactly what to do there, with
  every value filled in, and checks what comes back.
- The agent **acts on SailPoint ISC** on the **IAM engineer**'s order: it creates, configures, tests and aggregates
  the source, and fixes SailPoint-side problems itself.
- Each person has their own login and screen and their own **thread** with the agent. Next to it, each screen shows
  the other person's thread live, read-only, so both always see what the other person and the agent are saying. The
  agent works between the two threads: it asks each person for what it needs in that person's thread and tells the
  other one what happened.

The product is built for **any connector type**. Each connector type brings its own setup steps for the application
owner, its own source settings, checks and known failures; the sessions, roles, conversation threads, audit and
secret handling are the same for all. In v1 one connector type is available, the **AWS SaaS connector** (the proof of
concept, with the existing `sailpoint-isc-aws-connector` skill as its reference); further types are listed as
planned and added later without changing the shared parts.

The end state of a session is the same for every connector type: a working source in ISC that has passed its
connection check, completed an aggregation and passes Test Connection, with the application granting SailPoint only
the access that connector type needs.

## Clarifications

### Session 2026-10-07

- Q: Whose SailPoint access should the agent use when it changes the tenant? → A: One service credential per tenant (source-admin rights), set up by an administrator; SailPoint's log shows the service account and the app's action record names the engineer who ordered each change.
- Q: Who acts as the "administrator" that creates accounts and sets up each tenant's SailPoint service credential? → A: IAM engineers with an "admin" flag on their account; there is no separate administrator role.
- Q: When both participants send the agent a message at nearly the same time, how should the agent handle them? → A: One shared conversation; messages handled one at a time in arrival order, a message arriving while the agent is busy shows as "queued" and is answered next.
- Q: How quickly should the agent's reply start appearing after a participant sends a message? → A: Replies stream in; first words within 5 seconds in 95% of cases, with a progress line during SailPoint or screenshot steps.
- Q: Before an uploaded screenshot is shown to the other participant and stored, how should the system guard against visible secrets? → A: The agent checks each screenshot first; a likely secret means the image is held and the uploader asked for a redacted version.
- Q: Should the screens and the agent's replies support Thai as well as English? → A: English only in v1; Thai (screens and agent replies) is planned for a later version, so v1 must not make it hard to add.
- Revision 2026-10-07 (user request): the product onboards any application; connector types come from a catalog and AWS SaaS is the first available one. The actor formerly called "AWS cloud engineer" is now the **Application owner** (for the AWS connector, the AWS owner).
- Q: How does a connector type get into the catalog and become available? → A: Fixed per release: the catalog ships with the product and admins can only read it; a new or newly available type means a new product version.
- Revision 2026-10-07 (user request, design canvas "ISC Onboarding Agent UI", IAM engineer and application owner artboards): the single shared chat is replaced by **one thread per person**. Each screen shows "Conversations" with the person's own thread (they post there) and the other person's thread (view only, live). The agent posts in whichever thread needs to act, and leaves a short relay note in the other thread. Messages from both threads still go through one queue in arrival order, and the agent sees both threads. This supersedes the "one shared conversation" answer above for what is shown; the queueing part stands.
- Q: When the application owner confirms a fix in their thread, may the agent rerun the failed SailPoint check by itself? → A: Yes, only the checks the IAM engineer already ordered (connection check, aggregation, Test Connection), recorded under that order with a note in the IAM engineer's thread; creating, changing or deleting anything in SailPoint still needs a new IAM engineer order.
- Q: While the agent is waiting for the application owner, is a new IAM engineer message answered right away or held? → A: Answered as soon as the agent is free; messages queue only while the agent is busy with another message, and "waiting for the application owner" is information only.
- Q: Where does the agent put news both participants need (all checks passed, a SailPoint step failed)? → A: The full message in the thread of the participant it concerns (SailPoint results: the IAM engineer's), plus a one-line relay note in the other thread; never the full text twice.
- Revision 2026-10-07 (user request): each participant's message box offers at least 3 suggested questions or answers for the current point in the session; the participant can pick one or type their own message.
- Revision 2026-10-07 (user request, updated design canvas "ISC Onboarding Agent UI"): each thread's messages scroll inside a fixed-height area with a vertical scrollbar, so the page does not grow and the message box stays in view; and the "waiting" message is a prominent banner (highlighted strip with a clock icon and a bold lead naming who is waited on) shown in both threads on both screens, worded for the viewer. The canvas's line "Your next message will be queued" is not adopted: per the answer above, waiting never holds a message back.

## Actors and terms

- **Application owner**: the person who can change the application being onboarded — for the AWS SaaS connector, the
  AWS owner who works in the AWS organization. The agent only ever gives this person instructions.
- **IAM engineer**: the person who administers the SailPoint tenant and orders SailPoint changes. May carry the
  admin flag.
- **Connector type**: one way of connecting an application to SailPoint (e.g. AWS SaaS, Microsoft Entra ID,
  Active Directory). Each type defines, for the agent: the application-side setup steps, the SailPoint source
  settings, the checks to run, and the known failures and fixes. Listed in the **connector catalog** as
  *available* or *planned*.
- **Onboarding session**: one application being onboarded with one connector type into one tenant, by one IAM
  engineer and one application owner.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - IAM engineer has the agent create a working source (Priority: P1)

The IAM engineer signs in, opens an onboarding session — picks an available connector type from the catalog, the
tenant, and fills in the details that connector type asks for (for AWS SaaS: source name and owner, AWS management
account, accounts in scope, region, optional AI-agent discovery) — and tells the agent to create the connector. The
instruction itself is the approval. The agent reads what it needs from the tenant (for AWS SaaS: the External ID and
the connector's current settings form), creates the source, configures it, runs the connection check, starts an
aggregation, runs Test Connection, and reports each result in the IAM engineer's thread, including the identifiers of the source and
of each task.

**Why this priority**: This is the outcome the organization is paying for: a governed application in SailPoint.
Without it nothing else matters.

**Independent Test**: With the application side already prepared (outside the system), an IAM engineer can sign in,
order creation of an AWS SaaS source, and end with a source that passes its connection check, has a completed
aggregation and passes Test Connection, all without touching the SailPoint admin console.

**Acceptance Scenarios**:

1. **Given** a signed-in IAM engineer in a session with all details the connector type requires filled in, **When**
   they tell the agent to create the connector, **Then** the agent creates and configures the source without asking
   for a further confirmation, and reports the source name and identifier.
2. **Given** a configured source and a correctly prepared application side, **When** the agent runs the connection
   check, aggregation and Test Connection, **Then** each result (success or failure, with the error text) appears in
   the chat and in the session status within one minute of the result being available.
3. **Given** a source with the requested name already exists and is owned by someone else, **When** the IAM engineer
   orders creation, **Then** the agent refuses to reuse or change it, says who owns it, and asks for a different name.
4. **Given** the session details are incomplete for the chosen connector type (e.g. no AWS management account),
   **When** the IAM engineer orders creation, **Then** the agent asks for the missing values before changing anything
   in SailPoint.
5. **Given** the IAM engineer picks a connector type marked *planned*, **When** they try to start the session,
   **Then** the system explains that this type is not available yet and offers the available types.

---

### User Story 2 - Application owner prepares the application from the agent's instructions (Priority: P1)

The application owner signs in, joins the same session, and receives step-by-step setup instructions for their
application from the agent, as defined by the connector type. Every instruction is ready to use, with the session's
values already in place. For the AWS SaaS connector these are AWS command-line instructions: management account,
role name for this tenant, the tenant's External ID, the required read-only policies, optional Bedrock / AgentCore
agent discovery permissions, and the target accounts. After each step the agent asks for the output (or a
screenshot), checks it, and either moves on or explains what is wrong and what to do next.

**Why this priority**: The SailPoint source cannot connect until the application trusts it. This is the other half of
the minimum viable onboarding; together with Story 1 it delivers a complete onboarding.

**Independent Test**: An AWS owner can follow only the agent's instructions, paste back the outputs, and end with an
AWS role that the agent confirms is correctly set up (right trust, right External ID, right policies), without the
agent ever having AWS access.

**Acceptance Scenarios**:

1. **Given** a session whose values are known to the agent, **When** the application owner asks what to do, **Then**
   the agent gives instructions with no remaining placeholders for values the session already knows, and states for
   each step what it changes and whether it is read-only.
2. **Given** the application owner pastes the output of a step, **When** the output shows success, **Then** the agent
   marks that application step done in the session status and gives the next step.
3. **Given** the pasted output shows an error or an unexpected state (e.g. an AWS role with that name already exists
   and was not created for this onboarding), **When** the agent reviews it, **Then** it explains the cause in plain
   language and gives the read-only step(s) needed to confirm it, and never proposes overwriting something in the
   application that was not created for this onboarding.
4. **Given** the application owner pastes text that contains a credential (for AWS: an access key, secret key or
   session token), **When** the message is received, **Then** the secret is masked before anyone (including the other
   participant) sees it or it is stored, and the agent reminds the application owner it never needs the application's
   credentials.

---

### User Story 3 - Each participant has their own thread and sees the other's live (Priority: P2)

Each participant has their own screen with two threads side by side under "Conversations": **their own thread with
the agent**, where they write, and **the other participant's thread**, which they can read live but not post in.
The agent works between the two: when it needs something from the other person it asks in that person's thread,
and it leaves a short relay note in the thread where the request came from (for example "Cause found in the
application owner's thread; fix sent to them"). Each screen also shows the session header (connector type, the
current status: application ready, source created, configured, connection check, aggregation, Test Connection, and
whether the other participant is online) and a side panel for that role: the source details and the SailPoint
action record for the IAM engineer; the setup steps (each marked read-only or change, with its state) and the
values the agent fills in for the application owner.

**Why this priority**: Separate threads keep each person's instructions readable while the read-only view of the
other thread keeps the shared visibility that turns two hand-offs into one conversation. The onboarding can still
succeed (more slowly) without it.

**Independent Test**: With both participants signed in on separate devices, a message one of them sends in their own
thread appears, within 2 seconds, in the read-only copy of that thread on the other screen; the agent's request to
the other person appears in that person's thread with a relay note in the first thread; status changes appear on
both screens without refreshing.

**Acceptance Scenarios**:

1. **Given** both participants are in the same session, **When** the IAM engineer sends a message in their thread,
   **Then** it appears in the IAM engineer's thread on the application owner's screen (read-only, labelled with the
   IAM engineer's name) within 2 seconds, and vice versa.
2. **Given** the IAM engineer's order needs something from the application owner (e.g. a read-only check after a
   failed connection check), **When** the agent replies, **Then** it reports the result in the IAM engineer's thread,
   posts the request in the application owner's thread, and adds a relay note in the IAM engineer's thread saying
   what it asked and of whom.
3. **Given** a participant views the other participant's thread, **When** they try to write there, **Then** they
   cannot: that thread is marked "View only" with no message box, and a note says to ask the agent in their own
   thread to relay a message.
4. **Given** the agent is waiting on one participant and is not busy, **When** the other participant sends a message
   in their own thread, **Then** their thread shows the waiting banner naming who the agent is waiting for, and the
   agent answers the new message straight away rather than holding it until the awaited participant responds.
5. **Given** a participant joins or rejoins a session in progress, **When** their screen opens, **Then** both threads
   show their full history and the current status.
6. **Given** each participant writes only in their own thread, **When** an application owner tries to order a
   SailPoint change, **Then** the agent declines in the application owner's thread and explains that SailPoint
   changes are ordered by the IAM engineer.
7. **Given** a thread holds more messages than fit in its area, **When** a participant views it, **Then** the
   messages scroll inside the thread with a vertical scrollbar, the newest message is in view, and the page itself
   does not grow: the thread's header, waiting banner, suggestions and message box stay where they are.
8. **Given** a participant has scrolled up to read older messages, **When** a new message arrives in that thread,
   **Then** their reading position is kept and they are told that new messages are below, with one action to jump to
   the newest.
9. **Given** the agent asks the application owner to do something and waits for them, **When** either participant
   looks at their screen, **Then** both threads on both screens show the waiting banner directly above the message
   box (or the view-only note): in the application owner's own thread it says the agent is waiting for them and what
   to do ("Waiting for you: run step 4 and paste the output"); in the IAM engineer's threads it names the application
   owner and what they are doing; the banner disappears in all four places as soon as the wait ends.

---

### User Story 4 - Troubleshoot errors together from text and screenshots (Priority: P2)

When the connection check, aggregation or Test Connection fails, or when a SailPoint admin screen shows an error,
the IAM engineer pastes the error text or uploads a screenshot. The agent analyses it, says whether the cause is on
the SailPoint side or the application side, fixes SailPoint-side problems itself (e.g. missing source settings), and
gives the application owner the steps to diagnose or fix application-side problems, or asks for a screenshot of the
application's console.

**Why this priority**: First-run failures are common (the AWS reference skill documents several). Fast, shared
diagnosis is the main time saving over doing this by email, but a clean first run doesn't need it.

**Independent Test**: For the AWS SaaS connector, reproduce each known failure (wrong External ID in the AWS trust,
missing source schema settings, missing AWS permission for a discovery dataset), submit the error as text or
screenshot, and check that the agent identifies the cause and that following its fix makes the next check pass.

**Acceptance Scenarios**:

1. **Given** a failed connection check caused by an AWS trust that does not include the tenant's External ID,
   **When** the IAM engineer pastes the error, **Then** the agent identifies the AWS trust as the cause and gives the
   AWS owner a command to view the trust and a command to correct it.
2. **Given** an aggregation error caused by missing source settings on the SailPoint side, **When** the IAM engineer
   reports it, **Then** the agent corrects the source settings itself, reruns the step, and reports the new result;
   if a known transient error recurs once, it retries once before escalating.
3. **Given** the IAM engineer uploads a screenshot of a SailPoint error page, **When** the agent receives it, **Then**
   it reads the error from the image and responds as if the text had been pasted.
4. **Given** a permission error for AI-agent discovery, **When** it is reported, **Then** the agent names the missing
   permission and gives the AWS owner the command to add it.
5. **Given** a participant uploads a screenshot that shows a credential, **When** the agent checks it, **Then** the
   image is not shown to the other participant or stored, and the uploader is asked for a redacted version.

---

### User Story 5 - Browse the connector catalog (Priority: P3)

Any signed-in user can open the connector catalog: the connector types the product can onboard, each marked
*available* or *planned*, with a short description of what the application owner will be asked to do and what the
agent sets up in SailPoint. The IAM engineer starts a session from an available type.

**Why this priority**: Makes the product's scope visible and sets expectations for later connector types, but a
single available type can be started without a catalog page.

**Independent Test**: Open the catalog as each role; AWS SaaS shows as available with its description; planned types
show without a start action.

**Acceptance Scenarios**:

1. **Given** the catalog, **When** a user opens it, **Then** every connector type shows its name, status
   (available / planned), the application-side access it will ask for, and what the agent configures in SailPoint.
2. **Given** an available type, **When** an IAM engineer chooses it, **Then** a new session form opens with that
   type's required details.
3. **Given** a planned type, **When** any user views it, **Then** no session can be started from it and the entry says
   it is planned.

---

### User Story 6 - Sign in and session history (Priority: P3)

Each participant signs in on a local login page with their own account, which has exactly one role (application
owner or IAM engineer). They can list the onboarding sessions they take part in, open a past session to read its
full history, and see every SailPoint change the agent made, with who ordered it and when.

**Why this priority**: Required for accountability and for coming back to a half-finished onboarding, but a single
live session can run without browsing history.

**Independent Test**: Sign in as each role, see only sessions you belong to, open a finished session, and find for
each SailPoint change the ordering user, time and result.

**Acceptance Scenarios**:

1. **Given** a user with the application owner role, **When** they sign in, **Then** they land on the application
   owner screen and cannot open the IAM engineer screen.
2. **Given** wrong credentials, **When** a user tries to sign in five times in a row, **Then** further attempts on that
   account are blocked for 15 minutes and the attempts are recorded.
3. **Given** a finished session, **When** either participant opens it, **Then** they see the full conversation,
   uploaded screenshots, status history and the list of SailPoint changes with who ordered each.

---

### User Story 7 - Pick a suggested question or answer instead of typing (Priority: P2)

Under the message box of their own thread, each participant sees at least three suggested messages that fit where
the session is right now: answers to what the agent just asked (e.g. "Here is the output:", "I get an error at this
step", "Explain what this step does"), the next sensible order for the IAM engineer (e.g. "Create the connector and
run the checks" before a source exists, "Rerun the connection check" after the application owner's fix), or common
questions ("What is left to do?", "Why did the connection check fail?"). Picking one puts it in the message box,
where the participant can send it as is, edit it first, or ignore it and type their own message.

**Why this priority**: Most messages in an onboarding are predictable. Suggestions save typing, show newcomers what
they can ask, and make the next step obvious, but everything still works by typing.

**Independent Test**: In a session at each status (no source yet, waiting for the application owner's command
output, connection check failed, all checks passed), each participant's own thread shows at least three suggestions
that fit that status and their role; picking one and sending it gives the same result as typing the same text.

**Acceptance Scenarios**:

1. **Given** a participant's own thread, **When** the message box is shown, **Then** at least three suggestions are
   offered beside it, and the box still accepts free text.
2. **Given** the agent has just asked the application owner to run a command and paste the output, **When** the
   owner looks at their suggestions, **Then** they include starting a reply with the output, saying the command
   failed, and asking what the step does; picking the first puts a starter text in the box for the output to be
   pasted after it.
3. **Given** no source exists yet, **When** the IAM engineer looks at their suggestions, **Then** one of them is the
   order to create the connector and run the checks; after the connection check has failed, one of them is to ask
   why it failed and one is to rerun it.
4. **Given** a suggestion has been picked, **When** the participant edits it or clears it, **Then** what is sent is
   exactly what is in the box; picking alone never sends a message or orders a SailPoint change.
5. **Given** the application owner's suggestions, **When** they are shown, **Then** none of them is a SailPoint order.
6. **Given** the agent replies or the status changes, **When** the reply finishes, **Then** the suggestions are
   refreshed to fit the new point in the session.
7. **Given** the other participant's view-only thread, **When** it is shown, **Then** it has no suggestions.

---

### Edge Cases

- One participant is offline: the other can keep working; messages and status changes wait in the session and appear
  when the absent participant signs back in.
- The IAM engineer orders creation before the application side is ready: the agent creates and configures the
  source, then reports that the connection check is waiting on the application steps and tells the application owner
  what is still missing.
- Both participants send messages at nearly the same time, or the IAM engineer orders "create" twice: the messages
  from both threads are queued and handled in arrival order (FR-006a); the second "create" sees that the source now
  exists and does not create a duplicate.
- A participant wants to tell the other person something directly: they cannot post in the other thread, so they ask
  the agent in their own thread; the agent relays it into the other thread, attributed to the sender (FR-006c).
- A value the application side depends on changes (for AWS SaaS: the tenant's External ID, or the session points at
  a different tenant): the agent warns that the application's trust must be updated and gives the application owner
  the change.
- Something in the application already carries the chosen name and belongs to someone else (for AWS SaaS: a role
  that trusts another tenant): the agent recommends a tenant-specific name instead of changing the existing item.
- A screenshot is unreadable or unrelated: the agent says what it could not read and asks for the error text or a
  clearer capture.
- Pasted text contains secrets or tokens: they are masked before display and storage (FR-026). A screenshot shows a
  secret: it is held, never reaches the other participant or storage, and the uploader is asked for a redacted
  version (FR-026a).
- SailPoint is unreachable or the tenant's service credential is rejected: the agent reports this to the IAM engineer
  without exposing the credential, and makes no further SailPoint changes until it is resolved.
- A step takes a long time (aggregation): the session shows it as running, and both screens update when it finishes.
- The agent writes to the person who is offline: the message waits in that person's thread, and the other screen shows
  the waiting banner naming them (FR-006g).
- A thread grows very long (hundreds of messages) or a single message is long (pasted command output): the thread
  scrolls inside its area and the page layout does not change; long lines wrap or scroll within their message rather
  than widening the thread.
- On a narrow screen (phone width) the two threads stack one above the other; each keeps its own scroll area and its
  waiting banner.
- The agent is asked to do something outside the session's connector type or outside onboarding (another connector
  type in the same session, acting on the application itself): it declines and explains the scope.
- A connector type is marked *planned*: it can be read about but no session can be started from it.
- The agent has no specific suggestions for the current point (for example right after an unexpected error): the
  thread still shows at least three general suggestions for that role ("What is left to do?", "Explain the last
  message", "Show me the current status").
- A suggestion would need a value the session does not know, or a secret: it is not offered; suggestions never
  contain or ask for credentials.

## Requirements *(mandatory)*

### Functional Requirements

**Access and roles**

- **FR-001**: The system MUST provide a login page with local accounts (username and password); each account has
  exactly one role: *Application owner* or *IAM engineer*.
- **FR-002**: An IAM engineer account MAY carry an *admin* flag. Admin IAM engineers MUST be able to create, disable
  and reset accounts, assign their role and admin flag, register tenants with their SailPoint service credential
  (FR-025); other users MUST NOT. Application owner accounts
  can never be admins. There is no self-registration; the first admin account is created when the system is
  installed. Admin actions are recorded like sign-ins (FR-004).
- **FR-003**: The system MUST show each role its own screen and MUST NOT let a user open the other role's screen or
  act with the other role's permissions.
- **FR-004**: The system MUST lock an account for 15 minutes after 5 consecutive failed sign-ins, end idle sessions
  after 30 minutes, and record sign-ins, failures and lockouts.

**Connector catalog**

- **FR-028**: The system MUST keep a connector catalog listing every connector type it knows, each with: name, status
  (*available* / *planned*), a plain-language description, the application-side access the application owner will be
  asked to grant, what the agent configures in SailPoint, and the details a session of that type requires. Any
  signed-in user can read it. The catalog is **fixed per product release**: no user, admin included, can add a type
  or change a type's status while the system runs; a new type, or a planned type becoming available, ships as a new
  product version with its playbook (FR-030) tested against a real tenant first.
- **FR-029**: In v1 exactly one connector type is *available*: **AWS SaaS** (AWS IAM users, groups and policies, plus
  optional Bedrock / AgentCore AI-agent discovery). The catalog MUST also list the planned types below so users can
  see the roadmap; a planned type cannot start a session.

  | Connector type | v1 status | Application owner will be asked to… | Agent configures in SailPoint… |
  |---|---|---|---|
  | AWS SaaS | available | create a cross-account IAM role with SailPoint's trust, External ID and read-only policies (plus discovery permissions if chosen) | AWS SaaS source: role, management account, accounts in scope, region, change-password policy, discovery regions |
  | Microsoft Entra ID | planned | register an app with read permissions and grant admin consent | Entra ID source: tenant, app credentials, scope |
  | Active Directory | planned | create a service account and a VA-reachable connection | AD source: domain, service account, search scopes |
  | Google Cloud Platform | planned | create a service account with read roles | GCP source: project/org, service account |
  | Microsoft Azure (resources) | planned | create an app registration with reader roles | Azure source: tenant, subscriptions |
  | Okta | planned | create a read-only API token | Okta source: org URL, token |
  | ServiceNow | planned | create an integration user with read roles | ServiceNow source: instance, user |
  | Salesforce | planned | create an integration user / connected app | Salesforce source: org, credentials |
  | Workday | planned | create an integration system user | Workday source: tenant, user |
  | Web Services (generic REST) | planned | provide an API account and endpoints | Web Services source: endpoints, auth |

  The planned list and its order are indicative; adding a type to the catalog MUST NOT require changes to sessions,
  roles, conversation threads, audit or secret handling (FR-030).
- **FR-030**: Every connector type MUST define, for the agent, the same four things: application-side setup steps
  (instructions with their values, each marked read-only or change), SailPoint source settings (at creation and at
  configuration), the checks to run (connection check, aggregation, Test Connection) and the known failures with
  their fixes. All other behaviour in this specification is connector-independent.

**Onboarding sessions and conversation threads**

- **FR-005**: An IAM engineer MUST be able to create an onboarding session by choosing an available connector type and
  a tenant, filling in the details that type requires (for AWS SaaS: source name, source owner, AWS management account,
  accounts in scope, region, optional AI-agent discovery with regions), and inviting one application owner to it.
- **FR-006**: Each session MUST have two **threads**, one per participant (the IAM engineer's and the application
  owner's), each holding that participant's messages and the agent's messages to them, in order. Each screen MUST
  show, under "Conversations", the participant's own thread with a message box, and the other participant's thread
  live and **view only** (no message box, marked "View only", with a note to ask the agent to relay a message).
  Every message is labelled with its speaker and time.
- **FR-006a**: Messages from both threads MUST go into one queue per session and be handled one at a time in arrival
  order, each with the full history of **both** threads (including the other participant's latest messages and
  actions). A message that arrives while the agent is busy MUST be shown as "queued" in its thread, on both screens,
  and answered next; no message type jumps the queue. "Queued" means only that the agent is busy with another
  message: waiting for the other participant never holds a message back. A participant's thread MUST say when the
  agent is waiting on the other participant, as information only.
- **FR-006b**: Agent replies MUST stream into their thread on both screens as they are written, and while the agent
  works on SailPoint or reads a screenshot it MUST show a progress line in that thread saying what it is doing (e.g.
  "checking the source in SailPoint…").
- **FR-006c**: The agent MUST answer in the thread of the participant who wrote, and MUST post anything the other
  participant has to do (a check, a fix, a confirmation, a relayed message) in that participant's thread. When it
  does, it MUST add a short **relay note** in the originating thread saying what it asked or passed on and to whom;
  a relayed message names the person it came from. The agent never posts the other participant's instructions in the
  wrong thread. News that concerns both (for example all checks passed, or a SailPoint step failed) MUST go in full
  in the thread of the participant it belongs to (SailPoint results: the IAM engineer's) with a one-line relay note
  in the other thread, never the full text in both.
- **FR-006d**: Each screen MUST show, beside the threads, the panel for its role: for the IAM engineer, the source
  details (connector type, name, owner, tenant, the type's details, External ID and access) and the SailPoint action
  record with who ordered each change; for the application owner, the connector type's setup steps in order, each
  marked read-only or change and shown as pending, current, done or failed, and the session values the agent fills
  into the instructions. The session header on both screens shows the connector type, the onboarding status
  (FR-008) and whether the other participant is online.
- **FR-006e**: Each participant's own thread MUST offer at least three **suggested messages** next to its message
  box, chosen for that participant's role and the current point in the session: answers to what the agent has just
  asked, the next likely order (IAM engineer only), and common questions. Picking a suggestion MUST only put its text
  in the message box (the participant may send it, edit it or discard it) and MUST never send a message or order a
  SailPoint change by itself. The application owner MUST never be offered a SailPoint order. Suggestions MUST be
  refreshed after each agent reply and status change, MUST NOT contain or ask for credentials or values the session
  does not know, and MUST NOT appear in the other participant's view-only thread. When nothing specific fits, at
  least three general suggestions for that role are shown. Free typing MUST always remain available.
- **FR-006f**: Each thread's messages MUST scroll inside a fixed-height area of the thread with a vertical
  scrollbar, so a long conversation never pushes the thread's header, waiting banner, suggestions or message box out
  of view. A thread MUST open at its newest message and MUST stay at the newest message as new ones arrive while the
  participant is at the bottom; if the participant has scrolled up, the reading position MUST be kept and a "new
  messages" cue MUST offer a jump to the newest. Both threads on a screen scroll independently.
- **FR-006g**: While the agent is waiting on a participant, both threads on both screens MUST show a **waiting
  banner** between the messages and the message box (or the view-only note): a highlighted strip, set apart from the
  messages by colour and a clock icon, with a bold lead that names who is waited on, followed by what they need to
  do. Its wording fits the viewer: the awaited participant sees "Waiting for you:" and their next step; the other
  participant sees the awaited person's name and what the agent is waiting for. The banner is announced to screen
  readers as a status, has at least 4.5:1 text contrast, is visible without scrolling, and disappears everywhere as
  soon as the wait ends. It is information only and never says that messages will be held (FR-006a).
- **FR-007**: Users MUST be able to attach screenshots (common image formats, up to 10 MB each) to a message; the agent
  MUST be able to read text and errors in them.
- **FR-008**: The session MUST show the connector type and the current onboarding status — application ready, source
  created, source configured, connection check, aggregation, Test Connection — each as not started / in progress /
  passed / failed, updated live on both screens. The label of the first step MAY be specialised by connector type
  (for AWS SaaS: "AWS role ready").
- **FR-009**: The system MUST keep the full session history (both threads with their messages and relay notes,
  screenshots, status changes, SailPoint actions) and let both participants reopen it later.

**Application owner guidance (application side)**

- **FR-010**: The agent MUST NOT perform any action in the application being onboarded and MUST NOT hold, request or
  accept the application's credentials (for AWS: access keys, secret keys, session tokens).
- **FR-011**: The agent MUST give the application owner the connector type's step-by-step setup instructions with all
  values the session knows filled in, and label each step as read-only or as a change. For AWS SaaS these are
  command-line instructions that create the role and policies the connector needs (management account, role name,
  tenant External ID, accounts in scope, optional discovery permissions).
- **FR-012**: The default application-side access MUST be the least the connector type needs for aggregation
  (read-only); provisioning access is out of scope for v1 unless the IAM engineer explicitly asks, in which case the
  agent states that enabling provisioning in SailPoint cannot be undone.
- **FR-013**: The agent MUST check pasted output (or a screenshot) against the expected result and either confirm the
  step or explain the problem and give the next read-only step to investigate.
- **FR-014**: Before any change step, the agent MUST have the application owner run a read-only check for an existing
  item with the same name (for AWS SaaS: role or stack), and MUST NOT propose modifying anything that was not created
  for this onboarding; when the name is taken it proposes a tenant-specific name.
- **FR-015**: When several SailPoint tenants share one application (for AWS SaaS: one AWS account), the agent MUST give
  each tenant its own application-side identity (its own role) so one tenant's setup never cuts off another's.

**IAM engineer orders (SailPoint side)**

- **FR-016**: Only the IAM engineer can order SailPoint changes; an explicit order from the IAM engineer in their thread
  (e.g. "create the connector", "rerun aggregation") is the approval, and the agent MUST carry it out without asking
  for a further confirmation, provided all required values are known.
- **FR-016a**: An IAM engineer's order to run the checks (connection check, aggregation, Test Connection) stands
  until they pass: when the application owner confirms in their thread that an application-side fix is done, the
  agent MUST rerun the failed check and the checks after it without a new order, record each rerun as ordered by
  that IAM engineer, and post the result in the IAM engineer's thread. A confirmation from the application owner
  MUST NOT lead to any other SailPoint change (creating, configuring, fixing or deleting a source); those need a new
  order from the IAM engineer.
- **FR-017**: For every available connector type the agent MUST be able to: read what the tenant provides for that
  type (for AWS SaaS: the tenant External ID and the connector's current settings form); create the source with the
  settings the connector type needs at creation; configure it (for AWS SaaS: role name, management account, accounts
  in scope, region, change-password policy and optional discovery regions); run a connection check; start account and
  entitlement aggregation; run Test Connection; and report each result.
- **FR-018**: The agent MUST check for an existing source with the same name and MUST NOT reuse, change or delete a
  source owned by someone else; it may delete and recreate only a source created in this session, and only on the
  IAM engineer's order.
- **FR-019**: The agent MUST refuse SailPoint change requests from the application owner (other than the check reruns
  of FR-016a) and anything outside the session's connector type and onboarding, and say why.
- **FR-020**: Every SailPoint change MUST be recorded with: what changed, on which source, who ordered it, when, and the
  result; the record is visible to both participants.

**Troubleshooting**

- **FR-021**: The agent MUST analyse pasted errors and screenshots and state whether the cause is on the SailPoint
  side or the application side, citing the error it relied on.
- **FR-022**: For SailPoint-side causes the agent MUST apply the fix itself (e.g. add missing source settings) and
  rerun the failed step; for a known transient failure it MUST retry once before reporting failure.
- **FR-023**: For application-side causes the agent MUST give the application owner the read-only steps to confirm the
  cause, then the change steps to fix it, or ask for a specific screenshot of the application's console.
- **FR-024**: For each available connector type the agent MUST recognise that type's documented failures. For AWS
  SaaS: AWS trust missing the tenant's External ID or the right SailPoint principal; missing schema-related source
  settings; a stale connector configuration that clears on retry; missing permissions for Bedrock or AgentCore
  discovery; missing change-password policy; role-name collisions.

**Secrets and data protection**

- **FR-025**: The agent MUST act on SailPoint with one service credential per tenant (source-administration rights),
  configured by an admin IAM engineer (FR-002); users never enter or link personal SailPoint credentials. The
  credential MUST be held by the system and never shown in any chat, screen, screenshot caption or log, to either
  participant. Because SailPoint's own audit shows the service account, the system's action record (FR-020) is the
  authoritative link between each change and the IAM engineer who ordered it.
- **FR-026**: The system MUST mask access keys, secret keys, tokens, passwords and client secrets in messages before
  they are displayed, stored or sent to the agent.
- **FR-026a**: The agent MUST check every uploaded screenshot for visible secrets before it is shown to the other
  participant or stored. If it sees a likely secret, the image MUST be held (shown only to the uploader, not stored,
  not used for diagnosis) and the uploader asked to re-upload a cropped or redacted version; a held image is
  discarded once replaced or after the uploader dismisses it.
- **FR-027**: Non-secret identifiers the application side needs (for AWS SaaS: the tenant External ID, which goes in
  the AWS trust) MAY be shown to both participants; credentials MUST NOT.

### Key Entities

- **User**: a person who signs in; username, display name, role (application owner or IAM engineer), admin flag
  (IAM engineers only), status (active, locked, disabled).
- **Tenant**: a SailPoint ISC tenant registered by an admin IAM engineer; name, address, service credential (stored
  secretly, never displayed after entry), values read from it per connector type (for AWS SaaS: External ID),
  status (usable / credential rejected).
- **Connector type**: a catalog entry shipped with the product release (read-only at run time); name, status (available / planned), description, application-side access
  asked for, SailPoint settings configured, required session details, and the agent's playbook (setup steps, source
  settings, checks, known failures).
- **Onboarding session**: one application onboarded with one connector type into one tenant; source name, owner,
  tenant, connector type, the type's details (for AWS SaaS: management account, accounts in scope, region, discovery
  options), participants (one IAM engineer, one application owner), created/finished time, current status.
- **Thread**: one participant's conversation with the agent in a session; session, participant (IAM engineer or
  application owner). Each session has exactly two; each is readable by both participants, writable only by its own.
- **Message**: one entry in a thread; thread, speaker (the thread's participant or the agent), kind (message or
  relay note; a relay note names the other thread it refers to), text after masking, attachments, queue state, time.
- **Attachment**: an uploaded screenshot; message, image, upload time, secret check result (passed / held).
- **Suggested message**: one offered question or answer in a participant's thread; thread, text, kind (answer, order,
  question), the point in the session it was offered for. Not stored as a message unless the participant sends it.
- **Onboarding step**: one tracked milestone (application ready, source created, configured, connection check,
  aggregation, Test Connection); state and time of each change.
- **SailPoint action record**: one change the agent made in SailPoint; session, source, action, ordered by, time,
  result, task identifiers.
- **Application setup step**: one instruction given to the application owner; instruction (for AWS SaaS: command),
  read-only or change, expected result, confirmed or not.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A first-time onboarding with both participants available finishes (source passes connection check,
  aggregation completes, Test Connection passes) in under 60 minutes, excluding aggregation run time, for every
  available connector type (v1: AWS SaaS).
- **SC-002**: The application owner can follow 100% of the agent's setup instructions as given, with no values to look
  up or fill in by hand, for the values the session knows.
- **SC-003**: A message, relay note or status change from one participant's side appears in the matching thread or
  header on the other participant's screen within 2 seconds in 95% of cases.
- **SC-003a**: The first words of the agent's reply appear within 5 seconds of a message being handled in 95% of cases
  (long SailPoint tasks such as aggregation excepted; their progress line appears within 5 seconds instead).
- **SC-004**: Zero SailPoint credentials and zero unmasked application or SailPoint secrets appear in any screen,
  stored transcript or log in testing (including deliberate attempts to paste them).
- **SC-005**: For each failure listed in FR-024 for an available connector type, the agent names the correct cause in
  its first reply in at least 9 of 10 trials, and following its fix makes the failed step pass.
- **SC-006**: 100% of SailPoint changes have an action record naming the ordering IAM engineer.
- **SC-007**: In a test with an existing application-side item and source owned by someone else, the agent never
  proposes or makes a change to either.
- **SC-008**: In a usability trial, at least 4 of 5 pairs (IAM engineer + application owner) complete an onboarding
  without consulting SailPoint or application documentation.
- **SC-009**: Adding a second connector type to the catalog changes nothing a user sees in login, sessions, threads,
  status, history or audit other than the new catalog entry and that type's own details and instructions.
- **SC-010**: In testing, 100% of the agent's requests to the other participant appear in that participant's thread
  (never only in the requester's), each with a relay note in the originating thread, and no participant can post in
  the other participant's thread.
- **SC-011**: At every point where a participant can write, at least 3 suggestions that fit their role and the current
  status are shown within 1 second of the agent's reply finishing; in a test onboarding, both participants can reach
  a working source by picking suggestions and pasting command output only, without typing any other message.
- **SC-012**: With 200 messages in each thread, the message box, suggestions and waiting banner of every thread stay
  visible without scrolling the page, and each thread can be scrolled from its newest to its oldest message.
- **SC-013**: In 100% of tested waiting states the waiting banner appears in both threads on both screens within
  2 seconds and is gone within 2 seconds of the wait ending; in a usability trial, at least 4 of 5 participants can
  say who the agent is waiting for, and what that person must do, within 5 seconds of looking at their screen.

## Assumptions

- Hosting is a given of the request: the onboarding agent runs on AWS Bedrock AgentCore. The web app and its storage
  may run alongside it; their hosting is a planning decision.
- The product is designed for any connector type; v1 makes exactly one available, the SailPoint **AWS SaaS**
  connector (AWS IAM users, groups and policies, plus optional Bedrock / AgentCore AI-agent discovery), as the proof
  of concept. The virtual-appliance AWS connector and IAM Identity Center are not part of that type. Other types are
  listed as planned (FR-029) and delivered in later versions.
- For AWS SaaS, the behaviour and knowledge (required AWS policies, connector settings, known failures and fixes)
  follow the existing `sailpoint-isc-aws-connector` skill, which is proven on two tenants; it is the reference for
  correctness. Each later connector type will need an equivalent reference.
- An admin IAM engineer sets up the per-tenant SailPoint service credential (FR-025) before the tenant can be used in
  a session.
- For AWS SaaS, the AWS organization has all features enabled and the AWS owner can work in the management account; if
  not, the agent says so and limits the setup to the management account.
- One session has exactly one IAM engineer and one application owner; several sessions may run at the same time.
- Picking a suggestion fills the message box rather than sending at once, so an IAM engineer's order is always a
  deliberate send (FR-016) and a participant can add details before sending. Suggestions come from the agent with
  each reply, with fixed role-and-status defaults when the agent offers none.
- The two session screens follow the design canvas "ISC Onboarding Agent UI" (artboards "IAM engineer — session" and
  "Cloud engineer — same session", now the application owner's screen): header with status, role panel on the left,
  "Conversations" with the own thread and the other participant's view-only thread side by side. The canvas is the
  visual reference; this spec states the behaviour.
- Accounts are few (a team, not a public service); local accounts seeded by admin IAM engineers are enough;
  single sign-on is out of scope for v1.
- Session history is kept for 90 days, then deleted; action records follow the organization's audit retention.
- v1 is English only: screens and agent replies. Thai support for both (with commands, identifiers and quoted
  SailPoint/application error texts staying as emitted) is planned for a later version, so all user-facing text in v1
  must be kept so it can be translated without redesign. Desktop browsers only; a usable phone layout is not required
  for v1.
- Thread scrolling follows common chat behaviour: newest at the bottom, auto-follow only while the reader is at the
  bottom, and a "new messages" cue when they are not. The exact thread height is a design decision (the canvas uses
  one fixed height per thread on a desktop screen); what matters is that the page never grows with the conversation.
- The waiting banner's look (amber strip, clock icon, bold lead) comes from the updated design canvas; the spec
  fixes only that it stands out from messages, names who is waited on and for what, and meets the contrast rule.
