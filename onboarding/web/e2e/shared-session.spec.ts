import { expect, test } from '@playwright/test';
import { agentDone, otherLog, otherThread, ownLog, ownThread, seed, send, signIn, openOther } from './helpers';

// T104 / US3 (revised), SC-003: one thread per person, each shown live on the other screen (view only).
test('each participant writes in their own thread and sees the other one live', async ({ browser }) => {
  const s = seed();
  const iam = await signIn(browser, s.iam, s.password);
  const owner = await signIn(browser, s.owner, s.password);

  // Each role reaches only its own screen; the owner is sent back from the IAM screen (FR-003).
  await owner.goto(`sessions/${s.session_id}/iam`);
  await expect(owner).toHaveURL(new RegExp(`sessions/${s.session_id}/owner$`));
  await iam.goto(`sessions/${s.session_id}/iam`);
  await openOther(owner); // collapsed on the owner's screen by default (FR-006)
  await expect(iam.getByRole('heading', { name: 'SailPoint actions' })).toBeVisible();
  await expect(iam.getByText(/online/).first()).toBeVisible();

  // Both screens show two threads: the own one with a message box, the other one view only, without one (FR-006).
  for (const page of [iam, owner]) {
    await expect(ownThread(page).locator('#composer-text')).toBeVisible();
    await expect(otherThread(page).getByText('View only')).toBeVisible();
    await expect(otherThread(page).locator('textarea')).toHaveCount(0);
    await expect(otherThread(page).getByText('Ask the agent in your thread to relay a message')).toBeVisible();
  }

  // Relay: the owner's message lands in the owner's thread on the IAM screen (view only) within 2 s, labelled with
  // the owner's name, and never in the IAM engineer's own thread.
  const text = `Hello from the owner ${Date.now()}`;
  const sent = Date.now();
  await send(owner, text);
  await expect(otherLog(iam).getByText(text)).toBeVisible({ timeout: 2_000 });
  console.log(`relay ${Date.now() - sent} ms`);
  await expect(otherLog(iam).locator('article.msg', { hasText: text }).locator('.who')).not.toHaveText('You');
  await expect(ownLog(iam).getByText(text)).toHaveCount(0);

  // A second message (from the other thread) while the agent answers the first waits in the queue, on both screens.
  const second = `And a second question ${Date.now()}`;
  await send(iam, second);
  // FR-006h: its agent reply is there at once, right under it (a status, or the answer if the agent was quick).
  await expect(ownLog(iam).locator('article.msg', { hasText: second })
    .locator('xpath=following-sibling::article[contains(@class,"agent")][1]')).toBeVisible({ timeout: 1_000 });
  await expect(otherLog(owner).locator('article.msg', { hasText: second })).toBeVisible({ timeout: 2_000 });
  await agentDone(iam);
  // Each thread got at least its own reply; the agent may also pass a message on into the other thread (FR-006c).
  await expect(ownLog(iam).locator('article.msg.agent').first()).toBeVisible();
  await expect(otherLog(iam).locator('article.msg.agent').first()).toBeVisible();

  // Rejoin: a reload shows both threads again, labelled from this side.
  await owner.reload();
  await expect(owner.getByRole('button', { name: /IAM engineer ↔ Agent/ })).toHaveAttribute('aria-expanded', 'false');
  await openOther(owner);
  await expect(ownLog(owner).locator('article.msg', { hasText: text }).locator('.who')).toHaveText('You');
  await expect(otherLog(owner).getByText(second)).toBeVisible();
  // Both screens show the same threads: the owner's view of each thread matches the IAM engineer's.
  await expect(ownLog(owner).locator('article.msg.agent')).toHaveCount(await otherLog(iam).locator('article.msg.agent').count());
  await expect(otherLog(owner).locator('article.msg.agent')).toHaveCount(await ownLog(iam).locator('article.msg.agent').count());
});

// Signed out, the app root and guarded pages send you to the login page (guards must not crash, NG0203).
test('signed-out visitors land on the login page', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  for (const path of ['', 'sessions', 'admin', 'catalog']) {
    await page.goto(path);
    await expect(page).toHaveURL(/\/login$/);
    await expect(page.getByRole('button', { name: 'Sign in' })).toBeVisible();
  }
  expect(errors).toEqual([]);
});

// A live-stream request that fails (502 while the web pod or port-forward restarts) must not leave the window
// stuck on "reconnecting…": it retries and catches up on what it missed.
test('the live stream recovers after a failed reconnect', async ({ browser }) => {
  const s = seed();
  const iam = await signIn(browser, s.iam, s.password);
  const owner = await signIn(browser, s.owner, s.password);
  let failures = 2;
  await iam.route('**/events?*', (route) => (failures-- > 0 ? route.fulfill({ status: 502, body: 'Bad Gateway' }) : route.continue()));
  await iam.goto(`sessions/${s.session_id}/iam`);
  await owner.goto(`sessions/${s.session_id}/owner`);
  await expect(iam.getByText('reconnecting', { exact: false })).toBeHidden({ timeout: 15_000 });
  const text = `After the outage ${Date.now()}`;
  await send(owner, text);
  await expect(otherLog(iam).getByText(text)).toBeVisible({ timeout: 5_000 });
});

// A proxy that holds the event stream back while the request itself succeeds (seen behind a TLS-inspecting corporate
// proxy) must not leave the screen frozen: it long-polls the same events, with a few requests a minute.
test('the screen keeps up by long-polling when a proxy holds the live stream back', async ({ browser }) => {
  const s = seed();
  const iam = await signIn(browser, s.iam, s.password);
  const owner = await signIn(browser, s.owner, s.password);
  await owner.route('**/events?*', () => undefined); // never answered, like a buffering proxy
  let polls = 0;
  owner.on('request', (r) => { if (r.url().includes('/events/poll')) polls++; });
  await owner.goto(`sessions/${s.session_id}/owner`);
  await openOther(owner);
  await iam.goto(`sessions/${s.session_id}/iam`);
  await expect.poll(() => polls, { timeout: 10_000 }).toBeGreaterThan(0);
  await expect(owner.getByText('reconnecting', { exact: false })).toBeHidden();
  const text = `Through the proxy ${Date.now()}`;
  const sent = Date.now();
  await send(iam, text);
  await expect(otherLog(owner).locator('article.msg', { hasText: text }).first()).toBeVisible({ timeout: 3_000 });
  console.log(`long-poll relay ${Date.now() - sent} ms`);
});
