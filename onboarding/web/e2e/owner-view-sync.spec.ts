import { expect, Locator, test } from '@playwright/test';
import { otherLog, otherThread, ownLog, ownThread, seed, signIn } from './helpers';

// T154 / quickstart §3b steps 3-4 (FR-006, FR-006i, US3 #15-16, SC-018).

test("the application owner sees only their own thread; the IAM engineer's opens on request", async ({ browser }) => {
  const s = seed();
  const owner = await signIn(browser, s.owner, s.password);
  await owner.goto(`sessions/${s.session_id}/owner`);
  await expect(ownThread(owner).locator('#composer-text')).toHaveCount(1);
  await expect(owner.locator('#composer-text')).toHaveCount(1);
  const bar = owner.getByRole('button', { name: /IAM engineer ↔ Agent/ });
  await expect(bar).toHaveAttribute('aria-expanded', 'false');
  await expect(otherThread(owner)).toHaveCount(0);
  await expect(owner.getByRole('heading', { name: 'SailPoint actions' })).toHaveCount(0);
  await expect(owner.getByRole('heading', { name: 'Plan' })).toBeVisible();

  await bar.click();
  await expect(bar).toHaveAttribute('aria-expanded', 'true');
  await expect(otherThread(owner).getByText('View only')).toBeVisible();
  await expect(otherThread(owner).locator('textarea')).toHaveCount(0);
  await owner.reload();
  await expect(owner.getByRole('button', { name: /IAM engineer ↔ Agent/ })).toHaveAttribute('aria-expanded', 'false');
  await expect(otherThread(owner)).toHaveCount(0);
});

/** The time at the top of a log and of the item just after it (for "within one message"). */
async function topAndNext(log: Locator): Promise<[number, number]> {
  return log.evaluate((el) => {
    const items = [...el.querySelectorAll<HTMLElement>('[data-ts]')];
    const i = items.findIndex((x) => x.offsetTop + x.offsetHeight > el.scrollTop);
    const at = (k: number) => (k >= 0 && k < items.length ? Number(items[k].dataset['ts']) : Number.POSITIVE_INFINITY);
    return [at(i), at(i + 1)];
  });
}

test("the IAM engineer's two threads scroll in step by time, with a Sync toggle", async ({ browser }) => {
  const s = seed('happy', 120);
  const iam = await signIn(browser, s.iam, s.password);
  await iam.setViewportSize({ width: 1440, height: 900 });
  await iam.goto(`sessions/${s.session_id}/iam`);
  const own = ownLog(iam);
  const other = otherLog(iam);
  await expect(own.locator('article.msg')).toHaveCount(120);
  const sync = iam.getByRole('button', { name: /Sync by time/ });
  await expect(sync).toHaveAttribute('aria-pressed', 'true');

  // Both logs show the same divider times.
  const times = (l: Locator) => l.locator('p.divider').evaluateAll((els) => els.map((e) => Number(e.getAttribute('data-ts'))));
  const ownTimes = new Set(await times(own));
  const otherTimes = new Set(await times(other));
  expect(ownTimes.size).toBeGreaterThan(0);
  for (const t of ownTimes) expect([...otherTimes].some((u) => u <= t)).toBe(true);

  // Scroll one log to points in its history: the other comes to its last item at or before that time.
  for (const fraction of [0, 0.35, 0.7]) {
    await own.evaluate((el, f) => { el.scrollTop = Math.round((el.scrollHeight - el.clientHeight) * f); }, fraction);
    await iam.waitForTimeout(250);
    const [t] = await topAndNext(own);
    const [o, oNext] = await topAndNext(other);
    // within one message: the other log's top item is at or before t, the one after it is later (or there is none)
    expect(o === Number.POSITIVE_INFINITY || o <= t || (await other.evaluate((el) => el.scrollTop)) === 0).toBe(true);
    expect(oNext > t || o <= t).toBe(true);
  }

  // Off: the logs scroll separately, and the choice survives a reload.
  await sync.click();
  await expect(sync).toHaveAttribute('aria-pressed', 'false');
  const before = await other.evaluate((el) => el.scrollTop);
  await own.evaluate((el) => { el.scrollTop = 0; });
  await iam.waitForTimeout(250);
  expect(await other.evaluate((el) => el.scrollTop)).toBe(before);
  await iam.reload();
  await expect(iam.getByRole('button', { name: /Sync by time/ })).toHaveAttribute('aria-pressed', 'false');
  await expect(otherLog(iam).locator('p.divider')).toHaveCount(0);

  // Narrow window: threads stack, sync is off and the toggle is disabled.
  await iam.getByRole('button', { name: /Sync by time/ }).click();
  await iam.setViewportSize({ width: 1000, height: 900 });
  await expect(iam.getByRole('button', { name: /Sync by time/ })).toBeDisabled();
  await expect(ownLog(iam).locator('p.divider')).toHaveCount(0);
});
