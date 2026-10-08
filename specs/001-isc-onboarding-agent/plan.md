# Implementation Plan: SailPoint ISC Application Onboarding Agent

**Branch**: `001-isc-onboarding-agent` | **Date**: 2026-10-07 (revised 2026-10-07: threads per person, suggested
replies; scrolling threads and waiting banner) | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/001-isc-onboarding-agent/spec.md`

## Summary

A two-actor web app (IAM engineer, Application owner) with **one thread per person** in each onboarding session (each
person writes in their own thread and sees the other's live, view only; each thread scrolls in its own area under a prominent waiting banner), suggested
replies in each own thread, and an
AI agent that works between the two threads to onboard an application into SailPoint ISC. v1 ships one connector type, **AWS SaaS**, ported from the
proven `sailpoint-isc-aws-connector` skill.

User direction for this plan: **MongoDB** is the database, the LLM is **Claude Haiku 4.5 on Bedrock, run from an
AgentCore runtime**, and **everything else runs locally on the project's Kubernetes cluster** (k3s on WSL, or Docker
Desktop Kubernetes on macOS — the same clusters this repo already targets).

Technical approach:

- **Local cluster (namespace `onboarding`)**: Angular web app (its build served by the session API, one container), a Python **session API** (FastAPI)
  that owns accounts, sessions, the per-session message queue, secret masking, live fan-out (SSE) and the audit
  record, and **MongoDB** (single-replica StatefulSet with a PVC; screenshots in GridFS).
- **AWS (ap-southeast-1)**: one **AgentCore runtime**, `isc_onboarding_agent`, running Claude Haiku 4.5 on Bedrock. It is
  stateless per turn: the session API sends the turn plus the session context, and the agent streams back reply text,
  progress lines, tool results and status changes. It calls the SailPoint ISC REST API itself through tools; it has
  **no AWS tools** (FR-010).
- **SailPoint credentials** live in **AgentCore Identity** as one OAuth2 client-credentials provider per tenant: never
  in MongoDB, never in the API, never in a prompt (FR-025).
- **Threads** are a label on each message (`thread`, `kind: message | relay_note`) over the existing single queue per
  session; the agent reaches the other person through explicit thread tools and leaves a relay note (research
  R15). An IAM engineer's order to run the checks stands, so an application owner's confirmation can rerun only
  those checks (R16). Suggested replies come from the agent's `suggest_replies` tool, topped up from playbook
  defaults to 3-5 per thread by the API (R17). Existing sessions are migrated once (R18).
- **Connector types** are data, not code: a release-shipped catalog plus one playbook per type (setup steps, source
  settings, checks, known failures). The agent's tools are generic ISC source operations driven by the playbook
  (FR-028–FR-030, SC-009).

## Technical Context

**Language/Version**: Python 3.12 (session API, agent); TypeScript 5.9 with Angular 22 (web app, same toolchain as `chatbot/`)

**Primary Dependencies**:
- API: FastAPI, `pymongo` 4.x async API, `argon2-cffi`, `boto3` (AgentCore invoke/control), `sse-starlette`.
- Agent: `bedrock-agentcore` SDK, `anthropic[bedrock]` (Claude Haiku 4.5 with vision), `httpx`.
- Web: Angular standalone + signals, Deloitte tokens from `chatbot/src/styles.css`, `@angular/localize`.

**Storage**: MongoDB 8.0 (StatefulSet, 5 Gi PVC), GridFS for screenshots; TTL indexes for 90-day session history

**Testing**: pytest + respx (ISC API mocked) + mongomock-motor or a throwaway Mongo container; Angular unit tests (vitest via `@angular/build`); Playwright two-browser end-to-end (IAM engineer + application owner); recorded-transcript evals for the agent's diagnosis (SC-005)

**Target Platform**: local Kubernetes (k3s / Docker Desktop Kubernetes, `localhost:5000` registry) + AWS AgentCore Runtime (ap-southeast-1) + Amazon Bedrock

**Project Type**: web application (frontend + backend API) plus a hosted agent

**Performance Goals**: relay a message or status change to the other screen in ≤ 2 s p95 (SC-003); first streamed agent words in ≤ 5 s p95 (SC-003a); refreshed suggestions within 1 s of a reply finishing (SC-011, no extra model call)

**Constraints**:
- No secrets in MongoDB, logs, prompts or the browser (FR-025–FR-027).
- The agent never acts on AWS (FR-010).
- One queue per session across both threads (FR-006a); writes always land in the caller's own thread (FR-006).
- The agent never decides where its streamed reply goes; only its thread tools reach the other thread (FR-006c).
- Suggestions never send by themselves and never offer the application owner a SailPoint order (FR-006e).
- English UI text is externalised for later Thai.
- Desktop browsers only; below ~1100 px wide the two threads stack (research R19).

**Scale/Scope**: a team: ≤ 50 accounts, ≤ 10 concurrent sessions, ≤ 2 participants per session; 1 available connector type, 9 planned

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

`.specify/memory/constitution.md` is still the unfilled template, so there are no ratified principles to gate on.
Until `/speckit-constitution` is run, this plan gates on the rules this repository already enforces (README "Gates",
`docs/DEMO-GUIDE.md` §2):

| Gate (from this repo's practice) | How this design meets it | Pre | Post |
|---|---|---|---|
| Secrets never in events, logs or UI; tokens shown only as placeholders | Masking on API ingress; ISC credential only in AgentCore Identity; structured logs run through the same masker | ✅ | ✅ |
| Least privilege per hop | Browser → API (session cookie); API → AgentCore (`InvokeAgentRuntime` on one runtime only); agent → ISC (per-tenant M2M token, source-admin scope) | ✅ | ✅ |
| Identity of the human preserved end to end | Every turn carries the ordering user id; every ISC change has an action record naming them (FR-020, SC-006) | ✅ | ✅ |
| Nothing reachable except through its gate | MongoDB is cluster-internal (ClusterIP + NetworkPolicy admitting only the API); only the web and API are exposed | ✅ | ✅ |
| Reproducible deploy scripts (`make` targets, idempotent) | `make onboarding-*` targets mirroring `scripts/agent-deploy.sh` style | ✅ | ✅ |
| (revision) Who may change SailPoint is enforced in code, not the prompt | Role gate offers write tools per turn; application owner turns get only the three check tools, and only under a standing `check_order` (R16); view-only threads enforced by the API (R15) | ✅ | ✅ |

No violations, so Complexity Tracking stays empty. Recommend running `/speckit-constitution` before `/speckit-tasks` so
these gates become ratified principles.

## Project Structure

### Documentation (this feature)

```text
specs/001-isc-onboarding-agent/
├── plan.md              # This file
├── research.md          # Phase 0: decisions and alternatives
├── data-model.md        # Phase 1: MongoDB collections, states, indexes
├── quickstart.md        # Phase 1: end-to-end validation guide
├── contracts/
│   ├── session-api.openapi.yaml   # browser ↔ session API (REST)
│   ├── live-events.md             # session API → browser (SSE event stream)
│   ├── agent-invocation.md        # session API ↔ AgentCore runtime (request + streamed events)
│   └── connector-playbook.md      # shape of a connector type (catalog entry + playbook)
├── checklists/requirements.md
└── tasks.md             # Phase 2 (/speckit-tasks — not created here)
```

### Source Code (repository root)

```text
onboarding/
├── api/                          # session API (FastAPI), runs on the local cluster
│   ├── src/onboarding_api/
│   │   ├── auth/                 # local accounts, argon2, lockout, idle timeout, admin flag
│   │   ├── sessions/             # sessions, participants, status steps, history
│   │   ├── chat/                 # per-session queue, threads + relay notes, masking, SSE fan-out
│   │   │   └── suggestions.py    # merge agent + playbook suggestions, validate, 3-5 per thread (R17)
│   │   ├── agent_client/         # AgentCore invoke (SigV4) + event stream parsing
│   │   ├── tenants/              # tenant registry; creates/rotates AgentCore Identity providers
│   │   ├── catalog/              # loads the shipped catalog (read-only)
│   │   ├── audit/                # SailPoint action records, admin/sign-in events
│   │   └── masking.py            # one masker for messages, logs and agent output
│   └── tests/{unit,contract,integration}/
├── agent/                        # AgentCore runtime (Python), runs on AWS
│   ├── src/onboarding_agent/
│   │   ├── main.py               # BedrockAgentCoreApp entrypoint, streaming events
│   │   ├── loop.py               # Claude Haiku tool loop
│   │   ├── isc/                  # ISC REST client + generic source tools
│   │   ├── tools/session.py      # thread tools: post_to_other_thread, notify_other_thread, set_waiting, suggest_replies
│   │   ├── playbooks.py          # loads catalog/playbooks into prompts and tool args
│   │   └── prompts/en/           # system prompts (Thai later: prompts/th/)
│   └── tests/{unit,evals}/
├── catalog/
│   ├── catalog.yaml              # 10 connector types: 1 available, 9 planned
│   └── playbooks/aws-saas/       # setup steps, source settings, checks, known failures, suggestions.yaml
│                                 #   (ported from .claude/skills/sailpoint-isc-aws-connector)
├── web/                          # Angular app: login, IAM screen, owner screen, catalog, admin
│   └── src/app/{auth,session,catalog,admin,shared}/   # session/: two-thread "Conversations" view per the
│                                 #   design canvas; shared/: thread view, composer with suggestion chips
└── deploy/
    ├── k8s/                      # namespace, mongodb, api, web, network policies
    └── scripts/                  # build, deploy, agent-deploy, tenant bootstrap
```

**Structure Decision**: A new top-level `onboarding/` product directory, separate from the existing MCP-Gateway demo
(`agent/`, `chatbot/`, `servers/`), so the two can evolve and be torn down independently. It reuses the repo's
patterns, not its code paths:
- AgentCore deploy style from `scripts/agent-deploy.sh`.
- The `localhost:5000` registry and image build from `scripts/03-build-docker.sh`.
- The Deloitte tokens from `chatbot/src/styles.css`.
- The `/mcp`-style path prefix on the shared ngrok domain (the edge gains an `/onboarding/` route).

## Complexity Tracking

No constitution violations to justify.

## Revision 2026-10-07: what changes in the built code

The first implementation shipped one shared chat. The revision touches these parts (detail for `/speckit-tasks`):

| Area | Change | Spec |
|---|---|---|
| Data | `messages.thread`, `kind`, `relay_ref`, `relayed_from`; retire `addressed_to`; `sessions.waiting_on`, `check_order`, `suggestions`; `actions.trigger`; one-off migration (R18) | FR-006, FR-006c, FR-016a, FR-006e |
| API | POST message → caller's thread; turn output handling for `other_thread`, `waiting`, `suggestions`; relay-note pairing check; standing check order; `GET /suggestions`; new events `thread.waiting`, `suggestions.updated` | contracts/*.md |
| Agent | Thread tools replace `address`; prompt: answer in the writer's thread, reach the other only via tools, news-for-both rule; role gate adds check tools for owner turns under `check_order`; `suggest_replies` at the end of each turn | agent-invocation.md |
| Catalog | `playbooks/aws-saas/suggestions.yaml` | connector-playbook.md |
| Web | Session screens per the canvas: role panel, "Conversations" with own thread (composer + suggestion chips) and other thread (View only), relay notes, waiting line | US3, US7 |
| Tests | Unit: thread tools, relay pairing, check-order gate, suggestion validation; e2e: quickstart §3a; evals unchanged | SC-010, SC-011 |

## Revision 2026-10-07 (2): scrolling threads and waiting banner

Driven by the updated canvas (spec FR-006f, FR-006g, US3 #7-9, SC-012, SC-013). Design: research R19, R20.

| Area | Change | Spec |
|---|---|---|
| Data | `sessions.waiting_reason` (string?, ≤ 120, masked), cleared with `waiting_on` | FR-006g |
| API | `waiting` output accepts `reason` (mask, cap, one line); `thread.waiting` carries `reason`; session GET returns `waiting_on`, `waiting_reason`; wait auto-clears when the awaited participant's message is answered and no new wait is set | live-events.md, agent-invocation.md, openapi |
| Agent | `set_waiting(on, reason?)`; prompt: always give the awaited person's next step as the reason | agent-invocation.md |
| Web | Thread layout: fixed header, own scrolling log (follow mode, "New messages" button, long content contained), waiting banner component (per-viewer wording, `role="status"`, canvas colours) above composer/view-only note in both threads; stack below ~1100 px; old foot line removed | FR-006f, FR-006g |
| Tests | Unit: reason capping/masking, auto-clear; e2e: banner in 4 places with per-viewer text and clearing (SC-013), scrolling with 200 seeded messages, keep-position + "New messages" (SC-012); smoke `--seed --messages N` | SC-012, SC-013 |

Constitution Check after this design: unchanged, all gates pass (the reason text goes through the same masker; no new
exposure, permission or data path).
