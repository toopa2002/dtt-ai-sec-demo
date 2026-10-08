# ISC Onboarding Agent

Onboard an application into SailPoint Identity Security Cloud with two people and one AI agent:

- the **IAM engineer** orders SailPoint changes;
- the **application owner** follows the setup steps for their application;
- the agent does the SailPoint side and only ever gives the application owner instructions.

Each person has their own **thread** with the agent and sees the other person's thread live, read-only. The agent
answers in the writer's thread, reaches the other person only through their thread (leaving a one-line relay note),
and offers 3–5 **suggested replies** under each message box (picking one fills the box; nothing is sent until Send).
An IAM engineer's order to run the checks stands until they pass, so the owner's "Done, I applied the fix" reruns
them under that order. While the agent waits for someone, a **waiting banner** in every thread says who and their
next step (information only). Each thread's messages scroll in their own area, with a **New messages** jump when
you have scrolled up.

Spec, plan and tasks: [`specs/001-isc-onboarding-agent/`](../specs/001-isc-onboarding-agent/).

## Where things run

| Component | Runs on | Holds |
|---|---|---|
| `web/` — Angular app | local Kubernetes (namespace `onboarding`) | nothing |
| `api/` — session API (FastAPI) | local Kubernetes | sign-in, sessions, the per-session message queue, secret masking, live updates (SSE), audit |
| MongoDB | local Kubernetes (StatefulSet + PVC) | accounts, sessions, transcripts, screenshots, action records |
| `agent/` — AgentCore runtime `isc_onboarding_agent` | AWS AgentCore, ap-southeast-1, Claude Haiku 4.5 on Bedrock | nothing between turns (stateless) |
| SailPoint credentials | AWS AgentCore Identity, one OAuth2 client-credentials provider per tenant | the ISC personal access token |

All user data stays on the local cluster. The agent receives one turn at a time from the API and never touches the
application (no AWS tools).

Connector types are data: [`catalog/catalog.yaml`](catalog/catalog.yaml) plus one playbook per type under
[`catalog/playbooks/`](catalog/playbooks/). AWS SaaS is the first available type.

## Make targets

```bash
make onboarding-images     # build onboarding-api (API + web, one container) into localhost:5000
make onboarding-agent      # deploy the AgentCore runtime + the scoped IAM user for the API
make onboarding-up         # MongoDB, API, web, network policies; /onboarding/ on the shared edge
make onboarding-bootstrap  # first admin account
make onboarding-evals      # SC-005 diagnosis gate on Bedrock (paid; prints the estimate; skipped when unchanged)
make onboarding-e2e        # two-browser end-to-end run against the ISC stub and the scripted model (free)
make onboarding-leak-scan  # MongoDB and logs scanned for keys, tokens and 64-hex secrets (SC-004)
make onboarding-down       # delete the namespace
make onboarding-agent-delete
```

## Run it locally without a cluster

`deploy/scripts/dev.sh` runs everything on this machine against the ISC stub (`deploy/stub-isc/`). By default the
agent calls the real Claude Haiku 4.5 on Bedrock through your AWS credentials (region `ap-southeast-1`); with
`AGENT_MODEL=fake` it uses the scripted model instead (see "What costs money" below):

| Process | Port |
|---|---|
| MongoDB (docker `onb-mongo-test`, one-member replica set) | 27018 |
| ISC stub | 8099 |
| agent (`python -m onboarding_agent.main`) | 8092 |
| session API | 8080 |
| web (`ng serve`) | 4300 → <http://127.0.0.1:4300/onboarding/> |

```bash
make onboarding-dev                       # start (NO_WEB=1 skips the web server); dev.sh stop | status
uv run --project onboarding/api python onboarding/deploy/scripts/smoke.py --scenario happy   # or trust | confirm
make onboarding-e2e                       # scripted model: Playwright specs, then the leak scan (no Bedrock calls)
REAL_MODEL=1 make onboarding-e2e          # the same specs on real Claude Haiku (prints the estimate first)
onboarding/deploy/scripts/evals.sh        # quick eval read: 3 runs × 10 failures × text/screenshot (~300 calls, ~$1)
make onboarding-evals                     # the SC-005 gate: 10 runs each, ≥ 9/10 (~1,000 calls, ~$4); skipped when unchanged
make onboarding-leak-scan                 # dev stack by default; deploy/scripts/leak-scan.sh cluster for the cluster
```

### What the session screens do (2026-10-08)

- **Status replies**: every message gets the agent's reply at once, first as a status ("Received… yours is next",
  then "Working on it: …"), which the answer replaces in the same bubble (FR-006h).
- **Plan**: one shared plan of the whole onboarding on both screens, seeded from `catalog/playbooks/<id>/plan.yaml`
  and kept current by the agent (`update_plan`); the status chips are derived from it (FR-008a-c).
- **Owner's view**: the application owner sees only their own thread; the IAM engineer's opens on request (FR-006).
- **Sync by time**: the IAM engineer's two threads scroll together with shared minute dividers (FR-006i).
- **Action details**: each SailPoint action shows its outcome and opens to the request, response and diagnosis; IAM
  engineer only (FR-020a).
- **Admin → Sessions**: reopen a finished session or hand a place over to another person (FR-032, FR-033).

### What costs money

Constitution Principle IV (cost-bounded AI use). Every real Claude Haiku call on Bedrock is paid; the agent caches its
static prompt (rules, playbook, tool definitions) so a call costs about $0.004 instead of about $0.01.

| Command | Bedrock calls | Rough cost |
|---|---|---|
| `make onboarding-e2e`, `smoke.py` against `AGENT_MODEL=fake` | none (scripted model) | $0 |
| `REAL_MODEL=1 make onboarding-e2e` | about 40 per spec | about $1.50 for the suite |
| `smoke.py --scenario happy\|trust\|confirm` against a real agent | 15-30 | under $0.15 |
| `deploy/scripts/evals.sh` (3 runs) | about 300 | about $1.20 |
| `make onboarding-evals` (gate, 10 runs) | about 1,000, or none when unchanged | about $4 |
| Using the app (dev stack or cluster) | about 3-6 per message | about $0.02 per message |

`evals.sh --estimate` prints the estimate without calling the model; every paid command prints it before it starts and
the eval runs print the actual calls, tokens and cost at the end. The API logs `model_usage` per turn and totals it on
`/onboarding/api/healthz/metrics`.

**Budget alarm** (for the AWS account owner; no script here changes billing): AWS Billing → Budgets → Create budget →
Cost budget, monthly, amount e.g. USD 30, filter Service = Amazon Bedrock (and "Claude Haiku 4.5 (Bedrock Edition)"),
alert at 80% actual and 100% forecast to your email.

`smoke.py` creates the accounts `smoke.admin`, `smoke.iam` and `smoke.owner` (password `smoke-password-123`) in the
dev database, registers the stub tenant and opens a session. Sign in with them on the web dev server to look around.
The stub's failure switches: `POST :8099/_stub/trust_broken`, `/_stub/fix_trust`, `/_stub/foreign_source?name=…`,
`/_stub/reset`.

Validation guide: [`specs/001-isc-onboarding-agent/quickstart.md`](../specs/001-isc-onboarding-agent/quickstart.md).
