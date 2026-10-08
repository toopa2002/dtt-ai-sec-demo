import { execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { Browser, expect, Locator, Page } from '@playwright/test';

export interface Seed {
  session_id: string;
  password: string;
  stub: string;
  iam: string;
  owner: string;
  admin: string;
}

const REPO = resolve(__dirname, '../../..');

/** Fresh users/tenant/session through smoke.py --seed (same setup as the API smoke run). */
export function seed(scenario: 'happy' | 'trust' = 'happy', messages = 0): Seed {
  const file = join(mkdtempSync(join(tmpdir(), 'onb-e2e-')), 'seed.json');
  execFileSync('uv', ['run', '-q', '--project', 'onboarding/api', 'python', 'onboarding/deploy/scripts/smoke.py',
    '--scenario', scenario, '--seed', file, '--messages', String(messages)], { cwd: REPO, stdio: 'inherit' });
  return JSON.parse(readFileSync(file, 'utf8')) as Seed;
}

export async function signIn(browser: Browser, username: string, password: string): Promise<Page> {
  const page = await (await browser.newContext()).newPage();
  await page.goto('login');
  await page.getByLabel('Username').fill(username);
  await page.getByLabel('Password').fill(password);
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page).toHaveURL(/\/sessions$/);
  return page;
}

/** The viewer's own thread (writable) and the other participant's thread (view only) on one screen (FR-006). */
export const ownThread = (page: Page) => page.locator('app-thread:not(.view-only)');
export const otherThread = (page: Page) => page.locator('app-thread.view-only');
export const ownLog = (page: Page) => page.getByRole('log', { name: 'Your thread with the agent' });
export const otherLog = (page: Page) => page.getByRole('log', { name: /^(?!Your).*thread with the agent$/ });
/** Kept for older specs: the viewer's own thread. */
export const log = ownLog;
/** Suggested-message chips under the viewer's own message box (FR-006e). */
export const chips = (page: Page) => ownThread(page).locator('button.suggestion');
export const relayNotes = (l: Locator) => l.locator('.note');
/** The waiting banner inside one thread (FR-006g). */
export const banner = (thread: Locator) => thread.locator('app-waiting-banner [role=status]');

/** On the application owner's screen the IAM engineer's thread is collapsed by default (FR-006): open it. */
export async function openOther(page: Page): Promise<void> {
  const bar = page.getByRole('button', { name: /IAM engineer ↔ Agent/ });
  await expect(bar).toBeVisible();
  if ((await bar.getAttribute('aria-expanded')) !== 'true') await bar.click();
}

export async function send(page: Page, text: string): Promise<void> {
  await page.locator('#composer-text').fill(text);
  await page.getByRole('button', { name: 'Send' }).click();
}

/** Waits until no reply in either thread is still received or working (FR-006h), then returns the last agent message
 * in the viewer's own thread. */
export async function agentDone(page: Page, timeout = 4 * 60_000) {
  await expect(page.locator('app-thread article.msg').last()).toBeVisible();
  // The reply to the viewer's latest message must exist first (it is created with the message, FR-006h).
  const mine = ownLog(page).locator('article.msg.mine');
  if (await mine.count()) {
    await expect(mine.last().locator('xpath=following-sibling::article[contains(@class,"agent")][1]')).toBeVisible({ timeout });
  }
  await expect(page.locator('app-thread article.msg[data-reply-state="received"], app-thread article.msg[data-reply-state="working"]'))
    .toHaveCount(0, { timeout });
  return ownLog(page).locator('article.msg.agent').last();
}

export const chip = (page: Page, label: string) => page.getByRole('list', { name: 'Onboarding status' }).locator('li', { hasText: label });

export async function stub(s: Seed, action: string): Promise<void> {
  const r = await fetch(`${s.stub}/_stub/${action}`, { method: 'POST' });
  expect(r.ok).toBeTruthy();
}
