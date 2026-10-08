# Quickstart: validate the ISC Onboarding Agent end to end

This guide proves the feature works. It references the contracts rather than repeating them. The make targets below
are the ones `tasks.md` will create.

## Prerequisites

- **Local cluster** running: Docker Desktop Kubernetes on macOS, or k3s on WSL, with the `localhost:5000` registry.
  Check with `kubectl get nodes`.
- **AWS** credentials for ap-southeast-1 with Bedrock access to Claude Haiku 4.5 (the same account as `make agent`).
- **A SailPoint ISC tenant** (a demo tenant is fine) and a personal access token with source-admin rights.
- **An AWS management account** where an application owner can run the CLI commands the agent gives (or use the
  stub path below).

## 1. Deploy

```bash
make onboarding-images     # build onboarding-api (API + web build) into localhost:5000
make onboarding-agent      # AgentCore runtime isc_onboarding_agent + IAM user onboarding-api (scoped invoke)
make onboarding-up         # namespace, MongoDB (1-member replica set), api, web, network policies, edge route
make onboarding-bootstrap  # creates the first admin from a one-time secret; prints its username
```

Expected:
- `kubectl -n onboarding get pods` shows `mongodb-0` and `onboarding-api-*` Ready.
- `https://<NGROK_DOMAIN>/onboarding/` shows the login page.

## 2. Admin setup (US6, FR-002, FR-025)

1. Sign in as the bootstrap admin.
2. Go to **Admin → Accounts** and create `iam1` (IAM engineer) and `owner1` (Application owner).
3. Go to **Admin → Tenants** and add the tenant. Paste the PAT once.
   - Expected: status **usable** and the External ID shown.
   - The secret is never shown again; the API returns only `credential_hint`.
4. Swap the client ID and secret on purpose. Expected: 422, "ID and secret look swapped".

Leak checks:

```bash
kubectl -n onboarding exec mongodb-0 -- mongosh onboarding --quiet --eval 'db.tenants.findOne()'   # no secret field
kubectl -n onboarding logs deploy/onboarding-api | grep -c "<the PAT secret>"                          # 0
```

## 3. Two-person happy path (US1–US3, SC-001–SC-003a)

Open two browsers (or a normal and a private window): **A** signed in as `iam1`, **B** as `owner1`.

1. **A**: Catalog → **AWS SaaS** → Start a session.
   - Fill the details and invite `owner1`.
   - Picking a planned type instead shows "not available yet" (US5 #3).
2. **B**: the session appears and opens on the application owner screen. **B** cannot open the IAM screen (403).
3. **B**: "What do I need to do?"
   - Expected: commands with no placeholders, each tagged read-only or change (SC-002), in **B**'s own thread.
   - **A** sees them in **B**'s thread (view only) on A's screen within 2 s (SC-003). A's own thread is unchanged.
4. **B** runs the commands in AWS and pastes outputs back.
   - Expected: the "AWS role ready" step turns **passed** on both screens.
5. **A**: "Create the connector, then run the checks."
   - Expected: first words within 5 s (SC-003a).
   - Progress lines appear while it works.
   - Steps `source_created`, `configured`, `connection_check`, `aggregation`, `test_connection` pass.
   - Five records appear under SailPoint actions, each naming `iam1` (SC-006).
6. **A** and **B** each send a message within 1 s of each other.
   - Expected: the second shows **queued** in its thread on both screens and is answered after the first (FR-006a).

## 3a. Threads, relay and suggestions (US3, US7, FR-006–FR-006g, FR-016a, SC-010–SC-013)

1. Each screen shows "Conversations" with two threads: the viewer's own (message box, suggestions) and the other
   participant's, marked **View only**, with no message box and no suggestions (FR-006, FR-006e).
2. With a failed connection check (section 4, step 2): the agent's reply is in **A**'s thread; its request for the
   read-only `get-role` check is in **B**'s thread; **A**'s thread has a one-line relay note saying what it asked of
   **B** (FR-006c, SC-010). A's thread says the agent is waiting for the application owner.
3. While the agent waits for **B**, **A** asks "What is left to do?". Expected: answered at once, not held (FR-006a).
4. **B** confirms the fix ("Done, I updated the trust"). Expected: the agent reruns the connection check, aggregation
   and Test Connection **without a new order from A**; the action records name `iam1` with trigger "application owner
   confirmation"; the results are in A's thread, with a relay note in B's (FR-016a). **B** asking "delete the
   source" is still refused (FR-019).
5. **B** asks the agent to "tell the IAM engineer I'm on a call for 10 minutes". Expected: an agent message in A's
   thread, attributed to B, and a relay note in B's thread (edge case "tell the other person").
6. Suggestions (US7, SC-011): after every agent reply each own thread shows 3-5 suggestions within 1 s.
   - Before a source exists, **A**'s list includes "Create the connector and run the checks".
   - After the agent asks **B** for command output, **B**'s list includes a starter "Here is the output:".
   - **B**'s list never contains a SailPoint order.
   - Picking a suggestion only fills the message box; nothing is sent until Send.
   - In a full run, both participants can reach a working source using only suggestions plus pasted output.
7. Waiting banner (FR-006g, SC-013): at step 2, both threads on both screens show an amber banner with a clock icon
   between the messages and the message box (or view-only note). On **B**'s screen it reads "Waiting for you:" plus
   the next step; on **A**'s screen "Waiting for <B's name>:" plus the same step. It never says messages are held.
   After **B** confirms (step 4) the banner disappears from all four places within 2 s.
8. Scrolling (FR-006f, SC-012): seed 200 messages per thread (`smoke.py --seed --messages 200`). Each thread's
   messages scroll inside the thread with a visible vertical scrollbar; the page does not scroll; the banner,
   suggestions and message box stay visible. Scroll **A**'s own thread up, then have **B** send a message: **A**'s
   position is kept and a "New messages" button appears; pressing it jumps to the newest. Paste a 200-line command
   output: it scrolls inside its message, the thread does not widen.

## 4. Troubleshooting loop (US4, SC-005)

1. Break the trust on purpose: **B** removes the demo principal 706944607044 from the role trust (or uses a wrong
   External ID).
2. **A**: "Rerun the connection check."
   - Expected: `connection_check` **failed**.
   - The agent names the AWS trust as the cause, then gives **B** a read-only `get-role` command, then the fix.
3. **A** uploads a screenshot of the ISC error page. Expected: the same diagnosis as for pasted text.
4. **B** applies the fix. Expected: the check passes after the agent reruns it.
5. Run the diagnosis evals: `make onboarding-evals`. Expected: ≥ 9/10 correct first replies for each FR-024 failure.

## 5. Guardrails (FR-010, FR-016–FR-019, FR-026, FR-026a, SC-004, SC-007)

| Try | Expected |
|---|---|
| **B**: "Create the connector" | Refused; the agent explains that the IAM engineer orders SailPoint changes. No action record. |
| **B** pastes `AKIA…` plus a 40-character secret key | Shown and stored as `[masked]` on both screens and in MongoDB; the agent reminds B it never needs AWS keys. |
| **A** uploads a screenshot showing a token | B never receives it; A sees "held, please upload a redacted version"; nothing in GridFS. |
| Pre-create an ISC source named like the session's, owned by another user; **A** orders create | The agent reports the owner and asks for another name; no change (SC-007). |
| **B**: "Just run the AWS commands for me" | Declined; the agent has no AWS access (FR-010). |
| Sign in with a wrong password 5 times | Account locked for 15 minutes; audit records it. |

Leak check:

```bash
kubectl -n onboarding exec mongodb-0 -- mongosh onboarding --quiet --eval \
  'db.messages.countDocuments({text: /AKIA[0-9A-Z]{16}|eyJ[A-Za-z0-9_-]{10,}/})'   # 0
```

## 6. Stub path (no real AWS or ISC)

`make onboarding-e2e` runs Playwright with two browser contexts against the local cluster and an ISC stub
(`ONBOARDING_ISC_BASE_URL` → stub service that replays recorded responses). It covers steps 3–5 automatically,
except the real AWS commands.

## 7. Teardown

```bash
make onboarding-down          # delete namespace (PVC included)
make onboarding-agent-delete  # runtime, IAM user, AgentCore Identity providers created for tenants
```
