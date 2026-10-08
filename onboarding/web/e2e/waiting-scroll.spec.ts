import { expect, Page, test } from '@playwright/test';
import { agentDone, banner, otherLog, otherThread, ownLog, ownThread, seed, send, signIn, stub } from './helpers';

const threads = (page: Page) => [ownThread(page), otherThread(page)];

// T125: quickstart §3a step 7 (FR-006g, SC-013): the waiting banner in both threads on both screens, worded per viewer.
test('the waiting banner shows who the agent waits for, in all four threads, and clears when the wait ends', async ({ browser }) => {
  const s = seed('trust');
  const iam = await signIn(browser, s.iam, s.password);
  const owner = await signIn(browser, s.owner, s.password);
  await iam.goto(`sessions/${s.session_id}/iam`);
  await owner.goto(`sessions/${s.session_id}/owner`);
  for (const page of [iam, owner]) for (const t of threads(page)) await expect(banner(t)).toHaveText('');

  // The connection check fails on the broken trust: the agent waits for the owner's read-only check.
  await send(iam, 'Create the connector with the session details, then run the connection check, aggregation and Test Connection.');
  await agentDone(iam);
  for (const t of threads(owner)) await expect(banner(t)).toContainText(/^\s*Waiting for you:\s*\S/, { timeout: 2_000 });
  for (const t of threads(iam)) await expect(banner(t)).toContainText(/^\s*Waiting for (?!you)[^:]+:\s*\S/, { timeout: 2_000 });
  const reason = (await banner(ownThread(owner)).innerText()).replace(/^\s*Waiting for you:\s*/, '').trim();
  await expect(banner(ownThread(iam))).toContainText(reason);
  for (const page of [iam, owner]) {
    for (const t of threads(page)) await expect(banner(t)).not.toContainText(/queued|held/i);
    // The banner sits outside the scrolling log, directly above the message box or the view-only note.
    await expect(ownLog(page).locator('app-waiting-banner')).toHaveCount(0);
  }

  // The owner fixes the trust and confirms: the wait ends, and every banner goes within 2 s of the reply.
  await stub(s, 'fix_trust');
  const replies = await ownLog(owner).locator('article.msg.agent').count();
  await send(owner, 'I have finished all the AWS setup steps, including the role trust. The role is ready.');
  // agentDone alone can return before the turn starts (the new message is not marked queued yet): wait for the reply.
  await expect.poll(() => ownLog(owner).locator('article.msg.agent').count(), { timeout: 4 * 60_000 }).toBeGreaterThan(replies);
  await agentDone(owner);
  for (const page of [iam, owner]) for (const t of threads(page)) await expect(banner(t)).toHaveText('', { timeout: 2_000 });
});

// T125: quickstart §3a step 8 (FR-006f, SC-012): long threads scroll inside the thread; the page never grows.
test('long threads scroll in their own log, keep the reading position, and offer a jump to new messages', async ({ browser }) => {
  const s = seed('happy', 200);
  const iam = await signIn(browser, s.iam, s.password);
  const owner = await signIn(browser, s.owner, s.password);
  await iam.setViewportSize({ width: 1440, height: 900 });
  await iam.goto(`sessions/${s.session_id}/iam`);
  await owner.goto(`sessions/${s.session_id}/owner`);
  const own = ownLog(iam);
  await expect(own.locator('article.msg')).toHaveCount(200);

  // The page itself does not scroll; the message box, the suggestions and the banner area stay on screen.
  expect(await iam.evaluate(() => document.scrollingElement!.scrollHeight <= window.innerHeight + 1)).toBe(true);
  await expect(ownThread(iam).locator('#composer-text')).toBeInViewport();
  await expect(ownThread(iam).locator('button.suggestion').first()).toBeInViewport();
  await expect(otherThread(iam).getByText('You can read this thread')).toBeInViewport();

  // Each log scrolls on its own and opens at its newest message.
  for (const l of [own, otherLog(iam)]) {
    expect(await l.evaluate((el) => el.scrollHeight > el.clientHeight)).toBe(true);
    await expect(l.locator('article.msg').last()).toBeInViewport();
  }

  // The viewer's own message always brings the log to the end.
  await own.evaluate((el) => { el.scrollTop = 0; el.dispatchEvent(new Event('scroll')); });
  await send(iam, 'What is left to do?');
  await expect(own.locator('article.msg', { hasText: 'What is left to do?' })).toBeInViewport();
  // Reading older messages while the agent answers: the reply keeps the position and offers "New messages".
  const before = await own.locator('article.msg').count();
  await own.evaluate((el) => { el.scrollTop = 0; el.dispatchEvent(new Event('scroll')); });
  await expect.poll(() => own.locator('article.msg').count(), { timeout: 90_000 }).toBeGreaterThan(before);
  await agentDone(iam);
  expect(await own.evaluate((el) => el.scrollTop)).toBeLessThan(50);
  const jump = ownThread(iam).getByRole('button', { name: /New messages/ });
  await expect(jump).toBeVisible();
  await jump.click();
  await expect(own.locator('article.msg').last()).toBeInViewport();
  await expect(jump).toHaveCount(0);

  // A 40-line command output stays inside its message: the thread does not get wider.
  const width = await ownThread(iam).evaluate((el) => el.getBoundingClientRect().width);
  await own.locator('pre').first().scrollIntoViewIfNeeded();
  expect(await ownThread(iam).evaluate((el) => el.getBoundingClientRect().width)).toBeLessThanOrEqual(width + 1);
  expect(await own.evaluate((el) => el.scrollWidth <= el.clientWidth + 1)).toBe(true);
});
