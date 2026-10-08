# Research: SailPoint ISC Application Onboarding Agent

Every Technical Context item is resolved here. Fixed inputs from the user: MongoDB, Claude Haiku on AgentCore, all
other components on the local Kubernetes cluster.

## R1. Where each component runs

- **Decision**: The web app, session API and MongoDB run on the local cluster. Only the agent runtime runs on AWS
  AgentCore, and it calls Claude Haiku 4.5 on Bedrock.
- **Rationale**: The user named AgentCore as the agent host, and AgentCore is a managed AWS service, so the agent
  can't run on the local cluster. Everything holding user data (accounts, transcripts, screenshots, audit) stays
  local, as asked. The cloud side only receives one turn at a time. This is the split the existing demo already uses
  (`chatbot/` + gateway locally, `agent/` on AgentCore).
- **Alternatives considered**:
  - Agent in a local pod calling Bedrock directly: contradicts "LLM on AgentCore".
  - API also on AgentCore: moves user data to the cloud and loses the local-only requirement.

## R2. Agent invocation: who calls AgentCore, and how it is authorised

- **Decision**: Only the session API invokes the runtime, using `bedrock-agentcore:InvokeAgentRuntime` with SigV4.
  The credentials belong to a dedicated IAM user, `onboarding-api`, whose policy allows only that one runtime ARN (plus
  R4's provider calls). They are stored as a Kubernetes Secret.
- **Rationale**: The browser never talks to the agent, so the per-session queue, masking and audit (FR-006a, FR-026,
  FR-020) cannot be bypassed. A local cluster has no workload identity, so a narrowly scoped access key is the
  simplest least-privilege option.
- **Alternatives considered**:
  - The browser calling AgentCore with a JWT, as `chatbot/` does: it would skip the API's queue and masking.
  - An AgentCore JWT authorizer with tokens minted by the API: works, but adds a signing-key lifecycle for no gain
    here.

## R3. Agent statefulness and the shared queue

- **Decision**: The agent is **stateless per turn**. For each queued message the API sends:
  - the session context (tenant, connector type, details, step states);
  - the last N masked messages, with speaker and addressee;
  - the new message and any attachment (as an image block);
  - the ordering user's id and role.

  The API processes one turn per session at a time (a per-session lock in MongoDB, `sessions.turn_lock`).
- **Rationale**: Satisfies FR-006a (one shared conversation, arrival order, nothing jumps the queue). MongoDB holds
  the single source of truth, the agent can scale out without sticky sessions, and replays and evals are
  deterministic.
- **Alternatives considered**:
  - AgentCore Memory as the transcript store: duplicates MongoDB and breaks "data stays local".
  - Runtime session stickiness (`runtimeSessionId` per onboarding session): kept as an optimisation only, never
    relied on.

## R4. SailPoint credential storage (FR-025)

- **Decision**: Each tenant's ISC personal access token (client id + secret) is stored as an **AgentCore Identity
  OAuth2 credential provider** of the custom-provider type, client-credentials (M2M) flow, with the token URL
  `https://<tenant>/oauth/token`. The agent obtains a short-lived ISC access token through the runtime's workload
  identity (`GetResourceOauth2Token`, M2M). MongoDB stores only the provider name and status.
- **Rationale**:
  - The secret is never in MongoDB, never in the API after the admin form posts it once, and never in a prompt.
  - Rotation is a provider update.
  - The pattern is proven here: `scripts/agentcore-gateway.sh` already manages the `mcpdemo-entra-obo` provider.
- **Alternatives considered**:
  - Secrets Manager per tenant, read by the agent: workable, but the agent would handle the raw secret.
  - Kubernetes Secret in the API, passing a token per turn: the token would cross the network inside the invoke
    payload.
  - Storing it in MongoDB encrypted: key management on a laptop cluster is weak.
- **Open item**: if the custom provider rejects ISC's token endpoint for M2M, fall back to Secrets Manager read by the
  agent role; nothing else in the design changes.

## R5. Agent model and loop

- **Decision**:
  - Claude Haiku 4.5 on Bedrock (the inference profile already used by `agent/agent.py`), with images enabled.
  - A plain tool-use loop of at most 8 tool rounds per turn.
  - Every tool call is streamed to the API as a `progress` event before it runs and a `tool_result` event after.
- **Rationale**: The proven model and region in this repo. Vision covers screenshots (FR-007, FR-026a). A bounded
  loop keeps a turn under the 5-second first-token target: text streams while tools run.
- **Alternatives considered**: a Bedrock Agent with action groups, as in this repo's `bedrock-agent` engine. It adds
  a second agent definition and RETURN_CONTROL plumbing that this product doesn't need.

## R6. Generic tools + connector playbooks (FR-028–FR-030, SC-009)

- **Decision**:
  - **Generic ISC tools**: `get_tenant_external_id`, `get_connector_form(type)`, `find_source(name)`,
    `create_source(type, name, owner, creation_settings)`, `configure_source(source_id, settings)`,
    `peek_accounts(source_id)` (the connection check), `start_aggregation(source_id, kind)`,
    `get_task(task_id)`, `test_connection(source_id)`, `delete_session_source(source_id)` (only sources created in
    this session).
  - **Session tools**: `set_step(step, state)`, `address(actor)` (marks who a reply is for),
    `record_application_step(text, read_only)`.
  - Everything connector-specific lives in `onboarding/catalog/playbooks/<type>/`:
    - `setup.md`: the steps for the application owner, as templated commands;
    - `settings.yaml`: field mapping, creation-only fields and defaults;
    - `checks.yaml`;
    - `failures.md`: signature → cause → fix, side = sailpoint | application.

    The agent's system prompt is assembled from the session's playbook.
- **Rationale**: A new connector type is new playbook files plus a catalog entry, with no change to tools, API or UI
  (SC-009). The AWS SaaS playbook is a direct port of the skill's `scripts/isc-source.sh` (spec id, `idnProxyType`,
  `spConnectorSupportsCustomSchemas`, `cloudScope`, change-password policy ARN), `references/aws-permissions.md` and
  `references/troubleshooting.md`, which are already proven on two tenants.
- **Alternatives considered**: one tool set per connector type, which grows linearly and violates SC-009.

## R7. Live updates to both screens

- **Decision**:
  - **Server-Sent Events** from the API, one stream per session and viewer.
  - Messages are sent with REST `POST`.
  - Events are written to MongoDB first, then published through an in-process broadcaster (single API replica).
  - A client reconnects with `Last-Event-ID` and replays from the `events` collection.
- **Rationale**:
  - Only the server pushes, so SSE is enough.
  - SSE passes through nginx, the repo's `mcpdemo-edge` and ngrok without WebSocket upgrades.
  - Replay by event id covers the edge case "one participant was offline".
- **Alternatives considered**:
  - WebSockets: bidirectional, but not needed.
  - MongoDB change streams: these need a replica set. Keep them as the path to more than one API replica (run Mongo
    as a one-member replica set from day one so the switch is configuration only).

## R8. Authentication (FR-001–FR-004)

- **Decision**:
  - Local accounts in MongoDB, with argon2id password hashes.
  - Server-side sessions: a random 256-bit id in an `HttpOnly; Secure; SameSite=Strict` cookie, stored with a
    30-minute sliding idle expiry.
  - Lockout after 5 consecutive failures for 15 minutes.
  - The first admin is created by a deploy-time bootstrap job from a one-time Kubernetes Secret.
- **Rationale**: Matches the spec's local-accounts decision. Server-side sessions make lockout, idle timeout and
  forced sign-out simple and auditable.
- **Alternatives considered**:
  - JWT access tokens: hard to revoke, with no gain at this scale.
  - Entra/OIDC: SSO is out of scope for v1.

## R9. Secret masking (FR-026, FR-026a, SC-004)

- **Decision**: One masker module in the API, applied to:
  - every inbound message before storage, broadcast and agent input;
  - every agent text and tool result before storage and broadcast;
  - every log line.

  Patterns:
  - AWS access key ids (`AKIA|ASIA[0-9A-Z]{16}`);
  - 40-character AWS secret keys in key=value or JSON context;
  - session tokens;
  - JWTs (`eyJ…`);
  - ISC PAT secrets (64 hex);
  - generic `password|secret|token: <value>` pairs.

  For screenshots, the first agent call of the turn is a **secret check**: a vision-only request with a fixed prompt.
  If it says *held*, the API keeps the image only in the uploader's pending area (FR-026a).
- **Rationale**: A deterministic masker handles text, and the model is used only where pattern matching can't see
  (pixels). The two never mix.
- **Alternatives considered**: an LLM-only masking pass on text, which is slower and not deterministic for SC-004.

## R10. Retention and audit

- **Decision**:
  - TTL indexes delete `messages`, `events` and `attachments` 90 days after their session finishes. The session
    document gets an `expires_at` at finish time.
  - `actions` (SailPoint action records) and `audit` (sign-in/admin) have **no TTL**.
- **Rationale**: Matches the spec's assumption: history for 90 days, action records kept under the organisation's
  audit retention.

## R11. Local cluster packaging

- **Decision**:
  - Namespace `onboarding`.
  - MongoDB as a single-member replica-set StatefulSet (`mongo:8.0`, 5 Gi PVC, no storageClass name so it works on
    both k3s `local-path` and Docker Desktop `standard`). The no-storageClass lesson comes from
    `deployment/redis-persistence.yml`.
  - `onboarding-api` Deployment (1 replica): one container whose API also serves the built Angular app (a
    separate nginx web deployment was dropped to run a single container).
  - NetworkPolicies: Mongo accepts only the API; the API accepts no pod traffic (only the edge's port-forward).
  - Exposed on the shared ngrok domain under `/onboarding/` through the existing `mcpdemo-edge` nginx. That adds one
    `location` block, prefix stripped, the same pattern as `/mcp/`.
  - Images are built into `localhost:5000/onboarding-*` with `scripts/03-build-docker.sh`'s approach.
- **Rationale**: The same clusters and conventions as the rest of this repo, including Docker Desktop on macOS (per
  the project's standing preference).

## R12. Internationalisation readiness

- **Decision**:
  - Angular `@angular/localize` with English message IDs for all UI text.
  - Agent prompts and canned agent strings under `prompts/en/`.
  - The language is carried in the turn request (`lang: "en"`).
- **Rationale**: Thai later is a translation file plus `prompts/th/`, with no redesign (spec assumption).

## R13. Testing strategy

- **Decision**:
  - **Contract tests**: the API against `contracts/session-api.openapi.yaml`; agent event-stream fixtures against
    `contracts/agent-invocation.md`.
  - **ISC tools**: tested with respx-mocked ISC responses copied from real calls (shapes from
    `references/isc-api.md`).
  - **Masker**: tested with a corpus of real-looking keys.
  - **Agent diagnosis evals** (SC-005): each FR-024 failure is replayed as error text and as a screenshot, 10 runs each;
    pass if the cause and side are named in the first reply.
  - **End-to-end**: Playwright drives two browser contexts (IAM engineer and application owner) through the
    quickstart flow against a stub ISC, and once against a real demo tenant.

## R14. Observability

- **Decision**:
  - JSON logs through the masker.
  - A per-turn `turn_id` across API, agent and ISC calls.
  - AgentCore runtime logs at 1-day retention, as in `scripts/agent-deploy.sh`.
  - API metrics: turn latency to first token, relay latency, ISC call errors.
- **Rationale**: Enough to verify SC-003/SC-003a and to debug a failed onboarding without reading transcripts.

---

*Revision 2026-10-07: decisions for the spec revisions "one thread per person" (US3, FR-006–FR-006d, FR-016a,
SC-010) and "suggested replies" (US7, FR-006e, SC-011).*

## R15. Threads, relay notes and the one queue

- **Decision**:
  - Keep **one `messages` collection and one queue per session**; each message gets a `thread`
    (`iam_engineer` | `application_owner`) and a `kind` (`message` | `relay_note`). A participant's message always
    goes into their own thread (the API sets it from the caller's role; the browser cannot choose).
  - The agent's streamed reply goes into the **writer's thread**. To reach the other participant the agent calls one
    tool, `post_to_other_thread(text?, relay_note)`: `text` (optional) becomes an agent message in the other thread,
    `relay_note` becomes a one-line relay note in the writer's thread. News for both (FR-006c, clarification 3) is
    the same tool with only a `relay_note` to the other side: `notify_other_thread(relay_note)`.
  - The agent marks who it waits for with `set_waiting(on)`; the API keeps `sessions.waiting_on` and shows it as
    information only (clarification 2): it never blocks or reorders the queue.
  - The agent still receives **both threads** as history, each entry tagged with its thread, so it can see what the
    other person said and did (FR-006a).
- **Rationale**: one queue keeps the earlier clarification (arrival order, nothing jumps the queue) and the existing
  turn lock, replay, masking and SSE code. The thread is a label on a message, so "both threads on both screens" is
  just a filter in the browser; view-only is enforced by the API (writes always land in the caller's own thread).
  Tool-based posting makes the relay explicit and testable (SC-010): the API can check every `post_to_other_thread`
  produced a relay note.
- **Alternatives considered**:
  - Two separate agent conversations per session: the agent would lose sight of the other side, and keeping two
    queues in step reintroduces the ordering problems clarification 1 solved.
  - Letting the agent choose a thread per streamed chunk: hard to stream correctly and impossible to audit.
  - Free-form text markers in the reply ("@owner: …"): fragile to parse, and a model slip would put instructions in
    the wrong thread.

## R16. Standing check order (FR-016a)

- **Decision**: when the IAM engineer's turn runs any check tool (`peek_accounts`, `start_aggregation`,
  `test_connection`), the API stores `sessions.check_order = {user_id, display_name, turn_id, at}`. In an
  application owner's turn, if `check_order` is set and a source exists, the agent is offered **only** the three
  check tools (no create, configure, fix or delete). Each resulting action record is written with
  `ordered_by = check_order` and `trigger = "application_owner_confirmation"`. `check_order` is cleared when all
  checks pass or the IAM engineer orders anything new.
- **Rationale**: matches clarification 1 exactly, keeps the role gate in code (not in the prompt), and keeps SC-006
  true (every action names the IAM engineer who ordered it).
- **Alternatives considered**: a prompt-only rule (unsafe: the model decides), or a confirmation button for the IAM
  engineer (rejected by the clarification).

## R17. Suggested messages (FR-006e, US7, SC-011)

- **Decision**: two sources, merged by the API per thread:
  1. **Agent suggestions**: at the end of each turn the agent calls `suggest_replies(thread, items[])` for one or both
     threads (items: `{text, kind: answer|order|question}`), typically answers to what it just asked.
  2. **Playbook defaults**: `playbooks/<id>/suggestions.yaml` lists suggestions per role keyed by session state
     (no source yet, waiting for owner output, a check failed, all checks passed, any). Shipped with the catalog, so a
     new connector type brings its own (SC-009).
  The API validates and fills: drops `order` items for the application owner, runs every item through the masker and
  drops any that changed (no secrets), de-duplicates, takes agent items first, tops up from defaults to **at least
  3** and caps at 5. It stores them as `sessions.suggestions.<thread>` with the `event_id` they were made for and
  emits `suggestions.updated` to that thread's participant only. A step change without an agent turn recomputes from
  defaults.
  Picking a suggestion is browser-only (fills the box); nothing is stored unless sent (FR-006e).
- **Rationale**: the agent knows what it just asked; defaults guarantee the "at least 3" and the 1 s target (SC-011)
  even when the agent gives none, without an extra model call.
- **Alternatives considered**: a separate model call per turn for suggestions (adds latency and cost, misses the 1 s
  target); static suggestions only (cannot answer the agent's specific question).

## R18. Sessions created before threads

- **Decision**: a one-off migration in the API's startup index step: messages without `thread` get one from their
  old fields: a participant message → the speaker's thread; an agent message with `addressed_to` of a participant →
  that thread; `addressed_to: both` → the thread of the participant whose message started that turn (`turn_id`).
  `kind` defaults to `message`. Idempotent; `addressed_to` is then ignored.
- **Rationale**: existing demo sessions stay readable without special cases in the browser.
- **Alternatives considered**: hiding old sessions (loses the user's real-tenant session), or a "legacy" shared view
  (two code paths for one demo).

## R19. Scrolling threads (FR-006f, US3 #7-8, SC-012)

- **Decision**: each thread is a column with a fixed header, a **message log** that scrolls on its own, then the
  waiting banner, the suggestions and the message box (or the view-only note). The log has a set height
  (`clamp(320px, 60vh, 560px)` on desktop; the canvas uses 520 px) with `overflow-y: auto`, a stable scrollbar gutter
  and a thin visible scrollbar; the page never grows with the conversation. Follow mode: the log is "at the bottom"
  when within 48 px of the end. A new message, delta or relay note scrolls to the end only when the viewer is at the
  bottom (or it is their own message just sent); otherwise the position is kept and a **"New messages" button**
  (count of unseen items) appears at the bottom edge of the log, jumping to the end and clearing the count. A thread
  opens (page load or rejoin) at its end; a live-stream resync does not move the reader. Each log tracks its own state, so the two threads scroll independently. Long content
  (pasted command output, code blocks) wraps or scrolls sideways inside its message (`overflow-wrap: anywhere`,
  `pre { overflow-x: auto }`), never widening the thread. Below ~1100 px wide the two threads stack, each keeping its
  own log height.
- **Rationale**: matches the updated canvas (fixed-height log, thin scrollbar, newest at the bottom) and the usual
  chat behaviour that the spec's assumption names; yanking a reader to the bottom while they read older output is the
  failure the "New messages" button avoids. Pure browser work: no API or agent change.
- **Alternatives considered**: `flex-direction: column-reverse` as in the canvas markup (auto-anchors at the bottom
  but reverses keyboard/screen-reader order and fights the "keep position" rule), whole-page scrolling (pushes the
  message box off screen, the problem being fixed), virtualised lists (unneeded at ≤ a few hundred messages; SC-012
  tests 200).

## R20. Waiting banner (FR-006g, US3 #4 and #9, SC-013)

- **Decision**: `set_waiting(on, reason?)` gains a **reason**: one line, ≤ 120 characters, masked, phrased as the
  awaited person's next step ("run step 4 and paste the output", "apply the trust fix and confirm"). The API stores
  it as `sessions.waiting_reason` beside `waiting_on`, sends it in `thread.waiting {waiting_on, reason}`, and clears
  both when the wait ends (`set_waiting(null)`, or automatically when the awaited participant's message is answered
  and the agent did not set a new wait). The browser renders the banner in **both** threads on **both** screens,
  between the log and the composer (or view-only note), worded for the viewer:
  - awaited participant: **"Waiting for you:"** + reason;
  - other participant: **"Waiting for <display name>:"** + reason.
  No reason → a role default from the playbook's suggestion states (`application_owner`: "the application steps";
  `iam_engineer`: "the next SailPoint order", worded to read right on both screens). The banner never says messages will be held (FR-006a); the canvas's
  "Your next message will be queued" line is dropped (spec clarification).
  Look (from the canvas, kept as tokens): background `#FFF4E5`, top border `#E8A33D`, clock icon stroke `#B45309`,
  text `#000000` with a bold lead (≥ 4.5:1), 13 px/18 px, inline stroke SVG clock; `role="status"` with
  `aria-live="polite"` so screen readers announce changes once. Not part of the scrolling log, so it is always in
  view.
- **Rationale**: the old thread foot line was easy to miss; the canvas makes waiting a strip of its own. Putting the
  reason in the agent's tool call (rather than parsing its reply) keeps wording reliable and masker-checked; per-viewer
  wording is pure display, so one stored value serves all four places.
- **Alternatives considered**: showing the banner only in the awaited person's thread (the spec wants both people to
  see who is waited on), a toast/notification (disappears; not visible on rejoin), deriving the reason from the last
  relay note (often longer than a line and phrased for the other person).
