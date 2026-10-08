# Quickstart run: real demo tenant (T084)

**Tenant**: the real SailPoint demo tenant (identitynow-demo.com), status **usable**
**Cluster**: local k3d, namespace `onboarding`; public URL `https://<NGROK_DOMAIN>/onboarding/`
**Agent**: AgentCore runtime `isc_onboarding_agent`, version 6 (thread model, standing check order, suggestions)

Status: **Pass**, **Fail**, **Partial** (some evidence, not the full step), **Open** (not run yet).

## Results

| § | Step | Status | Evidence / notes |
|---|---|---|---|
| 1 | Deploy (images, agent, up, bootstrap) | Pass | 2026-10-07: `mongodb-0`, `onboarding-api`, `onboarding-web` Running; `/onboarding/` returns 200 and `/onboarding/api/healthz` returns `{"ok":true}` through ngrok. |
| 2.1–2.2 | Admin signs in, creates accounts | Pass | Users `admin` (iam_engineer) and `aws.owner` (application_owner). |
| 2.3 | Add tenant, PAT pasted once | Pass | Status usable, External ID shown. The tenant record keeps only `credential_provider` and `credential_hint` (no secret field). Needed the AgentCore Identity IAM fix and the standalone workload identity `isc-onboarding-agent`. |
| 2.4 | Swapped ID and secret give 422 | Open | |
| 2 | Leak checks (tenant record, API logs) | Pass | No secret field on the tenant record; `leak-scan.sh cluster` found no secrets in MongoDB or the API logs. |
| 3.1–3.2 | Start AWS SaaS session, owner joins; owner refused the IAM screen | Partial | 5 sessions created on the cluster; the owner screen redirect is covered by e2e only. |
| 3.3–3.4 | Owner gets commands, runs them in AWS, "AWS role ready" passes | Open | Earlier sessions reached `application_ready: in_progress` only. |
| 3.5 | IAM orders create plus checks; 5 action records name the IAM engineer | Partial | One session reached `source_created` passed and `configured` passed, then `connection_check` failed (the AWS role was not ready yet). |
| 3.6 | Two messages within 1 s: second is queued | Open | Covered on the stub by e2e `shared-session.spec.ts`. |
| 3a.1–3a.6 | Threads, relay notes, waiting, rerun on owner confirmation, suggestions | Open | On the dev stack with the ISC stub: e2e `threads.spec.ts` and `suggestions.spec.ts` pass; smoke `--scenario confirm` passed 5/5. |
| 4.1–4.4 | Broken trust, diagnosis, screenshot, fix, rerun | Open | |
| 4.5 | Diagnosis evals (FR-024) | Pass | `make onboarding-evals`: 20/20 cases at 10 runs each. |
| 5 | Guardrails table | Open | Covered on the stub by API integration tests (41 pass) and e2e. |
| 5 | Message leak check (`AKIA…`, `eyJ…`) | Pass | 0 matching messages in the cluster DB. |
| 7 | Teardown | Not run | Keep the stack for the demo. |

## Issues found during the run

- Adding a tenant failed until the IAM policy allowed `bedrock-agentcore:CreateOauth2CredentialProvider` on
  `token-vault/default/oauth2credentialprovider/*`. The live policy is fixed; `onboarding/deploy/scripts/agent-deploy.sh`
  still writes the old resource and must be updated before the next `make onboarding-agent`.
- The runtime's own workload identity is service-linked and refuses `GetWorkloadAccessToken`; a standalone workload
  identity `isc-onboarding-agent` is now created by `agent-deploy.sh`.
- A Docker Desktop restart stops the `:8091` port-forward loop, the gateway port-forward and ngrok. Recover with
  `scripts/tunnel.sh start` and the port-forward step of `onboarding/deploy/scripts/up.sh`.

## To finish T084

Run §3 (from step 3), §3a, §4 (steps 1–4) and the §5 table with two browsers on the public URL, `admin` as **A**
and `aws.owner` as **B**, then fill in the Open rows above.
