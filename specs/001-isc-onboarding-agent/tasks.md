---
description: "Task list for the SailPoint ISC Application Onboarding Agent (spec 001)"
---

# Tasks: SailPoint ISC Application Onboarding Agent

**Input**: Design documents from `specs/001-isc-onboarding-agent/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: The spec does not ask for test-first development. Test tasks are included only where a success criterion
has no other way to be verified:
- SC-004: zero secrets leak (masker corpus);
- FR-016/FR-019 and SC-007: the role gate and the collision rule;
- SC-005: diagnosis accuracy evals;
- SC-001–SC-003: the two-browser end-to-end run.

**Organization**: by user story (spec.md US1–US6), so each story can be built and demonstrated on its own.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an unfinished task)
- **[Story]**: US1–US6 from spec.md
- Paths follow plan.md: `onboarding/api`, `onboarding/agent`, `onboarding/web`, `onboarding/catalog`, `onboarding/deploy`

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Create the `onboarding/` product directory and its toolchains.

- [X] T001 Create the directory tree from plan.md "Source Code" (`onboarding/{api,agent,web,catalog,deploy}` with the listed subfolders) and an `onboarding/README.md` stating the local-cluster + AgentCore split from research.md R1
- [X] T002 Initialize the session API as a Python 3.12 uv project in `onboarding/api/pyproject.toml` (fastapi, uvicorn, pymongo>=4.10 async API, argon2-cffi, boto3, sse-starlette, pydantic-settings; dev: pytest, pytest-asyncio, respx, httpx) with package `onboarding/api/src/onboarding_api/__init__.py`
- [X] T003 [P] Initialize the agent as a Python 3.12 uv project in `onboarding/agent/pyproject.toml` (bedrock-agentcore, anthropic[bedrock], httpx, pyyaml; dev: pytest, respx) with package `onboarding/agent/src/onboarding_agent/__init__.py`
- [X] T004 [P] Scaffold the Angular 22 standalone app in `onboarding/web/` (`ng new` with routing, `@angular/localize` added, `baseHref` `/onboarding/`); copy the Deloitte tokens and fonts from `chatbot/src/styles.css` into `onboarding/web/src/styles.css`
- [X] T005 [P] Add ruff + mypy config to `onboarding/api/pyproject.toml` and `onboarding/agent/pyproject.toml`, and prettier/eslint to `onboarding/web/`
- [X] T006 [P] Add Makefile targets `onboarding-images`, `onboarding-agent`, `onboarding-agent-delete`, `onboarding-up`, `onboarding-down`, `onboarding-bootstrap`, `onboarding-evals`, `onboarding-e2e` to the root `Makefile`, each calling a script in `onboarding/deploy/scripts/` (scripts stubbed with `die "not implemented"` via `scripts/lib.sh`)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Everything every story needs: settings, MongoDB, masking, sign-in, tenants, catalog loading, sessions, the
shared turn queue, the agent skeleton, the live stream and the cluster deployment.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

### API core

- [X] T007 Implement settings in `onboarding/api/src/onboarding_api/config.py` (pydantic-settings: `MONGO_URI`, `AWS_REGION`, `AGENT_RUNTIME_ARN`, `CATALOG_DIR`, `COOKIE_SECURE`, `BASE_PATH=/onboarding/api`)
- [X] T008 Implement the Mongo connection and index bootstrap in `onboarding/api/src/onboarding_api/db.py`:
  - `users.username` unique;
  - `auth_sessions` TTL on `expires_at` (`expireAfterSeconds: 0`);
  - `sessions` `{iam_engineer_id:1,status:1}`, `{application_owner_id:1,status:1}` and TTL on `expires_at`;
  - `messages` `{session_id:1,seq:1}` unique + TTL;
  - `events` `{session_id:1,event_id:1}` unique + TTL;
  - `actions` `{session_id:1,at:1}`, with **no TTL** on `actions` or `audit`;
  - GridFS bucket `screenshots`.
- [X] T009 Implement the secret masker in `onboarding/api/src/onboarding_api/masking.py`. Patterns (research R9):
  - `AKIA|ASIA[0-9A-Z]{16}`;
  - 40-character AWS secret keys in key=value / JSON context;
  - AWS session tokens;
  - JWTs `eyJ…`;
  - 64-hex ISC secrets;
  - `password|secret|token: <value>` pairs.

  Returns `(text, masked: bool)`; replacement text is `[masked]`.
- [X] T010 Write the masker corpus test in `onboarding/api/tests/unit/test_masking.py` (real-looking keys from each family must be masked; External IDs, account ids and ARNs must NOT be masked: FR-027)
- [X] T011 Add a logging filter in `onboarding/api/src/onboarding_api/logging.py` that passes every log record's message and args through the masker and emits JSON with `turn_id` (research R14)
- [X] T012 Implement the `users` repository in `onboarding/api/src/onboarding_api/auth/users.py`:
  - `username` unique, 3–64 chars, `[a-z0-9._-]`; `display_name` 1–100 chars;
  - `role` ∈ `iam_engineer` | `application_owner`;
  - `is_admin` may be `true` only when `role = iam_engineer`;
  - argon2id `password_hash`;
  - `status` ∈ `active` | `locked` | `disabled`;
  - `failed_attempts`, `locked_until`.
- [X] T013 Implement sign-in in `onboarding/api/src/onboarding_api/auth/routes.py` + `auth/sessions.py`, per the `/auth/login`, `/auth/logout` and `/auth/session` contracts:
  - 256-bit cookie `ob_session` (HttpOnly, Secure, SameSite=Strict); only its SHA-256 is stored as `auth_sessions._id`;
  - 30-minute sliding idle;
  - lock for 15 minutes after the 5th consecutive failure (401 with attempts left, 423 when locked);
  - CSRF token in `Me`.
- [X] T014 Implement the dependencies `current_user`, `require_role(role)` and `require_admin` in `onboarding/api/src/onboarding_api/auth/deps.py` (403 `forbidden_role`), plus CSRF header checking
- [X] T015 Implement the audit writer in `onboarding/api/src/onboarding_api/audit/audit.py` (`kind` ∈ `sign_in` | `sign_in_failed` | `locked` | `user_created` | `user_disabled` | `password_reset` | `role_changed` | `tenant_added` | `credential_replaced`; no TTL) and call it from T013
- [X] T016 Implement the first-admin bootstrap command in `onboarding/api/src/onboarding_api/bootstrap.py` (reads username/password from env provided by a one-time Kubernetes Secret, creates an `iam_engineer` with `is_admin: true` only if no admin exists)

### Catalog and playbooks

- [X] T017 [P] Write `onboarding/catalog/catalog.yaml` with the AWS SaaS entry (fields exactly as in `contracts/connector-playbook.md`, `status: available`) and nine planned entries:
  - Microsoft Entra ID, Active Directory, Google Cloud Platform, Microsoft Azure (resources), Okta, ServiceNow,
    Salesforce, Workday, Web Services (generic REST);
  - each with `owner_asks` and `agent_configures` text from spec FR-029.
- [X] T018 Implement the read-only catalog loader in `onboarding/api/src/onboarding_api/catalog/catalog.py`:
  - validates entries;
  - validates session `details` against `session_fields`, with types `string`, `string_list`, `region`,
    `region_list`, `aws_account_id` (12 digits), `aws_account_id_list`;
  - applies defaults, e.g. `role_name` = `SailPointISCRole-{tenant}`.
- [X] T019 Implement `GET /catalog` in `onboarding/api/src/onboarding_api/catalog/routes.py` (any signed-in user; FR-028)

### Tenants and the SailPoint credential (FR-025)

- [X] T020 Implement the AgentCore Identity wrapper in `onboarding/api/src/onboarding_api/tenants/identity.py`:
  - create/update a custom OAuth2 credential provider named `onboarding-isc-<tenant>`;
  - client-credentials flow; token URL `https://<api_host>/oauth/token`; client id/secret passed through, never
    stored or logged;
  - delete.
  - Research R4's fallback (Secrets Manager) sits behind the same interface.
- [X] T021 Implement the tenant service in `onboarding/api/src/onboarding_api/tenants/service.py`:
  - PAT shape check: client id `^[0-9a-f]{32}$`, secret `^[0-9a-f]{64}$`, with a 422 "ID and secret look swapped"
    message as in the skill;
  - accept a UI host and convert it to the API host (`acme.identitynow-demo.com` → `acme-demo.api.identitynow-demo.com`
    rule from the skill);
  - `credential_hint` = last 4 characters of the client id;
  - `check()` = token request + `GET /beta/tenant` to fill `external_id` and set `status` ∈ `usable` |
    `credential_rejected` | `unchecked`.
- [X] T022 Implement `/admin/tenants`, `/admin/tenants/{id}/credential` and `/admin/tenants/{id}/check` in `onboarding/api/src/onboarding_api/tenants/routes.py` (admin only; responses never contain the secret; audit `tenant_added` / `credential_replaced`)

### Sessions, queue, agent client, live stream

- [X] T023 Implement the session repository in `onboarding/api/src/onboarding_api/sessions/repo.py`:
  - fields per data-model `sessions`;
  - `steps` keys `application_ready`, `source_created`, `configured`, `connection_check`, `aggregation`,
    `test_connection`, each state ∈ `not_started` | `in_progress` | `passed` | `failed`;
  - transition validator `not_started → in_progress → passed|failed`, `failed → in_progress`,
    `passed → in_progress` only for a rerun.
- [X] T024 Implement `POST/GET /sessions` and `GET /sessions/{id}` in `onboarding/api/src/onboarding_api/sessions/routes.py`:
  - only `iam_engineer` creates;
  - `connector_type` must be `available` (else 409 `connector_planned`);
  - tenant must be `usable` (else 409 `tenant_unusable`);
  - details validated by T018;
  - participants: exactly one IAM engineer, at most one application owner;
  - 404 for non-participants.
- [X] T025 Implement the message repository in `onboarding/api/src/onboarding_api/chat/messages.py`:
  - `seq` strictly increasing per session (atomic counter on the session);
  - `text` stored **after masking**, max 8,000 chars;
  - `speaker` ∈ `iam_engineer` | `application_owner` | `agent`;
  - `addressed_to` ∈ `iam_engineer` | `application_owner` | `both` (agent only);
  - `queue_state` ∈ `queued` | `processing` | `answered`.
- [X] T026 Implement the event journal and broadcaster in `onboarding/api/src/onboarding_api/chat/events.py`:
  - write to `events` with a per-session increasing `event_id`, then publish in-process;
  - `visible_to` = `both` or a user id.
- [X] T027 Implement `GET /sessions/{id}/events` as SSE in `onboarding/api/src/onboarding_api/chat/stream.py`:
  - replay after `Last-Event-ID`, filtered by `visible_to`;
  - `: keepalive` every 15 s;
  - event names exactly as in `contracts/live-events.md`.
- [X] T028 Implement the AgentCore client in `onboarding/api/src/onboarding_api/agent_client/client.py`:
  - SigV4 `InvokeAgentRuntime` with `runtimeSessionId = onb-<session_id>`;
  - builds the request from `contracts/agent-invocation.md` (last 40 masked messages + summary line; images only
    for `passed` attachments);
  - yields parsed stream events.
- [X] T029 Implement the per-session turn worker in `onboarding/api/src/onboarding_api/chat/turns.py`:
  - acquire `sessions.turn_lock` atomically, take the lowest-`seq` `queued` message, set `processing`;
  - invoke T028 and map each stream event per the `contracts/agent-invocation.md` table: masking every text, writing
    `messages`, `steps`, `actions` (adding `ordered_by` from the request) and `sessions.source`;
  - set `answered`; release the lock; loop.
  - On error, emit `turn.failed`, re-queue once, then answer with an error reply.
- [X] T030 Implement `POST/GET /sessions/{id}/messages` in `onboarding/api/src/onboarding_api/chat/routes.py`: mask, store as `queued`, emit `message.created` + `message.queue`, kick the turn worker, return 202 (FR-006, FR-006a)
- [X] T031 Wire the FastAPI app in `onboarding/api/src/onboarding_api/main.py` (routers under `/onboarding/api`, logging filter, Mongo startup, `/healthz`)

### Agent skeleton (AgentCore runtime)

- [X] T032 [P] Implement the ISC REST client in `onboarding/agent/src/onboarding_agent/isc/client.py`:
  - base URL `https://<api_host>`;
  - access token from AgentCore Identity via the M2M flow on `credential_provider` (research R4), cached until expiry;
  - `X-SailPoint-Experimental` header support;
  - errors returned as text with secrets stripped.
- [X] T033 [P] Implement the playbook loader in `onboarding/agent/src/onboarding_agent/playbooks.py` (reads `catalog.yaml` and `playbooks/<id>/{setup.md,settings.yaml,checks.yaml,failures.md,collisions.md}` baked into the image; renders `{placeholders}` from session details and tenant values)
- [X] T034 Implement the runtime entrypoint in `onboarding/agent/src/onboarding_agent/main.py`: `BedrockAgentCoreApp`, request validation per `contracts/agent-invocation.md`, streaming one JSON event per chunk (`delta`, `progress`, `set_step`, `action`, `source`, `application_step`, `final`, `secret_check`, `error`)
- [X] T035 Implement the Claude Haiku 4.5 tool loop in `onboarding/agent/src/onboarding_agent/loop.py`:
  - Bedrock inference profile from env; images as image blocks;
  - at most 8 tool rounds per turn;
  - streams `progress` before each tool call and text deltas as they arrive;
  - the system prompt is assembled from `onboarding/agent/src/onboarding_agent/prompts/en/system.md` plus the
    session's playbook.
- [X] T036 Implement the session tools in `onboarding/agent/src/onboarding_agent/tools/session.py` (`set_step`, `address`, `record_application_step`), each emitting its stream event
- [X] T037 Write `onboarding/agent/src/onboarding_agent/prompts/en/system.md`:
  - the two actors and how to address each;
  - the agent never acts on the application and never requests credentials (FR-010);
  - only the IAM engineer's order approves SailPoint changes (FR-016);
  - mark every instruction read-only or change (FR-011);
  - stay inside the session's connector type and onboarding (FR-019).

### Cluster deployment

- [X] T038 [P] Write `onboarding/deploy/k8s/mongodb.yaml`: namespace `onboarding`; StatefulSet `mongodb` (`mongo:8.0`, `--replSet rs0`, init job running `rs.initiate`); 5 Gi PVC with **no storageClassName** (k3s and Docker Desktop, research R11); ClusterIP service
- [X] T039 [P] Write `onboarding/deploy/k8s/api.yaml` (Deployment `onboarding-api` with 1 replica, env from ConfigMap + Secret `onboarding-aws` holding the `onboarding-api` IAM user keys, readiness `/onboarding/api/healthz`) and `onboarding/deploy/k8s/web.yaml` (Deployment `onboarding-web`, nginx serving the Angular build under `/onboarding/`, proxying `/onboarding/api/` to the API with SSE buffering off)
- [X] T040 [P] Write `onboarding/deploy/k8s/netpol.yaml`: MongoDB ingress only from `app=onboarding-api`; API ingress only from `app=onboarding-web` and the edge
- [X] T041 [P] Write `onboarding/api/Dockerfile`, `onboarding/web/Dockerfile` (build + nginx) and `onboarding/deploy/scripts/images.sh` (build and push `localhost:5000/onboarding-{api,web}`, following `scripts/03-build-docker.sh`)
- [X] T042 Write `onboarding/deploy/scripts/agent-deploy.sh` (pattern: `scripts/agent-deploy.sh`):
  - deploy runtime `isc_onboarding_agent` in ap-southeast-1 with Bedrock invoke rights and AgentCore Identity
    token rights;
  - create IAM user `onboarding-api` allowed only `bedrock-agentcore:InvokeAgentRuntime` on that runtime ARN plus
    credential-provider create/update/delete on `onboarding-isc-*`;
  - write the keys straight into Kubernetes Secret `onboarding-aws`, never echoed;
  - set log retention to 1 day;
  - `--delete` removes all of it.
- [X] T043 Write `onboarding/deploy/scripts/up.sh`, `down.sh` and `bootstrap.sh` (apply manifests, wait for Ready, run T016 as a one-off Job from a one-time Secret then delete the Secret) and add a `location /onboarding/` block to `deployment/edge-nginx.conf` forwarding to the web service (prefix kept; same SSE settings as `/mcp/gw`)

**Checkpoint**: an admin can sign in, register a tenant (credential in AgentCore Identity), and create a session; a
message reaches the agent and a streamed reply comes back on the live stream.

---

## Phase 3: User Story 1 - IAM engineer has the agent create a working source (Priority: P1) 🎯 MVP

**Goal**: An IAM engineer orders "create the connector", and the agent creates, configures and checks an AWS SaaS
source, then aggregates it and runs Test Connection, reporting every result and recording every change (FR-016–FR-018,
FR-020).

**Independent Test**: With an AWS role already prepared outside the system, sign in as an IAM engineer, start an AWS
SaaS session and order creation. End with `connection_check`, `aggregation` and `test_connection` passed, and five
action records naming the engineer (quickstart §3 step 5).

### Tests for User Story 1

- [X] T044 [P] [US1] Write ISC tool tests with respx in `onboarding/agent/tests/unit/test_isc_tools.py`, with response shapes from `.claude/skills/sailpoint-isc-aws-connector/references/isc-api.md`:
  - `create_source` sends the creation-only fields;
  - an existing same-name source owned by someone else returns `owned_by` with no write (SC-007);
  - every write emits exactly one `action` event.
- [X] T045 [P] [US1] Write the role-gate test in `onboarding/agent/tests/unit/test_role_gate.py`: with `ordered_by.role = application_owner`, the write tools are not offered and no `action` event is produced (FR-016, FR-019)

### Implementation for User Story 1

- [X] T046 [P] [US1] Port `onboarding/catalog/playbooks/aws-saas/settings.yaml` from `.claude/skills/sailpoint-isc-aws-connector/scripts/isc-source.sh` (`create` + `configure`) and `references/isc-api.md`:
  - spec id `6e47875b-73f1-481d-a613-59d4deca0c6a`;
  - creation-only fields `spConnectorSpecId`, `idnProxyType: sp-connect`, `spConnectorSupportsCustomSchemas: true`
    and the UI defaults;
  - `field_map` for `roleName`, `managementAccountId`, `region`, `externalId`, `cloudScope`, change-password policy
    ARN (default `arn:aws:iam::aws:policy/IAMUserChangePassword`), Bedrock / AgentCore discovery toggles and regions.
- [X] T047 [P] [US1] Write `onboarding/catalog/playbooks/aws-saas/checks.yaml`: `peek_accounts` → `connection_check`; account + entitlement aggregation → `aggregation`; test connection → `test_connection`; Test Connection runs after the first successful aggregation (skill note)
- [X] T048 [US1] Implement the generic ISC source tools in `onboarding/agent/src/onboarding_agent/isc/tools.py`:
  - `get_tenant_external_id`, `get_connector_form`, `find_source`, `create_source` (always `find_source` first;
    collision rule FR-018), `configure_source`, `peek_accounts`, `start_aggregation`, `get_task`,
    `test_connection`, `delete_session_source` (only `session.source.id`);
  - all driven by `settings.yaml` / `checks.yaml`;
  - each write emits one `action` event and updates steps through `set_step`.
- [X] T049 [US1] Add the role gate to `onboarding/agent/src/onboarding_agent/loop.py`: write tools are offered only when `ordered_by.role == "iam_engineer"`; missing required session details make the agent ask before any write (US1 scenario 4)
- [X] T050 [US1] Implement `GET /sessions/{id}/actions` in `onboarding/api/src/onboarding_api/audit/routes.py` (returns `Action` with the `ordered_by` display name; FR-020)
- [X] T051 [P] [US1] Build the session-creation flow in `onboarding/web/src/app/session/new-session.component.ts`: tenant picker (usable tenants only), connector type preselected, a form generated from `session_fields`, invite application owner
- [X] T052 [US1] Build the IAM engineer screen in `onboarding/web/src/app/session/iam-screen.component.ts`, following the design canvas "Main" artboard:
  - header with session title and connector type chip;
  - status chips bound to `steps`;
  - source details panel;
  - SailPoint actions panel from `/actions` + `action.recorded` events;
  - chat with composer.
- [X] T053 [US1] Implement the live-stream client in `onboarding/web/src/app/shared/live-stream.service.ts` (EventSource on `/onboarding/api/sessions/{id}/events`, typed events per `contracts/live-events.md`, merging `agent.delta` into the streaming message and replacing it on `agent.message`)

**Checkpoint**: US1 works on its own against a prepared AWS role; this is the MVP.

---

## Phase 4: User Story 2 - Application owner prepares the application from the agent's instructions (Priority: P1)

**Goal**: The application owner gets the connector type's setup steps with every known value filled in, marked
read-only or change. The agent checks pasted output and never acts on the application (FR-010–FR-015).

**Independent Test**: Sign in as an application owner and follow only the agent's instructions until the agent
confirms the AWS role (right trust, External ID, policies) and `application_ready` passes (quickstart §3 steps 3–4).

### Implementation for User Story 2

- [X] T054 [P] [US2] Port `onboarding/catalog/playbooks/aws-saas/setup.md` from `.claude/skills/sailpoint-isc-aws-connector/scripts/aws-setup.sh` and `references/aws-permissions.md`:
  - ordered steps: preflight (Organizations all-features, StackSets trusted access), name-collision check,
    CloudFormation stack + service-managed StackSet, trust with `ciem_universal` principals 874540850173 and
    706944607044 + External ID, `SPAggregationPolicy`, `SPOrganizationPolicy`, optional
    `SPBedrockAgentDiscoveryPolicy` / `SPBedrockAgentCoreDiscoveryPolicy`, CloudTrail confirmation;
  - each step has `read_only`, command templates with `{placeholders}` and its expected output.
- [X] T055 [P] [US2] Write `onboarding/catalog/playbooks/aws-saas/collisions.md` (role or stack name taken, or trust naming another External ID → propose `SailPointISCRole-<tenant>`; one role per tenant when tenants share an account; FR-014, FR-015)
- [X] T056 [US2] Extend `onboarding/agent/src/onboarding_agent/playbooks.py` to render setup steps with no leftover placeholder for any value the session or tenant knows (unknown values are named explicitly; SC-002), and to emit `application_step` events with `read_only`
- [X] T057 [US2] Add output checking to `onboarding/agent/src/onboarding_agent/prompts/en/system.md`: compare pasted output with each step's expected output, set `application_ready` with `set_step` once the role is confirmed, never propose changing an item not created for this onboarding, and say provisioning is out of scope unless the IAM engineer asks (enabling it in SailPoint cannot be undone; FR-012, FR-013)
- [X] T058 [US2] Build the application owner screen in `onboarding/web/src/app/session/owner-screen.component.ts`, following the design canvas "Cloud" artboard:
  - "Your steps in <application>" list from `application_step` data with read-only/change tags and the current
    step highlighted;
  - session values panel;
  - chat with a Copy button on code blocks (clipboard with select-text fallback).

**Checkpoint**: US1 + US2 together give a complete onboarding.

---

## Phase 5: User Story 3 - Both participants see one live, shared conversation (Priority: P2)

**Goal**: Each participant has their own screen, sees the other side live with speaker and addressee labels, sees
queued state, and gets the full history on rejoin (FR-006, FR-006a, FR-006b, FR-008).

**Independent Test**: Two browsers as the two roles. A message from one appears on the other labelled within 2 s.
Two near-simultaneous messages show the second as queued. A reloaded screen shows the full history (quickstart §3
steps 2, 3, 6).

### Implementation for User Story 3

- [X] T059 [US3] Track presence in `onboarding/api/src/onboarding_api/chat/stream.py` (open SSE streams per role → `participant.presence` events) and expose `online` in `GET /sessions/{id}`
- [X] T060 [US3] (superseded by T101) Render speaker and addressee labels in `onboarding/web/src/app/shared/message-list.component.ts`:
  - "You · <role>", "<Other role> · <name>", "Agent → IAM engineer", "Agent → Application owner", "Agent → You";
  - agent cards addressed to the other participant are marked visibly (blue card style from the canvas);
  - queued / processing badges from `message.queue`.
- [X] T061 [US3] Add the streaming progress line and first-token behaviour to `onboarding/web/src/app/shared/message-list.component.ts` (`agent.progress` text with a pulsing dot; cleared on `null`; FR-006b)
- [X] T062 [US3] Implement route guards in `onboarding/web/src/app/auth/role.guard.ts` so each role reaches only its own screen (the API enforces the same through T014; FR-003), and redirect after sign-in by role
- [X] T063 [US3] Implement history loading and reconnect in `onboarding/web/src/app/shared/live-stream.service.ts` (load `GET /messages` first, then open the stream with `Last-Event-ID`; exponential back-off reconnect)
- [X] T064 [P] [US3] (superseded by T104) Write the two-browser Playwright test in `onboarding/web/e2e/shared-session.spec.ts` (relay ≤ 2 s, queued state, rejoin history, the application owner blocked from the IAM screen; SC-003)

**Checkpoint**: The shared conversation works for both roles.

---

## Phase 6: User Story 4 - Troubleshoot errors together from text and screenshots (Priority: P2)

**Goal**: Failures are diagnosed from pasted text or screenshots. SailPoint-side fixes are applied by the agent;
application-side fixes go to the application owner as confirm-then-fix steps. Screenshots with secrets are held
(FR-007, FR-021–FR-024, FR-026a).

**Independent Test**: Reproduce each AWS SaaS failure from `failures.md`, submit it as text and as a screenshot, and
check that the agent names the cause and side and that its fix makes the step pass (quickstart §4).

### Tests for User Story 4

- [X] T065 [P] [US4] Build the diagnosis eval set in `onboarding/agent/tests/evals/aws_saas_failures/`:
  - one case per failure in T066, as error text and as a screenshot PNG;
  - the expected `side` and cause keyword;
  - runner `onboarding/agent/tests/evals/run_evals.py`: 10 runs per case, pass when ≥ 9/10 first replies name the
    cause and side (SC-005); wired to `make onboarding-evals`.

### Implementation for User Story 4

- [X] T066 [P] [US4] Port `onboarding/catalog/playbooks/aws-saas/failures.md` from `.claude/skills/sailpoint-isc-aws-connector/references/troubleshooting.md`. Each entry has its signature, side, cause, read-only confirm step, fix, and retry-once flag. Entries:
  - trust missing External ID;
  - wrong `ciem_universal` principal (production vs demo);
  - `no schema provided`;
  - `req.input is null`;
  - stale connector config clears on retry;
  - AgentCore/Bedrock discovery `Permission error` naming `ListGateways`, `GetResourcePolicy`,
    `ListTagsForResource`;
  - missing change-password policy;
  - role-name collision.
- [X] T067 [US4] Implement attachment upload in `onboarding/api/src/onboarding_api/chat/attachments.py` (`POST /sessions/{id}/attachments`: `image/png|jpeg|webp` ≤ 10 MB else 413; keep bytes in memory, `secret_check: pending`; run the agent in `secret_check` mode; on `passed` write GridFS and emit `attachment.checked` to both; on `held` keep the bytes for the uploader only ≤ 10 minutes and emit `attachment.held` to the uploader only; `GET`/`DELETE` per contract)
- [X] T068 [US4] Implement `secret_check` mode in `onboarding/agent/src/onboarding_agent/main.py` (vision-only request with a fixed prompt, returns `passed` / `held` + reason, never stores the image)
- [X] T069 [US4] Add the troubleshooting rules to `onboarding/agent/src/onboarding_agent/prompts/en/system.md`, using the session playbook's `failures.md`:
  - name the side and quote the error relied on;
  - apply SailPoint-side fixes and rerun;
  - retry a known transient failure once;
  - give the application owner the confirm step before the fix, or ask for a specific console screenshot.
- [X] T070 [US4] Add screenshot upload with a held-state preview ("held, please upload a redacted version", Replace / Dismiss) to `onboarding/web/src/app/shared/composer.component.ts`

**Checkpoint**: Failures are fixed together across both screens.

---

## Phase 7: User Story 5 - Browse the connector catalog (Priority: P3)

**Goal**: Any signed-in user can see the connector types, available or planned. An IAM engineer starts a session from
an available type (FR-028–FR-030).

**Independent Test**: Open the catalog as each role. AWS SaaS shows as available with "Start a session" (IAM engineer
only). Planned types show without a start action, and creating a session with one returns 409 `connector_planned`.

### Implementation for User Story 5

- [X] T071 [US5] Build the catalog page in `onboarding/web/src/app/catalog/catalog.component.ts`, following the design canvas "Catalog" artboard:
  - available card with owner-asks / agent-configures;
  - planned table;
  - note that the list ships with each product version;
  - "Start a session" only for IAM engineers on available types → T051.
- [X] T072 [US5] Show the connector type's `owner_label` and `first_step_label` (e.g. "AWS owner", "AWS role ready") in the session screens from the catalog entry (`onboarding/web/src/app/session/session-header.component.ts`), so no screen hard-codes AWS wording (SC-009)

**Checkpoint**: The catalog makes the product's scope visible.

---

## Phase 8: User Story 6 - Sign in and session history (Priority: P3)

**Goal**: Local sign-in per role, admin account management, session lists, finished-session history with action
records (FR-001–FR-004, FR-009, US6).

**Independent Test**: Sign in as each role. Each sees only their own sessions. Open a finished session to find the
full conversation, screenshots, status history and action records. Five wrong passwords lock the account for
15 minutes.

### Implementation for User Story 6

- [X] T073 [US6] Implement `/admin/users` and `/admin/users/{id}` in `onboarding/api/src/onboarding_api/auth/admin_routes.py`:
  - create, disable/enable, unlock, reset password, change role or admin flag;
  - `is_admin` only for `iam_engineer` (422 otherwise);
  - initial password minimum 12 characters;
  - audit `user_created`, `user_disabled`, `password_reset`, `role_changed`.
- [X] T074 [US6] Implement `POST /sessions/{id}/finish` in `onboarding/api/src/onboarding_api/sessions/routes.py` (IAM engineer only; sets `finished_at`, `expires_at = finished_at + 90 days` on the session and on its `messages`, `events` and `attachments`; research R10)
- [X] T075 [P] [US6] Build the login page in `onboarding/web/src/app/auth/login.component.ts`, following the design canvas "Login" artboard (local account form, attempts-left and locked messages from 401/423, idle sign-out handling)
- [X] T076 [P] [US6] Build the session list in `onboarding/web/src/app/session/session-list.component.ts` (open and finished sessions the user takes part in; finished ones open read-only with history, screenshots, status history and actions)
- [X] T077 [US6] Build the admin page in `onboarding/web/src/app/admin/admin.component.ts`, following the design canvas "Admin" artboard:
  - accounts table with role, admin flag, status, lock countdown and actions;
  - tenants panel with status, External ID, credential hint, "Replace credential" and "Check now";
  - credential fields are write-only.

**Checkpoint**: All six user stories work independently.

---

## Phase 9: Polish & Cross-Cutting Concerns

- [X] T078 [P] Externalize all UI text with `$localize` message IDs across `onboarding/web/src/app/**` and add the empty `onboarding/web/src/locale/messages.th.xlf` placeholder (research R12)
- [X] T079 [P] Add a leak scan `onboarding/deploy/scripts/leak-scan.sh` (Mongo queries for `AKIA[0-9A-Z]{16}`, `eyJ[A-Za-z0-9_-]{10,}`, 64-hex strings in `messages`, `events` and `actions`; grep of API and agent logs; non-zero exit on any hit; SC-004)
- [X] T080 [P] Add API metrics in `onboarding/api/src/onboarding_api/metrics.py` (time to first agent delta per turn, relay latency from message stored to event delivered, ISC tool error counts) and log them per `turn_id` (SC-003, SC-003a)
- [X] T081 Write the ISC stub service `onboarding/deploy/stub-isc/` (replays recorded responses for the AWS SaaS tool calls, including each failure in T066) and point `ONBOARDING_ISC_BASE_URL` at it for `make onboarding-e2e`
- [X] T082 Write the full end-to-end Playwright run `onboarding/web/e2e/onboarding.spec.ts` covering quickstart §3–§5 against the stub, two browser contexts, and the guardrail table rows (SC-001, SC-004, SC-006, SC-007)
- [X] T083 [P] Add the onboarding section to `README.md` and a deploy/run section to `docs/DEMO-GUIDE.md` (local cluster + AgentCore split, make targets, teardown)
- [ ] T084 Run every step of `specs/001-isc-onboarding-agent/quickstart.md` against a real demo tenant and record results in `specs/001-isc-onboarding-agent/checklists/quickstart-run.md`

---

## Revision 2026-10-07: one thread per person and suggested replies

The spec revision (US3 rewritten, US7 added; FR-006–FR-006e, FR-016a, FR-019, SC-003, SC-010, SC-011) and plan
research R15–R18 change parts that Phases 1–9 already built. Tasks T085–T116 deliver that change. T060 (addressee
labels) and T064 (shared-session e2e) are superseded by T101 and T104; their code is replaced, not extended.

---

## Phase 10: Revision foundation (blocks Phases 11 and 12)

**Purpose**: Thread-aware data, migration and turn payload, so both revised stories can build on them.

- [X] T085 Add thread fields to messages in `onboarding/api/src/onboarding_api/chat/messages.py`:
  - `add()` takes `thread` (`"iam_engineer"` | `"application_owner"`) and `kind` (`"message"` | `"relay_note"`, default
    `"message"`), plus optional `relay_ref` (ObjectId → messages) and `relayed_from` (`"iam_engineer"` |
    `"application_owner"`);
  - a participant message's `thread` is always the speaker's role ("a participant only ever speaks in their own
    thread"); reject anything else with ValueError;
  - stop writing `addressed_to` ("retired"); `public()` returns `thread`, `kind`, `relay_ref`, `relayed_from`.
- [X] T086 Add the one-off migration (research R18) and the new index in `onboarding/api/src/onboarding_api/db.py`
  `ensure_indexes()`:
  - index `{session_id: 1, thread: 1, seq: 1}`;
  - for messages without `thread`: participant message → speaker's thread; agent message with `addressed_to` of a
    participant → that thread; `addressed_to: "both"` or none → the thread of the participant message with the same
    `turn_id`; set `kind: "message"`; idempotent (only touches documents missing `thread`).
- [X] T087 Add session fields in `onboarding/api/src/onboarding_api/sessions/repo.py` and expose them in
  `onboarding/api/src/onboarding_api/sessions/routes.py` GET:
  - `waiting_on`: `"iam_engineer"` | `"application_owner"` | null ("information only, never blocks the queue");
  - `check_order`: `{user_id, display_name, turn_id, at}` or null;
  - `suggestions`: `{iam_engineer: [], application_owner: [], for_event_id: 0}` on create;
  - helpers `set_waiting()`, `set_check_order()`, `clear_check_order()`, `set_suggestions(thread, items, event_id)`.
- [X] T088 [P] Add `trigger` to action records in `onboarding/api/src/onboarding_api/audit/actions.py`:
  `"order"` | `"application_owner_confirmation"`, default `"order"`; `ordered_by` stays required.
- [X] T089 Rebuild the agent request in `onboarding/api/src/onboarding_api/chat/turns.py` `_history()` and the payload
  builder: history entries carry `thread` and `kind` from **both** threads in queue order (last 40); `message` carries
  `thread`; add `waiting_on`, `check_order` (or null) and `suggestion_defaults` per contracts/agent-invocation.md.
- [X] T090 [P] Write the migration test in `onboarding/api/tests/unit/test_thread_migration.py`: seed old-shape
  messages (participant, agent `addressed_to` participant, agent `addressed_to: both` with `turn_id`), run
  `ensure_indexes()` twice, assert threads and kinds and that the second run changes nothing.

**Checkpoint**: old sessions load with every message in a thread; the existing test suites still pass.

---

## Phase 11: User Story 3 (revised) - Each participant has their own thread and sees the other's live (Priority: P2)

**Goal**: Two threads per session, own thread writable and other view only, the agent reaching the other person
only through thread tools with a relay note, a waiting line that never blocks, and check reruns on the owner's
confirmation under the IAM engineer's standing order (FR-006–FR-006d, FR-016a, FR-019, SC-010).

**Independent Test**: quickstart §3a steps 1–5 on two browsers: messages land in the sender's thread on both screens
within 2 s, the agent's request to the owner appears in the owner's thread with a relay note in the IAM engineer's,
"View only" has no message box, the IAM engineer is answered while the agent waits on the owner, and the owner's
"done" reruns the checks with action records naming the IAM engineer.

### Tests for User Story 3 (revised)

- [X] T091 [P] [US3] Write thread-tool tests in `onboarding/agent/tests/unit/test_thread_tools.py`:
  `post_to_other_thread(text, relay_note)` emits one `other_thread` event with both fields; `notify_other_thread`
  emits only `relay_note`; `set_waiting` accepts only `iam_engineer` | `application_owner` | null; streamed `delta`
  and `final` events carry no thread or addressee field.
- [X] T092 [P] [US3] Extend `onboarding/agent/tests/unit/test_role_gate.py`: an application owner turn is offered no
  write tools without `check_order`; with `check_order` and a session source it is offered exactly `peek_accounts`,
  `start_aggregation`, `test_connection` and never `create_source`, `configure_source`, `delete_session_source`.
- [X] T093 [P] [US3] Write API integration tests in `onboarding/api/tests/integration/test_threads.py` (FakeAgent in
  `onboarding/api/tests/conftest.py`):
  - POST as either role lands in the caller's thread; there is no parameter to choose another thread;
  - an `other_thread` event creates an agent message in the other thread and a `relay_note` in the writer's, both
    emitted as `message.created`; an `other_thread` without `relay_note` gets a generic relay note (pairing rule);
  - `waiting_on` set by the agent does not hold the next message from the other participant;
  - an IAM engineer turn that runs a check sets `check_order`; an owner turn's check actions are stored with
    `ordered_by` = that IAM engineer and `trigger = "application_owner_confirmation"`; `check_order` clears when all
    checks pass.

### Implementation for User Story 3 (revised)

- [X] T094 [US3] Replace `address()` with thread tools in `onboarding/agent/src/onboarding_agent/tools/session.py`:
  `post_to_other_thread(text: str | None, relay_note: str, relayed_from: str | None)`,
  `notify_other_thread(relay_note: str)`, `set_waiting(on: str | None)`; they emit `other_thread` and `waiting` events
  per contracts/agent-invocation.md; remove `addressed_to`.
- [X] T095 [US3] Update `onboarding/agent/src/onboarding_agent/loop.py`: `TOOL_SPECS` entries for the thread tools;
  `offered_tools(role, check_order, has_source)` implements the FR-016a gate from T092; `delta` and `final` events
  drop `addressed_to`.
- [X] T096 [US3] Rewrite the reply rules in `onboarding/agent/src/onboarding_agent/prompts/en/system.md`: you are
  answering in the writer's thread; reach the other person only with `post_to_other_thread` (always with a one-line
  `relay_note`); news for both goes in full to the thread it belongs to (SailPoint results: the IAM engineer's) plus
  `notify_other_thread`; call `set_waiting` when you need the other person; on an application owner's confirmation
  under a standing check order, rerun only the checks and report in the IAM engineer's thread; relaying "tell the
  other person" uses `relayed_from`.
- [X] T097 [US3] Pass `check_order` through `onboarding/agent/src/onboarding_agent/main.py` to `loop.run_turn()` and
  mark check actions from an owner turn with `trigger: "application_owner_confirmation"` in
  `onboarding/agent/src/onboarding_agent/isc/tools.py`.
- [X] T098 [US3] Handle the new turn output in `onboarding/api/src/onboarding_api/chat/turns.py`: reply and deltas go to
  the writer's thread; `other_thread` → agent message in the other thread (masked) + relay note in the writer's
  thread with `relay_ref`; enforce the pairing rule; `waiting` → `set_waiting()` + `thread.waiting` event; set
  `check_order` on an IAM engineer turn that runs a check, clear it on a new IAM engineer order or when all checks
  pass; write owner-turn actions with `ordered_by = check_order` and `trigger`.
- [X] T099 [US3] Update live events in `onboarding/api/src/onboarding_api/chat/events.py` callers: `agent.delta`
  payload `{turn_id, message_id, thread, text_delta}`; new `thread.waiting {waiting_on}` (both); keep the LF framing
  and `: connected` first frame in `onboarding/api/src/onboarding_api/chat/stream.py`.
- [X] T100 [US3] Update `onboarding/web/src/app/shared/models.ts` (`Message.thread`, `kind`, `relay_ref`,
  `relayed_from`; `Session.waiting_on`) and `onboarding/web/src/app/shared/live-stream.service.ts` (`waiting` signal
  from `thread.waiting`; `threadMessages(thread)` computed; delta drafts keyed by thread; resync keeps working).
- [X] T101 [US3] Create `onboarding/web/src/app/shared/thread.component.ts` (replaces the labelling in
  `message-list.component.ts`), following the design canvas threads: header with initials, "You ↔ Agent" or
  "<Role> ↔ Agent", name and role, a **View only** badge for the other thread; messages with speaker and time; relay
  notes as a one-line note row; progress line; footer line "Waiting for <name>…" and, on the view-only thread, "You
  can read this thread but not post in it. Ask the agent in your thread to relay a message."
- [X] T102 [US3] Rework `onboarding/web/src/app/session/session-screen.component.ts` and `.css` to the canvas
  layout: header (connector type, status chips, other participant online); left role panel; main
  "Conversations" section with the subtitle for the role and two threads side by side (own thread with
  `app-composer`, other thread view only, no composer); stacks vertically below 1100 px.
- [X] T103 [US3] Align the role panels in `onboarding/web/src/app/session/session-screen.component.ts` with the
  canvas (FR-006d): IAM engineer: "Source" details list and "SailPoint actions" with "ordered by" and time;
  application owner: "Your steps in <application>" (each step numbered, tagged read-only / change, state pending /
  current / done / failed) and "Values the agent fills in for you".
- [X] T104 [US3] Replace `onboarding/web/e2e/shared-session.spec.ts` scenarios with the thread model and add
  `onboarding/web/e2e/threads.spec.ts` for quickstart §3a steps 1–5 against the ISC stub (two browser contexts;
  relay note present; no composer in the view-only thread; IAM answered while waiting; owner "done" reruns checks).

**Checkpoint**: quickstart §3a steps 1–5 pass; SC-010 holds in the e2e run.

---

## Phase 12: User Story 7 - Pick a suggested question or answer instead of typing (Priority: P2)

**Goal**: 3–5 suggestions in each own thread, fitted to role and state, that fill the message box when picked
(FR-006e, SC-011).

**Independent Test**: quickstart §3a step 6: at each state the own thread shows 3–5 fitting suggestions within 1 s of
the reply finishing, the owner never sees an order, picking only fills the box, and a full run is possible with
suggestions plus pasted output.

### Tests for User Story 7

- [X] T105 [P] [US7] Write `onboarding/api/tests/unit/test_suggestions.py` for the merge rules: agent items first;
  `kind: order` dropped for the application owner; items the masker changes are dropped; texts over 200 chars
  dropped; duplicates removed; topped up from defaults to at least 3; capped at 5; state detection (`no_source`,
  `waiting_for_owner_output`, `check_failed`, `all_passed`, `any`).

### Implementation for User Story 7

- [X] T106 [P] [US7] Write `onboarding/catalog/playbooks/aws-saas/suggestions.yaml`: per role and state, items
  `{text, kind}`; at least 3 per role under `any` ("What is left to do?", "Explain the last message", "Show me the
  current status"); IAM engineer `no_source`: "Create the connector and run the checks"; `check_failed`: "Why did the
  connection check fail?", "Rerun the connection check"; owner after a command request: "Here is the output:", "I get
  an error at this step", "Explain what this step does"; no `kind: order` for the application owner.
- [X] T107 [P] [US7] Load and validate `suggestions.yaml` in `onboarding/api/src/onboarding_api/catalog/catalog.py`
  (fail fast on a missing `any` list with fewer than 3 items per role or an owner `order`), and add the
  `suggestions.yaml` row check to the playbook loader in `onboarding/agent/src/onboarding_agent/playbooks.py` so the
  agent can quote defaults.
- [X] T108 [US7] Implement `onboarding/api/src/onboarding_api/chat/suggestions.py`: `current_state(session)`,
  `merge(thread, agent_items, defaults)` with the T105 rules (`text` ≤ 200 chars, `kind` ∈ `answer` | `order` |
  `question`, `source` ∈ `agent` | `default`).
- [X] T109 [US7] Add `suggest_replies(thread, items)` to `onboarding/agent/src/onboarding_agent/tools/session.py` and
  `TOOL_SPECS` in `onboarding/agent/src/onboarding_agent/loop.py` (emits a `suggestions` event), and one prompt rule
  in `onboarding/agent/src/onboarding_agent/prompts/en/system.md`: end each turn with 3 short replies for the writer's
  thread (and for the other thread if you asked them something), never containing secrets or SailPoint orders for the
  application owner.
- [X] T110 [US7] Wire suggestions in `onboarding/api/src/onboarding_api/chat/turns.py` and
  `onboarding/api/src/onboarding_api/chat/routes.py`: after every turn (and every `step.changed` outside a turn)
  merge agent items with defaults for both threads, `set_suggestions()`, emit `suggestions.updated` with `visible_to`
  = that thread's participant; add `GET /sessions/{id}/suggestions` (caller's thread only, 3–5 items) per the
  OpenAPI contract.
- [X] T111 [US7] Add suggestion chips to `onboarding/web/src/app/shared/composer.component.ts`: 3–5 `<button>`s under
  the message box from `suggestions.updated` (initial load from `GET /suggestions`); picking sets the box text (a
  text ending in ":" leaves the caret after it) and focuses the box; never sends; keyboard reachable; not rendered in
  the view-only thread.
- [X] T112 [US7] Write `onboarding/web/e2e/suggestions.spec.ts` for quickstart §3a step 6 against the ISC stub:
  3–5 chips per own thread after each reply; none in the view-only thread; the owner list has no order; picking fills
  but does not send; the happy path completes using chips plus pasted output only.

**Checkpoint**: quickstart §3a step 6 passes; SC-011 measured in the e2e run (chips within 1 s of `agent.message`).

---

## Phase 13: Revision polish

- [X] T113 [P] Update `onboarding/deploy/scripts/smoke.py` to the thread model: read replies by `thread`, assert the
  relay note after the owner's request, and add a `--scenario confirm` path for the owner-confirmation rerun.
- [X] T114 [P] Update `onboarding/agent/tests/evals/run_evals.py` for the new `SessionTools` (no `addressed_to`) and
  re-run `make onboarding-evals` (SC-005 must still hold at ≥ 9/10).
- [X] T115 [P] Update the demo script in `docs/DEMO-GUIDE.md` §8.3 and `onboarding/README.md` for threads, relay
  notes and suggestions.
- [X] T116 Rebuild and roll out (`make onboarding-images`, restart `onboarding-api` and `onboarding-web`), redeploy
  the agent code, run quickstart §3a against the cluster through the public URL, then `make onboarding-leak-scan`
  (check free disk space first: rebuilds need several GB).

---

## Revision 2026-10-07 (2): scrolling threads and waiting banner

The spec revision (FR-006f, FR-006g, US3 scenarios 4 and 7–9, SC-012, SC-013) and plan research R19–R20 change the
thread view and the waiting signal built in T094, T098 and T101. Tasks T117–T133 deliver it. The thread's old waiting
foot line (T101) is replaced by the banner; the view-only hint stays.

---

## Phase 14: Revision 2 foundation (blocks Phase 15)

**Purpose**: carry a reason with every wait, end to end, and seed long threads for testing.

- [X] T117 Add `waiting_reason` to sessions in `onboarding/api/src/onboarding_api/sessions/repo.py`: new sessions get
  `waiting_reason: None`; `set_waiting(session_id, on, reason=None)` stores both, sets `waiting_reason` to null
  whenever `on` is null, and enforces the data-model rule "one line, ≤ 120 chars, masked; null with `waiting_on`"
  (collapse whitespace/newlines to single spaces, run the masker, cut to 120 characters).
- [X] T118 [P] Return `waiting_on` and `waiting_reason` from the session GET in
  `onboarding/api/src/onboarding_api/sessions/routes.py` `full()` (contracts/session-api.openapi.yaml `Session`).
- [X] T119 Handle the reason in `onboarding/api/src/onboarding_api/chat/turns.py`: the `waiting` output reads
  `on` and `reason`, calls `repo.set_waiting(session_id, on, reason)` and emits `thread.waiting`
  `{waiting_on, reason}` (contracts/live-events.md); the agent payload adds `waiting_reason`
  (contracts/agent-invocation.md). Auto-clear (research R20): when a turn written by the awaited participant finishes
  without a `waiting` output, call `set_waiting(session_id, None)` and emit `thread.waiting {waiting_on: null,
  reason: null}` before `message.queue(answered)`.
- [X] T120 [P] Add the reason to the agent tool: `set_waiting(on, reason=None)` in
  `onboarding/agent/src/onboarding_agent/tools/session.py` cleans it (one line, ≤ 120 characters) and emits
  `{"type": "waiting", "on": on, "reason": reason}`; the `set_waiting` entry in `TOOL_SPECS` in
  `onboarding/agent/src/onboarding_agent/loop.py` gains an optional `reason` string ("the awaited person's next step,
  one short line, e.g. 'run step 4 and paste the output'"), and the system prompt's waiting line includes the stored
  reason from `payload["waiting_reason"]`.
- [X] T121 [P] Update the waiting rule in `onboarding/agent/src/onboarding_agent/prompts/en/system.md`: whenever you
  call `set_waiting` with a person, give `reason` as that person's next step in one short imperative line; never say
  or imply that messages will be held or queued while you wait (FR-006a).
- [X] T122 [P] Add `--messages N` to `onboarding/deploy/scripts/smoke.py` (used with `--seed`): insert N messages per
  thread into the seeded session directly in MongoDB through `onboarding_api.chat.messages.add`, alternating the
  participant and the agent, every tenth one a 40-line command output, so quickstart §3a step 8 and T125 have long
  threads without model calls.

**Checkpoint**: an agent wait arrives in the browser with its reason, survives a reload, and clears on its own.

---

## Phase 15: User Story 3 (revision 2) - Scrolling threads and a waiting banner (Priority: P2)

**Goal**: each thread scrolls in its own area with the page fixed, and both people always see who the agent is
waiting for and what that person must do.

**Independent Test**: quickstart §3a steps 7 and 8 on the dev stack: the banner shows in all four places with the
right wording and clears within 2 s; with 200 seeded messages per thread nothing but the thread logs scroll.

### Tests for User Story 3 (revision 2)

- [X] T123 [P] [US3] Extend `onboarding/api/tests/integration/test_threads.py`: a `waiting` output with a reason
  stores and emits it; a reason with a secret is masked, a multi-line reason becomes one line, a 300-character reason
  is cut to 120; `on: null` clears the reason; after the awaited participant's message is answered with no new
  `waiting` output, `waiting_on` and `waiting_reason` are null and `thread.waiting` with nulls was emitted; session
  GET returns both fields.
- [X] T124 [P] [US3] Extend `onboarding/agent/tests/unit/test_thread_tools.py`: `set_waiting("application_owner",
  "run step 4\nand paste")` emits one line; a reason over 120 characters is cut; `set_waiting(None, "x")` emits a
  null reason.
- [X] T125 [P] [US3] Write `onboarding/web/e2e/waiting-scroll.spec.ts` (two browsers, dev stack, ISC stub):
  - **Banner (SC-013)**: drive the agent to wait for the application owner (the trust scenario from
    `threads.spec.ts`); within 2 s both threads on both screens show `[role=status]` above the composer or view-only
    note; on the owner's screen the text starts "Waiting for you:", on the IAM screen "Waiting for <owner name>:",
    both followed by the same reason; no banner text contains "queued" or "held"; after the owner confirms, all four
    banners are gone within 2 s.
  - **Scrolling (SC-012)**: seed with `smoke.py --seed --messages 200`; the page itself does not scroll
    (`document.scrollingElement.scrollHeight <= innerHeight + 1`); each thread log is scrollable and starts at its
    newest message; composer, chips and banner are in the viewport; scroll the IAM engineer's own log to the top, send
    a message from the owner: the IAM log keeps its scroll position and shows "New messages"; clicking it reaches the
    newest; a 40-line command output does not make its thread wider than before.

### Implementation for User Story 3 (revision 2)

- [X] T126 [US3] Carry the reason in the browser: `waiting_reason` on `Session` in
  `onboarding/web/src/app/shared/models.ts`; in `onboarding/web/src/app/shared/live-stream.service.ts` the `waiting`
  signal becomes `{on: Role, reason: string | null} | null`, set from the session on load and resync and from
  `thread.waiting {waiting_on, reason}`.
- [X] T127 [US3] Create `onboarding/web/src/app/shared/waiting-banner.component.ts` (`app-waiting-banner`, inputs
  `viewerRole`, `names`): renders nothing when not waiting; otherwise a strip with an inline stroke clock SVG
  (`aria-hidden`), a bold lead and the reason — awaited viewer: "Waiting for you:"; other viewer: "Waiting for
  <display name>:"; no reason → "the application steps" (application owner) or "the next SailPoint order" (IAM
  engineer). `role="status"`, `aria-live="polite"`. Colours as CSS custom properties from research R20:
  `--waiting-bg: #FFF4E5`, `--waiting-border: #E8A33D` (top border), `--waiting-icon: #B45309`, text `#000000`,
  13 px / 18 px. All text through `$localize` (`@@waiting.you`, `@@waiting.other`, `@@waiting.defaultOwner`,
  `@@waiting.defaultIam`).
- [X] T128 [US3] Restructure `onboarding/web/src/app/shared/thread.component.ts` (research R19): a column of header,
  **log**, `<app-waiting-banner>`, then the projected composer or the view-only note; remove the old waiting text
  (`@@thread.waitingYou`, `@@thread.waitingOther`) and keep the view-only hint. The log: height
  `clamp(320px, 60vh, 560px)`, `overflow-y: auto`, `scrollbar-gutter: stable`, `scrollbar-width: thin`; tracks
  `atBottom` (within 48 px of the end) on scroll; after each change to this thread's messages, deltas or progress line
  it scrolls to the end when `atBottom` or when the newest message is the viewer's own, otherwise adds to an unseen
  count and shows a "New messages (n)" `<button>` pinned to the log's bottom edge that scrolls to the end and resets
  the count; on first render it starts at the end (a live-stream resync does not move the reader). Messages get `overflow-wrap: anywhere`; `pre` inside
  them `overflow-x: auto` (check `onboarding/web/src/app/shared/rich-text.component.ts`).
- [X] T129 [US3] Lay out "Conversations" in `onboarding/web/src/app/session/session-screen.component.css` so the two
  threads sit side by side at ≥ 1100 px and stack below it, each thread keeping its own log height, and nothing in
  the conversation makes the page grow (FR-006f, research R19).

**Checkpoint**: quickstart §3a steps 7 and 8 pass on the dev stack; T104, T112 and the earlier e2e specs still pass.

---

## Phase 16: Revision 2 polish

- [X] T130 [P] Update `docs/DEMO-GUIDE.md` §8.3 and `onboarding/README.md`: the waiting banner (what each person sees)
  and the scrolling threads with the "New messages" button.
- [X] T131 Run the full checks: `uv run pytest` in `onboarding/api` and `onboarding/agent`, `ruff check`,
  `make onboarding-evals` (prompt change: SC-005 must stay ≥ 9/10), and `make onboarding-e2e` (all specs, including
  T125).
- [X] T132 Rebuild and roll out: `make onboarding-images` (one container: API + web), `kubectl -n onboarding
  rollout restart deploy/onboarding-api`, redeploy the agent code to AgentCore, then `make onboarding-leak-scan`.
- [ ] T133 Through the public URL on the cluster, run quickstart §3a steps 7 and 8 and record the result in
  `specs/001-isc-onboarding-agent/checklists/quickstart-run.md`.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: none.
- **Foundational (Phase 2)**: needs Setup; blocks every story.
- **US1 (Phase 3)** and **US2 (Phase 4)**: both need Foundational and can run in parallel. Together they make a
  complete onboarding.
- **US3 (Phase 5)**: needs Foundational. It improves the screens built in US1/US2, so its web tasks come after T052
  and T058.
- **US4 (Phase 6)**: needs US1 (ISC tools and steps); its application-side fixes reuse US2's screen.
- **US5 (Phase 7)**: needs Foundational (T017–T019); T071 links to T051 (US1).
- **US6 (Phase 8)**: needs Foundational; independent of other stories.
- **Polish (Phase 9)**: after the stories it covers. T081/T082 need US1–US4.

- **Phase 10 (revision foundation)**: needs Phases 1–9 (it changes built code). Blocks Phases 11 and 12.
- **Phase 11 (US3 revised)** and **Phase 12 (US7)**: both need Phase 10. US7's web chips (T111) need the thread view
  (T101, T102); its API and agent tasks (T105–T110) can run in parallel with Phase 11.
- **Phase 13**: after Phases 11 and 12. T084 (real-tenant run) is best done after T116.
- **Phase 14 (revision 2 foundation)**: needs Phases 10–13. T117 → T119 (same data path); T118, T120, T121, T122
  in parallel. Blocks Phase 15.
- **Phase 15 (US3 revision 2)**: tests T123–T125 first (T125 needs T122); T126 → T127 → T128 (the banner sits in
  the thread); T129 alongside T127.
- **Phase 16**: after Phase 15; T132 before T133.

### Within each story

Playbook files and tests → agent tools/prompt → API routes → web screens.

### Parallel Opportunities

- Setup: T003, T004, T005, T006 together after T001/T002.
- Foundational: catalog file T017, agent skeleton T032/T033, and k8s files T038–T041 run alongside the API core
  (T007–T016).
- US1: T044, T045, T046, T047, T051 together; then T048 → T049; T052 → T053.
- US2: T054 and T055 together, then T056/T057, then T058.
- US1 and US2 can be staffed in parallel (agent/ISC vs playbook/owner screen).
- US4: T065 and T066 together; T067 (API) alongside T068 (agent).
- US6 can proceed in parallel with US3–US5.

### Parallel Example: User Story 1

```text
Task: "T044 ISC tool tests in onboarding/agent/tests/unit/test_isc_tools.py"
Task: "T045 Role-gate test in onboarding/agent/tests/unit/test_role_gate.py"
Task: "T046 Port settings.yaml in onboarding/catalog/playbooks/aws-saas/settings.yaml"
Task: "T047 checks.yaml in onboarding/catalog/playbooks/aws-saas/checks.yaml"
Task: "T051 Session creation flow in onboarding/web/src/app/session/new-session.component.ts"
```

---

## Implementation Strategy

### MVP first (User Story 1)

1. Phase 1 Setup → Phase 2 Foundational (check: sign in, register tenant, streamed agent reply).
2. Phase 3 US1 against an AWS role prepared by hand (or with the existing skill's `aws-setup.sh`).
3. **Stop and validate** with quickstart §3 step 5: three checks pass and five action records name the engineer.

### Incremental delivery

1. **+ US2**: the application owner prepares AWS through the agent. This is the first complete two-person onboarding.
2. **+ US3**: live shared conversation polish. Demo it to both teams.
3. **+ US4**: troubleshooting from text and screenshots, held screenshots, diagnosis evals.
4. **+ US5 / US6**: catalog page, admin, history.
5. **Polish**: leak scan, metrics, stub ISC, the full end-to-end run, docs, the real-tenant quickstart run.

### Revision delivery (T085–T116)

1. Phase 10 → Phase 11 → **stop and validate** quickstart §3a steps 1–5 (threads, relay, waiting, owner-confirm rerun).
2. Phase 12 → validate §3a step 6 (suggestions).
3. Phase 13 → deploy to the cluster, then the real-tenant run (T084).

### Revision 2 delivery (T117–T133)

1. Phase 14 → check one wait end to end (reason in the event, on reload, auto-clear).
2. Phase 15 → **stop and validate** quickstart §3a steps 7–8 on the dev stack.
3. Phase 16 → full checks, roll out the single container and the agent, validate through the public URL.

### Notes

- Never put the ISC client secret anywhere but the AgentCore Identity call in T020. Check with T079 after every phase.
- Commit after each task or logical group; stop at each checkpoint to validate the story on its own.
