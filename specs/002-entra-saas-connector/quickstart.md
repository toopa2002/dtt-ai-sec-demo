# Quickstart: validate the Microsoft Entra ID connector type (spec 002)

This guide proves the feature works. Contracts and fields are in [contracts/](contracts/) and
[data-model.md](data-model.md). The 001 setup ([001 quickstart](../001-isc-onboarding-agent/quickstart.md) §1–2) is
assumed: cluster, images, agent runtime, admin, one usable ISC tenant, one IAM engineer and one application owner.

**Cost (Constitution IV):**
- §1–3 and §5 are free: ISC stub and scripted model.
- §4 uses a real Entra tenant, a real ISC tenant and the real model. That is about 30–60 Claude Haiku calls, about
  $0.50–$1, and the command prints the estimate first.
- §6 (evals) is opt-in and prints its estimate: 11 cases × 2 modes × 3 runs ≈ 330 calls, about $1.30 (`--mode text`: about $0.65); setup checks about 60 calls, about $0.25.

## 1. Unit and integration tests (free)

```bash
(cd onboarding/api && uv run pytest)      # API unit + integration
(cd onboarding/agent && uv run pytest)    # agent unit; includes the AWS regression checks below
```

Expect these to pass:
- **AWS unchanged (SC-106):**
  - The legacy path table snapshot.
  - AWS `offered_tools` for each role and state.
  - The AWS static prompt hash.
  - The AWS eval fingerprint unchanged.
- **Field rules:** `capabilities` (directory always on; AI agents need subscriptions; provisioning needs its warning
  accepted).
- **Secret field:**
  - A GUID is rejected with "this is the secret's ID, not its Value".
  - The expiry date is required, at most 2 years ahead.
  - The IAM engineer gets 403.
- **Masker:** a `Q~`-format Entra secret is masked in text, and the existing masker tests pass unchanged.
- **Agent tools:**
  - The secret is injected only in tool code; the action shows `[vaulted]` and the tool result
    `secret_applied: true`.
  - The `X-SailPoint-Experimental` header is sent only on the listed paths, and every Entra call is under `/v2026/`.
  - Delta is restored after a failing aggregation.
  - A dataset 404 "endpoint is unavailable" gives `tenant_limitation`.
  - `adopt_source` refuses a foreign owner.
- **Follow-up:** running → following → done, which queues a continuation turn as the check-order IAM engineer; the
  step shows pending at 30 min.

## 2. Two-browser end to end on the stub (free)

```bash
make onboarding-e2e           # adds e2e/entra-onboarding.spec.ts and e2e/entra-secret-leak.spec.ts
```

Scenarios, all on the scripted model against `stub_isc.py` `/v2026` routes:

1. **Directory only (US1, US2).**
   1. Catalog: Microsoft Entra ID shows as available (FR-101).
   2. A new session with `tenant_domain=contoso.example` shows the custom-domain note; `not a domain` is rejected
      with an example.
   3. The owner's plan has no write permission and no directory role (SC-105).
   4. The owner pastes a GUID into the secret field and it is rejected. They paste the stub secret with an expiry
      date: both screens show "secret received · expires …".
   5. The IAM engineer orders the connector. The plan runs create → configure → connection check → Test Connection
      → entitlements → accounts, and the report shows users and entitlements counts.
   6. After Test Connection passes, the status chip reads "vault copy deleted" (FR-125).
2. **Secret in chat (US2-3).** The owner sends a `Q~` secret in their thread. It is masked in both threads and in
   the agent's input, and the agent asks for a new secret through the field.
3. **Service principals (US3).** With `service_principals`, `sp_schema` adds only missing attributes (stub: two
   exist, so two fewer are sent), and the report shows users and service principals separately.
4. **AI agents and fallback (US4).**
   1. With `ai_agents` and `_stub/entra/dataset_unavailable`, the Foundry step is `blocked` with the ISC UI path.
   2. The IAM engineer replies "done", the agent re-checks the count, and the step is done.
   3. The schedule is on.
   4. Without the switch: the dataset aggregates, and the count and schedule are reported.
5. **Provisioning (US5).**
   1. Save is refused until the warning is accepted.
   2. The policy and correlation are set.
   3. A pre-existing stub policy is kept.
   4. The proof makes no directory write: the stub records no account create.
   5. Lifecycle actions appear for review only.
6. **Long aggregation (FR-139).** With `/_stub/entra/slow_aggregation?polls=N` (the API test drives the clock: `followups.check_once(now)`): the
   step shows "pending · 30 min". The result note arrives in the IAM engineer's thread with nobody's message, and
   the proof continues.
7. **Extend-source (FR-105).**
   1. Start a second session for the same tenant: the agent names the existing source and owner, and offers to
      extend it.
   2. Accept with `service_principals`. Only the SP steps remain, and no new secret is asked for.
   3. `delete_session_source` is refused.
8. **Rotation (FR-122).** With a standing check order, the owner submits a new secret: it is applied, peek and
   Test Connection rerun, and the old provider is gone.
9. **Leak check (SC-102).** `entra-secret-leak.spec.ts` searches MongoDB, events, API and agent logs and attachments
   for the seeded secret and its fingerprint. Expect zero hits, except the stub's PATCH body for `clientSecret`, which
   must equal the submitted value exactly.
10. **AWS unchanged (SC-106).** The existing AWS e2e specs pass with no edits.
11. **Canvas checks** (the 002 artboards on the "ISC Onboarding Agent UI" canvas):
    - **Catalog:** two available cards; Entra has capability chips with provisioning tagged "writes"; the header
      reads "2 available · 8 planned".
    - **New session:**
      - Start session stays disabled until the provisioning warning is ticked.
      - The Foundry and provisioning fields appear only inside their chosen capability.
      - The side panel's permission counts equal the entries in `permissions/*.json`.
      - The Extend radio leads to the agent naming the tenant's existing source.
    - **Entra administrator:**
      - The secret card sits above the thread and shows the inline GUID error.
      - The status strip moves Not received → Received → In SailPoint → Vault copy deleted.
      - A `Q~` secret pasted in chat shows `[masked]` and the red "treat it as exposed" note, with no model call
        for the note.
    - **IAM engineer:**
      - The "What SailPoint now sees" tiles match the stub's counts, and AI agents shows "Start it in ISC" under
        `dataset_unavailable`.
      - The secret card shows "vault copy deleted".
      - Action rows show their summary line.
      - The header chips put Test Connection before Aggregation, while AWS sessions keep their order.
      - Collapsing the owner thread shows "N new".

## 3. Leak scan and smoke (free)

```bash
make onboarding-leak-scan                       # now includes the Entra secret pattern
python onboarding/deploy/scripts/smoke.py --scenario entra-directory   # scripted model, stub ISC
```

## 4. Real tenants (opt-in, paid; required before release for provisioning, 001 FR-028)

Use a **test** Entra tenant and a **test** ISC tenant, with fictional names in anything recorded.

1. Set the ISC tenant up as in 001 (admin → tenants → Check now = usable).
2. Start an Entra session with `directory, service_principals, ai_agents, provisioning`, with one Foundry
   subscription that has at least one agent.
3. As the Entra administrator (Global or Privileged Role Administrator), follow the steps in the owner thread. They
   should match the skill's `entra-setup.sh` dry-run for the same profiles (`--plan-only`).
4. Order the connector. Expect:
   - connection check and Test Connection SUCCESS;
   - entitlement and account aggregation SUCCESS, with user and service principal counts equal to
     `az ad user list --query 'length(@)'` and `az ad sp list --filter "servicePrincipalType eq 'Application'" --all
     --query 'length(@)'` (SC-104);
   - the Foundry count equal to the agents in the subscription, or the tenant-limitation path;
   - the provisioning policy and correlation present;
   - the vault copy deleted.
5. Check CloudTrail: `GetResourceApiKey` for `onboarding-entra-<session>` from the agent role only, then
   `DeleteApiKeyCredentialProvider` from the API user.
6. Clean up afterwards: delete the ISC source, then delete the Entra app registration with
   `az ad app delete --id <appId>` and the Azure role assignments.

## 5. Teardown (free)

```bash
make onboarding-agent-delete   # also deletes leftover onboarding-entra-* API-key providers
```

## 6. Diagnosis evals (opt-in, paid)

```bash
cd onboarding/agent
uv run python tests/evals/run_evals.py --suite entra_failures --estimate   # ~330 calls, ~$1.30; then without --estimate
uv run python tests/evals/run_evals.py --suite entra_failures --gate       # 10 runs; skipped unless the Entra fingerprint changed
```

`--suite` is new: today `run_evals.py` reads only `aws_saas_failures`, and the default stays that suite. The gate
fingerprint is kept per suite, so an Entra change doesn't re-run the AWS gate. Expect each of E1–E11 to be diagnosed with the correct cause and side in ≥ 9/10 runs at the gate (SC-103).
