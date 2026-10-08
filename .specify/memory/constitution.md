# DTT AI Security Demo Constitution

## Core Principles

### I. Secrets Stay in Their Vault (NON-NEGOTIABLE)

- Credentials (client secrets, tokens, API keys, passwords) MUST live only in their secret store: SSM SecureString,
  AgentCore Identity or a Kubernetes Secret.
- They MUST NOT appear in source code, git history, databases, prompts sent to a model, logs, live events, screens,
  or published artifacts (videos, pages, documents). Passwords are stored only as strong one-way hashes.
- Every text that reaches a user, a model or a log MUST pass through the product's one masker.
- A leak scan MUST pass before every rollout of a product that handles credentials.

Rationale: this repository demonstrates AI security controls; a leaked secret defeats the demonstration itself.

### II. Least Privilege and Gated Exposure

- Each hop (browser → API, API → agent, agent → tool or SaaS) MUST hold only the permission that hop needs, scoped to
  the single resource it calls.
- A component MUST be reachable only through its gate: cluster-internal by default, NetworkPolicies admitting only
  the intended caller, no LoadBalancer or NodePort, and one public route per product on the shared tunnel.
- Who may do what MUST be enforced in code or infrastructure (role checks, tool gating, API authorization), never
  only by a prompt or by hiding something in the UI.

Rationale: an AI agent follows instructions it is given; only enforced boundaries hold when it is wrong or misled.

### III. Human Identity End to End

- An action taken on a person's behalf MUST carry that person's identity to the system it changes (for example the
  user's on-behalf-of token for MCP tools, or the ordering user on an onboarding action).
- Every change an agent makes to an external system MUST have an audit record naming who ordered it, when, and the
  result.

Rationale: shared service credentials hide who did what; the audit trail must not.

### IV. Cost-Bounded AI Use

- Automated tests and CI MUST NOT call paid models by default. Unit, integration and end-to-end runs use stubs or
  scripted fakes; runs against a real model (evals, real-agent end-to-end, smoke tests) MUST be explicit opt-in
  commands.
- Every command that calls a paid model MUST state, in its help text or start banner, its expected number of model
  calls and approximate cost.
- The default size of such a command MUST be the smallest that answers its question: routine evals use a few runs
  per case; the full statistical gate runs only when prompts, playbooks, tools or the model change.
- Static context sent to a model on every call (system prompt, playbook, tool definitions) MUST use the provider's
  prompt caching where the provider supports it.
- A full paid suite MUST NOT be run again when nothing that affects its result has changed. Whoever runs one, an AI
  coding agent included, MUST state the estimate first.
- Each cloud account that runs models SHOULD have a budget alarm on model spend.

Rationale: on 2026-10-07, repeated full eval and end-to-end runs cost about $36 in one day, roughly 25 times normal
use, because every test call went to the real model with about 9,000 uncached input tokens.

### V. Reproducible, Verified Delivery

- Builds and deploys MUST go through idempotent `make` targets and scripts in this repository; no hand-made cluster or
  cloud changes that the scripts do not reproduce.
- Every change MUST ship with tests that run locally, and every rollout MUST be followed by the product's checks
  (tests, leak scan, health or smoke check).
- Specifications, plans and task lists MUST be updated before the code they describe (the Spec Kit flow).

Rationale: demos are rebuilt often and on different machines; only scripted, checked steps rebuild the same thing.

## Security and Data Constraints

- Real tenant names, real people and customer data MUST NOT appear in demos, videos, published artifacts or commits;
  use fictional stand-ins.
- Public tunnels MUST expose only the gated routes of each product (for example `/mcp` and `/onboarding/`).
- Stored conversation and session data follow the retention each specification sets (for example 90 days for
  onboarding session history); audit records follow the organization's audit retention.

## Development Workflow and Quality Gates

- Features follow the Spec Kit flow: specify → clarify → plan → tasks → implement.
- Each `plan.md` MUST include a Constitution Check against Principles I–V, before research and again after design.
- Before a merge: unit and integration tests pass, lint is clean, and the leak scan passes.
- Paid-model gates (evals) run only under the conditions of Principle IV.
- A deliberate deviation from a principle MUST be recorded and justified in the plan's Complexity Tracking table.

## Governance

- This constitution takes precedence over other practices in this repository.
- Amendments are made by a change to this file that includes a Sync Impact Report and updates any plan or template
  that the change affects.
- Versioning follows semantic versioning: MAJOR when a principle is removed or redefined, MINOR when a principle or
  section is added or materially expanded, PATCH for wording and clarifications.
- Every `/speckit-plan` run re-checks compliance; reviewers check compliance on each pull request.
- Runtime guidance for working in the repository lives in `README.md` and `docs/DEMO-GUIDE.md`.

**Version**: 1.0.0 | **Ratified**: 2026-10-08 | **Last Amended**: 2026-10-08
