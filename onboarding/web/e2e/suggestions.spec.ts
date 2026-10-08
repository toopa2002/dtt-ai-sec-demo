import { expect, test } from '@playwright/test';
import { agentDone, chip, chips, otherThread, ownLog, seed, signIn } from './helpers';

// T112: quickstart §3a step 6 against the ISC stub (US7, FR-006e, SC-011).
test('suggested replies fit the role and state, fill the box without sending, and carry a full run', async ({ browser }) => {
  const s = seed();
  const iam = await signIn(browser, s.iam, s.password);
  const owner = await signIn(browser, s.owner, s.password);
  await iam.goto(`sessions/${s.session_id}/iam`);
  await owner.goto(`sessions/${s.session_id}/owner`);

  // 3–5 chips in each own thread from the start; none in the view-only thread; no order for the owner.
  for (const page of [iam, owner]) {
    const n = await chips(page).count();
    expect(n).toBeGreaterThanOrEqual(3);
    expect(n).toBeLessThanOrEqual(5);
    await expect(otherThread(page).locator('button.suggestion')).toHaveCount(0);
  }
  await expect(chips(iam).filter({ hasText: /^Create the connector and run the checks$/ })).toHaveAttribute('data-kind', 'order');
  await expect(chips(owner).locator('[data-kind="order"]')).toHaveCount(0);

  // Picking only fills the box: nothing is sent until Send.
  const ownerChip = chips(owner).filter({ hasText: /^What do I need to set up in AWS first\?$/ });
  await ownerChip.click();
  await expect(owner.locator('#composer-text')).toHaveValue('What do I need to set up in AWS first?');
  await expect(ownLog(owner).locator('article.msg')).toHaveCount(0);
  await owner.getByRole('button', { name: 'Send' }).click();
  const reply = await agentDone(owner);
  await expect(reply).toContainText(/aws/i);

  // Refreshed within 1 s of the reply finishing; a starter is offered and fills the box with a trailing space.
  await expect(chips(owner).first()).toBeVisible({ timeout: 1_000 });
  const starter = chips(owner).filter({ hasText: /^Here is the output:$/ });
  await expect(starter.first()).toBeVisible({ timeout: 1_000 });
  await starter.first().click();
  await expect(owner.locator('#composer-text')).toHaveValue('Here is the output: ');
  await owner.locator('#composer-text').fill('');

  // The IAM engineer completes the onboarding with chips only (stub ISC): order → all checks pass.
  await chips(iam).filter({ hasText: /^Create the connector and run the checks$/ }).first().click();
  await expect(iam.locator('#composer-text')).toHaveValue('Create the connector and run the checks');
  await iam.getByRole('button', { name: 'Send' }).click();
  await agentDone(iam);
  for (const label of ['Source created', 'Configured', 'Connection check', 'Aggregation', 'Test Connection']) {
    await expect(chip(iam, label)).toHaveClass(/passed/);
  }
  await expect(chips(iam).first()).toBeVisible({ timeout: 1_000 });
  const n = await chips(iam).count();
  expect(n).toBeGreaterThanOrEqual(3);
  expect(n).toBeLessThanOrEqual(5);
});
