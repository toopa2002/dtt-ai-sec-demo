# Implementation Plan: SailPoint ISC Application Onboarding Agent

**Branch**: `001-isc-onboarding-agent` | **Date**: 2026-10-07 (revised 2026-10-07: threads per person, suggested
replies; scrolling threads and waiting banner; revised 2026-10-08: status replies, shared plan, owner's own-thread view,
time-synced threads, action details, admin reopen and handover; revised 2026-10-08 (2): constitution v1.0.0, cost-bounded
AI use) | **Spec**: [spec.md](spec.md) | **Design**: canvas
"ISC Onboarding Agent UI" (artboards IAM engineer, application owner, action details, admin)

**Input**: Feature specification from `specs/001-isc-onboarding-agent/spec.md`

## Summary

A two-actor web app (IAM engineer, Application owner) with **one thread per person** in each onboarding session (each
person writes in their own thread; the IAM engineer also sees the owner's thread live, view only, scrolling in step by
time, while the owner sees only their own with the other one hidden until opened; each thread scrolls in its own area
under a prominent waiting banner), suggested replies in each own thread, an instant status reply to every message, one
shared **plan** of the whole onboarding on both screens, SailPoint action details for the IAM engineer, admin reopen and
handover of sessions, and an
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
- **2026-10-08 revision**: every message gets its agent reply at once as a system-written status that the answer
  later replaces (R21); the plan is server-owned, seeded from a new `plan.yaml` per playbook, changed by the agent's
  `update_plan` tool, and the six milestones are derived from it (R22); admins reopen and hand over sessions through
  the existing participant check (R23); action records carry masked request/response details, IAM engineer only
  (R24); the owner's hidden thread and the time-synced scrolling are browser-only (R25, R26).
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

**Testing** (no paid model calls by default, Constitution IV): pytest + respx (ISC API mocked) + mongomock-motor or a throwaway Mongo container; Angular unit tests (vitest via `@angular/build`); Playwright two-browser end-to-end (IAM engineer + application owner); recorded-transcript evals for the agent's diagnosis (SC-005)

**Target Platform**: local Kubernetes (k3s / Docker Desktop Kubernetes, `localhost:5000` registry) + AWS AgentCore Runtime (ap-southeast-1) + Amazon Bedrock

**Project Type**: web application (frontend + backend API) plus a hosted agent

**Performance Goals**: relay a message or status change to the other screen in ≤ 2 s p95 (SC-003); first streamed agent words in ≤ 5 s p95 (SC-003a); refreshed suggestions within 1 s of a reply finishing (SC-011, no extra model call); status reply within 1 s of sending (SC-014); plan change on both screens within 2 s (SC-015); synced scroll within one message, one animation frame (SC-018)

**Constraints**:
- No secrets in MongoDB, logs, prompts or the browser (FR-025–FR-027).
- The agent never acts on AWS (FR-010).
- One queue per session across both threads (FR-006a); writes always land in the caller's own thread (FR-006).
- The agent never decides where its streamed reply goes; only its thread tools reach the other thread (FR-006c).
- Suggestions never send by themselves and never offer the application owner a SailPoint order (FR-006e).
- English UI text is externalised for later Thai.
- Desktop browsers only; below ~1100 px wide the two threads stack and time sync is off (research R19, R26).
- Every participant message gets its reply bubble in the same write (FR-006h); status text costs no model call (R21).
- The milestones are derived from the plan, never set separately (FR-008c, R22).
- Action details are the IAM engineer's only, enforced by the API, not just hidden (FR-020, R24).
- Reopen and handover are admin-only and audited; a handover never cuts a running answer (FR-032, FR-033, R23).
- Model cost (Constitution IV): every Bedrock call uses prompt caching (R27); automated tests use the scripted model
  and real-model runs are opt-in with a printed estimate (R28); the eval gate runs only when its inputs change (R29).

**Scale/Scope**: a team: ≤ 50 accounts, ≤ 10 concurrent sessions, ≤ 2 participants per session; 1 available connector type, 9 planned

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Gated on the ratified constitution, **v1.0.0** (`.specify/memory/constitution.md`, 2026-10-08), Principles I-V:

| Principle | How this design meets it | Pre | Post |
|---|---|---|---|
| **I. Secrets stay in their vault** | ISC credential only in AgentCore Identity (R4); passwords argon2id; one masker on API ingress, agent output, action request/response, plan and status text, and logs (R9, R24); leak scan before each rollout | ✅ | ✅ |
| **II. Least privilege and gated exposure** | Browser → API (session cookie); API → AgentCore (`InvokeAgentRuntime` on one runtime); agent → ISC (per-tenant M2M token, source-admin scope). MongoDB cluster-internal with a NetworkPolicy; one public route `/onboarding/`. Who may change SailPoint is enforced by the role gate (R16), view-only threads and IAM-only action records by the API (R15, R24), reopen and handover by the admin check (R23) | ✅ | ✅ |
| **III. Human identity end to end** | Every turn carries the ordering user; every ISC change has an action record naming them (FR-020, SC-006); every reopen and handover is audited with the admin (R23) | ✅ | ✅ |
| **IV. Cost-bounded AI use** | Before this revision: e2e and smoke ran on real Bedrock by default, evals defaulted to 10 runs, no prompt caching — **❌ (2026-10-07: ~$36 in a day)**. Design: prompt caching on tools, static system and rolling conversation (R27); scripted model for automated tests, real model opt-in with a printed estimate (R28); evals default 3 runs, gate 10 only when its fingerprint changes, estimate first and actual cost after (R29); budget alarm recommended to the account owner | ❌ | ✅ |
| **V. Reproducible, verified delivery** | `make onboarding-*` targets and idempotent scripts; tests per change; rollout followed by tests, leak scan and smoke (now free, R28); spec → plan → tasks before code | ✅ | ✅ |

Principle IV failed before design and passes after it (Revision 2026-10-08 (2) below), so Complexity Tracking stays
empty.

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
│   │   ├── auth/                 # local accounts, argon2, lockout, idle timeout, admin flag; admin sessions (reopen, handover)
│   │   ├── sessions/             # sessions, participants, status steps, history; plan.py (seed, ops, milestones)
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
│   │   ├── loop.py               # Claude Haiku tool loop, prompt caching and per-turn usage (R27)
│   │   ├── fake_model.py         # scripted model for the dev stack and tests (R28)
│   │   ├── isc/                  # ISC REST client + generic source tools
│   │   ├── tools/session.py      # thread tools: post_to_other_thread, notify_other_thread, set_waiting, suggest_replies
│   │   ├── playbooks.py          # loads catalog/playbooks into prompts and tool args
│   │   └── prompts/en/           # system prompts (Thai later: prompts/th/)
│   └── tests/{unit,evals,fake_model}/   # fake_model/script.yaml: the scripted replies (R28)
├── catalog/
│   ├── catalog.yaml              # 10 connector types: 1 available, 9 planned
│   └── playbooks/aws-saas/       # setup steps, source settings, checks, known failures, suggestions.yaml, plan.yaml
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

## Revision 2026-10-08: status replies, plan, owner view, timeline sync, action details, reopen and handover

Driven by the spec's 2026-10-08 sessions (FR-006, FR-006h, FR-006i, FR-008a-c, FR-020, FR-020a, FR-031-FR-033; US3
#10-16, US8; SC-014-SC-018) and the updated design canvas. Design: research R21-R26.

| Area | Change | Spec |
|---|---|---|
| Data | `messages.reply_to`, `reply_state`, `ahead`, `status_text`, `kind: system_note`; `sessions.plan` (PlanStep[]), `reopened_at`, `handovers`, `pending_handover`; `actions.action_ref`, `order_message_id`, `request`, `response`, `diagnosis`, `outcome`, `started_at`, `duration_ms`; audit kinds `session_reopened`, `session_handover`; one-off migration seeding plans for existing sessions | data-model.md |
| Catalog | `playbooks/aws-saas/plan.yaml` (starting plan, ported from the skill's step order); `checks.yaml` names the plan step each milestone marks | connector-playbook.md |
| API | Message POST creates the reply placeholder; queue worker keeps `ahead` and `reply_state` current and writes the answer into the placeholder; plan seeding, `plan` output validation, milestone derivation; `action` upsert by `action_ref`, `diagnosis`, outcome line, IAM-only actions endpoints and events; `GET /admin/sessions`, reopen, handover (deferred while a turn runs), stream re-check and `access.revoked` | openapi, live-events.md, agent-invocation.md |
| Agent | `update_plan(ops)` and `note_diagnosis(text)` tools; ISC tools emit `action_ref`, masked `request`/`response`, `running` then final for aggregation; `record_application_step` retired; prompt: keep the plan current, answer "what is left?" from it | agent-invocation.md |
| Web | Reply bubble with status (received/working/failed) replacing the queued label; Plan panel on both screens (count, progress, owner tags, added/blocked reasons, "Your next step" card for the owner); header chips from derived milestones; owner screen: own thread full width + collapsed IAM thread bar, no actions panel; IAM screen: minute dividers and "Sync by time" toggle; action rows with outcome and a details dialog (request, response, diagnosis, context, copy, Esc); finished read-only state; Admin → Sessions with Reopen and Hand over | canvas, US3, US8 |
| Tests | Unit: placeholder lifecycle and `ahead`, plan op validation and milestone derivation, action upsert and outcome, handover target rules and deferral, owner 403 on actions; e2e: quickstart §3b steps 1-7 (status reply, plan, owner view, sync with 120 seeded messages, action dialog, reopen, handover); evals: "what is left?" matches the plan | SC-014-SC-018 |

Constitution Check after this design: all gates still pass (new row above). No new external exposure; the admin
endpoints sit behind the existing admin check, and every new text field goes through the masker.

## Revision 2026-10-08 (2): constitution v1.0.0 compliance (cost-bounded AI use)

Driven by the ratified constitution (Principle IV) after the 2026-10-07 Bedrock spend (3,959 Claude Haiku calls,
34.7 M uncached input tokens, about $36, mostly three full eval runs). No spec change: the behaviour users see is the
same. Design: research R27-R29.

| Area | Change | Principle |
|---|---|---|
| Agent | System prompt split into static and dynamic blocks; cache breakpoints on the last tool, the static block and the conversation each round; per-turn `usage` event; `fake_model.py` + `tests/fake_model/script.yaml`, selected by `AGENT_MODEL=fake` only against the local ISC stub | IV |
| API | Record the `usage` event as metrics (`model_calls`, `model_input_tokens`, `model_cache_read_tokens`, `model_output_tokens`) | IV, observability |
| Scripts | `dev.sh` takes `AGENT_MODEL` (default `bedrock` for interactive dev); `e2e.sh` and `smoke.py --scenario` use `fake` unless `REAL_MODEL=1`, and print the estimate when it is; `evals.sh` / `run_evals.py`: default 3 runs, `--gate` (10), `--estimate`, cost report, fingerprint skip with `--force`; Makefile help texts say which targets call a paid model | IV, V |
| Tests | Unit: cache breakpoints present and ordered, usage summing, fake model refused off-stub, fingerprint changes on prompt edits; e2e: all specs pass on the scripted model; one real-model run (`REAL_MODEL=1`) shows cache reads in the usage metrics | IV |
| Docs | README and DEMO-GUIDE: which commands cost money and roughly how much; how to set a Bedrock budget alarm (for the account owner) | IV |

Constitution Check after this design: Principle IV ✅; the other principles unchanged.
