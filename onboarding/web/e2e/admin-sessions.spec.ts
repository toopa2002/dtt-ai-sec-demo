import { expect, Page, test } from '@playwright/test';
import { agentDone, ownLog, seed, send, signIn } from './helpers';

// T164 / quickstart §3b steps 6-7 (FR-031-FR-033, US6 #4, US8, SC-016).

async function createOwner(admin: Page, username: string): Promise<void> {
  const me = await (await admin.request.get('api/auth/session')).json();
  const r = await admin.request.post('api/admin/users', {
    headers: { 'X-CSRF-Token': me.csrf_token },
    data: { username, display_name: 'Second Owner', role: 'application_owner', is_admin: false,
            initial_password: 'second-owner-password-1' },
  });
  expect(r.ok()).toBeTruthy();
}

test('finish, admin reopen, and handing the owner place to someone else', async ({ browser }) => {
  const s = seed();
  const iam = await signIn(browser, s.iam, s.password);
  const owner = await signIn(browser, s.owner, s.password);
  const admin = await signIn(browser, s.admin, s.password);
  await iam.goto(`sessions/${s.session_id}/iam`);
  await owner.goto(`sessions/${s.session_id}/owner`);
  await send(owner, 'What do I need to set up in AWS first?');
  await agentDone(owner);

  // Finish (FR-031): both screens are read-only and say an admin can reopen it (US6 #4).
  await iam.getByRole('button', { name: 'Finish session' }).click();
  for (const page of [iam, owner]) {
    await expect(page.getByText('This session is finished. An admin can reopen it.')).toBeVisible();
    await expect(page.locator('#composer-text')).toHaveCount(0);
  }

  // Reopen (FR-032) from Admin → Sessions.
  await admin.goto('admin');
  const row = admin.locator(`tr[data-session="${s.session_id}"]`);
  await expect(row).toContainText('Finished');
  await expect(row).toContainText(/deleted in \d+ days/);
  await row.getByRole('button', { name: 'Reopen' }).click();
  await expect(admin.getByText('Session reopened.')).toBeVisible();
  for (const page of [iam, owner]) {
    await expect(page.locator('#composer-text')).toBeVisible({ timeout: 5_000 });
    await expect(ownLog(page).locator('.note.system', { hasText: 'reopened this session' })).toBeVisible();
  }

  // Handover (FR-033) while the first owner has the session open.
  const second = `second.owner.${Date.now() % 100000}`;
  await createOwner(admin, second);
  await admin.reload();
  const row2 = admin.locator(`tr[data-session="${s.session_id}"]`);
  await row2.getByRole('button', { name: 'Hand over' }).click();
  await admin.getByLabel('Place').selectOption('application_owner');
  const picker = admin.getByLabel('Hand to');
  await expect(picker.locator('option', { hasText: s.owner })).toHaveCount(0);  // the current holder is not offered
  await picker.selectOption({ label: `${second} · Second Owner` });
  const handed = Date.now();
  await admin.locator('form.handover').getByRole('button', { name: 'Hand over' }).click();
  await expect(admin.getByText('Place handed over.')).toBeVisible();

  // The previous owner leaves the session within 2 s and no longer lists it.
  await expect(owner).toHaveURL(/\/sessions\?left=/, { timeout: 2_000 });
  console.log(`revoked after ${Date.now() - handed} ms`);
  await expect(owner.getByText('An admin handed your place')).toBeVisible();
  await expect(owner.locator(`a.row[href*="${s.session_id}"]`)).toHaveCount(0);

  // The new owner sees the history (old messages keep their author's name) and the plan.
  const next = await signIn(browser, second, 'second-owner-password-1');
  await next.goto(`sessions/${s.session_id}/owner`);
  const first = ownLog(next).locator('article.msg', { hasText: 'What do I need to set up in AWS first?' });
  await expect(first.locator('.who')).not.toHaveText('You');
  await expect(ownLog(next).locator('.note.system', { hasText: 'handed the application owner' })).toBeVisible();
  await expect(next.getByRole('heading', { name: 'Plan' })).toBeVisible();
});
