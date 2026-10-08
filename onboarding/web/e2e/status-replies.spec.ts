import { expect, test } from '@playwright/test';
import { agentDone, otherLog, ownLog, seed, send, signIn } from './helpers';

// T143 / quickstart §3b step 1 (FR-006h, SC-014): every message gets the agent's reply at once with a status, which the
// answer later replaces in the same place; nothing is ever shown as only "queued". "slow question…" makes the scripted
// model take 4 s, so the agent is busy while the others write.
test('a message sent while the agent is busy gets a status reply that the answer replaces', async ({ browser }) => {
  const s = seed();
  const iam = await signIn(browser, s.iam, s.password);
  const owner = await signIn(browser, s.owner, s.password);
  await iam.goto(`sessions/${s.session_id}/iam`);
  await owner.goto(`sessions/${s.session_id}/owner`);

  await send(iam, 'slow question about the checks');
  await expect(ownLog(iam).locator('article.msg.agent[data-reply-state="working"]')).toBeVisible({ timeout: 2_000 });

  const sent = Date.now();
  await send(owner, 'What happens after the role is created?');
  const mine = ownLog(owner).locator('article.msg', { hasText: 'What happens after the role is created?' });
  const reply = mine.locator('xpath=following-sibling::article[contains(@class,"agent")][1]');
  await expect(reply).toContainText(/Received\.\s+I'm finishing .+'s question first; yours is next\./, { timeout: 1_000 });
  console.log(`status reply ${Date.now() - sent} ms`);
  // The same status on the IAM engineer's screen, in the owner's thread.
  await expect(otherLog(iam).locator('article.msg.agent[data-reply-state="received"]')).toContainText('yours is next');
  // No "queued" label anywhere.
  for (const page of [iam, owner]) await expect(page.getByText(/^\s*queued\s*$/)).toHaveCount(0);

  // Two more while the first is still running: they count down.
  await send(owner, 'slow question two');
  await expect(ownLog(owner).locator('article.msg', { hasText: 'slow question two' })).toBeVisible();
  await send(owner, 'and a third one');
  await expect(ownLog(owner).locator('article.msg.agent', { hasText: '2 messages are ahead of yours' })).toBeVisible({ timeout: 2_000 });

  await agentDone(owner, 60_000);
  // Each reply became its answer in place: one agent bubble per message, none left on a status.
  const agentBubbles = await ownLog(owner).locator('article.msg.agent').count();
  expect(agentBubbles).toBe(3);
  await expect(reply.locator('.bubble.status')).toHaveCount(0);
  await expect(ownLog(iam).locator('article.msg.agent').first()).toContainText('Here is the slow answer.');
});
