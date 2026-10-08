import { expect, test } from '@playwright/test';
import { agentDone, banner, chip, otherThread, ownLog, ownThread, relayNotes, seed, send, signIn, stub } from './helpers';

// T104: quickstart §3a steps 1–5 against the ISC stub (US3 revised; FR-006–FR-006c, FR-016a, SC-010).
test('threads, relay notes, answering while waiting, and check reruns on the owner\'s confirmation', async ({ browser }) => {
  const s = seed('trust');
  const iam = await signIn(browser, s.iam, s.password);
  const owner = await signIn(browser, s.owner, s.password);
  await iam.goto(`sessions/${s.session_id}/iam`);
  await owner.goto(`sessions/${s.session_id}/owner`);

  // 1. Two threads per screen: own (message box, suggestions) and the other one view only (no box, no chips).
  for (const page of [iam, owner]) {
    await expect(page.getByRole('heading', { name: 'Conversations' })).toBeVisible();
    await expect(ownThread(page).locator('#composer-text')).toBeVisible();
    await expect(ownThread(page).locator('button.suggestion').first()).toBeVisible();
    await expect(otherThread(page).getByText('View only')).toBeVisible();
    await expect(otherThread(page).locator('textarea')).toHaveCount(0);
    await expect(otherThread(page).locator('button.suggestion')).toHaveCount(0);
  }

  // 2. The IAM engineer's order fails on the broken trust: the diagnosis is in the IAM engineer's thread, the
  //    read-only check for the owner is in the owner's thread, with a relay note in the IAM engineer's thread.
  const ownerAgentBefore = await ownLog(owner).locator('article.msg.agent').count();
  await send(iam, 'Create the connector with the session details, then run the connection check, aggregation and Test Connection.');
  const diag = await agentDone(iam);
  await expect(chip(owner, 'Connection check')).toHaveClass(/failed/);
  await expect(diag).toContainText(/AWS/);
  await expect(relayNotes(ownLog(iam)).first()).toBeVisible({ timeout: 10_000 });
  expect(await ownLog(owner).locator('article.msg.agent').count()).toBeGreaterThan(ownerAgentBefore);
  await expect(ownLog(owner).locator('article.msg.agent').last()).toContainText(/get-role|trust|run/i);
  await expect(banner(ownThread(iam))).toContainText(/Waiting for/, { timeout: 10_000 });

  // 3. While the agent waits for the owner, the IAM engineer is answered at once, not held (FR-006a).
  const iamAgentBefore = await ownLog(iam).locator('article.msg.agent').count();
  await send(iam, 'What is left to do?');
  await expect(ownLog(iam).locator('article.msg', { hasText: 'What is left to do?' })).toBeVisible();
  await expect.poll(() => ownLog(iam).locator('article.msg.agent').count(), { timeout: 90_000 }).toBeGreaterThan(iamAgentBefore);

  // 4. The owner fixes the trust and confirms in their thread: the checks rerun without a new order from the IAM
  //    engineer, recorded under the IAM engineer's name as reruns after the confirmation (FR-016a).
  await stub(s, 'fix_trust');
  await send(owner, 'I have finished all the AWS setup steps, including the role trust. The role is ready.');
  await agentDone(owner);
  for (const page of [iam, owner]) {
    for (const label of ['Connection check', 'Aggregation', 'Test Connection']) {
      await expect(chip(page, label)).toHaveClass(/passed/, { timeout: 15_000 });
    }
  }
  await expect(iam.locator('section.actions').getByText('rerun after the application owner confirmed a fix').first()).toBeVisible();
  await expect(iam.locator('section.actions').getByText(/ordered by Smoke Iam/).first()).toBeVisible();
  await expect(ownLog(iam).locator('article.msg.agent').last()).toContainText(/pass|success/i, { timeout: 10_000 });

  // 5. "Tell the IAM engineer …": passed on into the IAM engineer's thread, with a relay note for the owner.
  const notesBefore = await relayNotes(ownLog(owner)).count();
  await send(owner, 'Please tell the IAM engineer that I am on a call for the next 10 minutes.');
  await agentDone(owner);
  await expect(ownLog(iam).locator('article.msg.agent').last()).toContainText(/call/i, { timeout: 10_000 });
  expect(await relayNotes(ownLog(owner)).count()).toBeGreaterThan(notesBefore);
});
