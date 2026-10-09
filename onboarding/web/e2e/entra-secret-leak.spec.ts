import { expect, test } from '@playwright/test';
import { agentDone, E2E_ENTRA_SECRET, seed, send, signIn } from './helpers';

// Spec 002 T048 (SC-102): after a full Entra run, the submitted secret is nowhere either participant can read through
// the API (session, messages, actions, live events), and the stub received exactly it once. e2e.sh then runs the leak
// scan over MongoDB and the dev logs with this secret as a literal.
test('the application secret leaks nowhere', async ({ browser }) => {
  const s = seed('entra-directory', 0, 'directory,service_principals,ai_agents');
  const owner = await signIn(browser, s.owner, s.password);
  const iam = await signIn(browser, s.iam, s.password);
  await owner.goto(`sessions/${s.session_id}/owner`);
  await owner.getByLabel('Client secret Value').fill(E2E_ENTRA_SECRET);
  await owner.getByLabel('Expires').fill('2027-09-30');
  await owner.getByRole('button', { name: 'Send to the vault' }).click();
  await expect(owner.getByLabel('Secret status').locator('li.current')).toHaveText('Received');
  await iam.goto(`sessions/${s.session_id}/iam`);
  await send(iam, 'Create the connector and run the checks');
  await agentDone(iam);
  await expect(iam.getByRole('region', { name: 'Application secret' })).toContainText('vault copy deleted');

  for (const page of [iam, owner]) {
    const api = `api/sessions/${s.session_id}`;
    for (const path of ['', '/messages', '/application-secret', '/suggestions', '/events/poll?after=0']) {
      const r = await page.request.get(api + path);
      expect(await r.text(), path).not.toContain(E2E_ENTRA_SECRET);
    }
  }
  const actions = await iam.request.get(`api/sessions/${s.session_id}/actions`);
  expect(await actions.text()).not.toContain(E2E_ENTRA_SECRET);
  const state = await (await fetch(`${s.stub}/_stub/state`)).json();
  const patches = (state.entra.patches as Record<string, unknown>[]).filter((p) => 'clientSecret' in p);
  expect(patches.map((p) => p['clientSecret'])).toEqual([E2E_ENTRA_SECRET]); // exactly the submitted value, once
});
