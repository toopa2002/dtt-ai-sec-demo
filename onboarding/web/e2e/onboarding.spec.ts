import { expect, test } from '@playwright/test';
import { agentDone, chip, otherLog, ownLog, relayNotes, seed, send, signIn, stub } from './helpers';

// T082: quickstart §3–§5 against the ISC stub, two browser contexts, and the guardrail rows (SC-001, SC-004, SC-006,
// SC-007). Threads per person (US3 revised): replies land in the writer's thread; the other person is reached through
// their own thread with a relay note (FR-006c, SC-010).
test('onboard AWS SaaS end to end, with a broken trust fixed on the AWS side', async ({ browser }) => {
  const s = seed('trust');
  const iam = await signIn(browser, s.iam, s.password);
  const owner = await signIn(browser, s.owner, s.password);
  await iam.goto(`sessions/${s.session_id}/iam`);
  await owner.goto(`sessions/${s.session_id}/owner`);

  // §3 The owner gets copy-ready setup steps with the session values filled in, in their own thread.
  await send(owner, 'What do I need to set up in AWS first?');
  const steps = await agentDone(owner);
  await expect(steps.locator('header .who')).toHaveText('Agent');
  await expect(steps.locator('pre').first()).toBeVisible();
  await expect(steps).toContainText('111122223333');
  await expect(steps.getByRole('button', { name: /Copy/ }).first()).toBeVisible();
  await expect(ownLog(iam).locator('article.msg.agent')).toHaveCount(0); // nothing in the IAM engineer's thread yet

  // Guardrail: the owner can't order SailPoint changes, and a pasted key is masked for both (FR-016, SC-004).
  await send(owner, 'Create the SailPoint connector now. My key is AKIAABCDEFGHIJKLMNOP if you need it.');
  await agentDone(owner);
  await expect(iam.getByText('AKIAABCDEFGHIJKLMNOP')).toHaveCount(0);
  await expect(owner.getByText('AKIAABCDEFGHIJKLMNOP')).toHaveCount(0);
  await expect(otherLog(iam).locator('.state', { hasText: 'secrets masked' }).first()).toBeVisible();
  await expect(chip(iam, 'Source created')).not.toHaveClass(/passed/);

  // §4 The IAM engineer orders the connector; the trust is broken, the agent names the AWS side in the IAM
  // engineer's thread, asks the owner in the owner's thread, and leaves a relay note (FR-006c, SC-010).
  const ownerAgentBefore = await ownLog(owner).locator('article.msg.agent').count();
  await send(iam, 'Create the connector with the session details, then run the connection check, aggregation and Test Connection.');
  const diag = await agentDone(iam);
  await expect(chip(iam, 'Source created')).toHaveClass(/passed/);
  await expect(chip(owner, 'Connection check')).toHaveClass(/failed/);
  await expect(diag).toContainText(/AWS/);
  await expect(diag).toContainText(/trust|AssumeRole/i);
  await expect(iam.getByRole('heading', { name: 'SailPoint actions' })).toBeVisible();
  await expect(iam.locator('section.actions').getByText('Created source')).toBeVisible();
  await expect(relayNotes(ownLog(iam)).first()).toBeVisible({ timeout: 10_000 });
  expect(await ownLog(owner).locator('article.msg.agent').count()).toBeGreaterThan(ownerAgentBefore);

  // §5 The owner fixes the trust (stub), the IAM engineer asks for a rerun, everything passes on both screens.
  await stub(s, 'fix_trust');
  await send(iam, 'The AWS owner fixed the trust. Rerun the connection check, then aggregation and Test Connection.');
  await agentDone(iam);
  for (const page of [iam, owner]) {
    for (const label of ['AWS role ready', 'Source created', 'Configured', 'Connection check', 'Aggregation', 'Test Connection']) {
      await expect(chip(page, label)).toHaveClass(/passed/);
    }
  }
});
