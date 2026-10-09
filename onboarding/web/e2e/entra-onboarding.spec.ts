import { expect, test } from '@playwright/test';
import { agentDone, chip, E2E_ENTRA_SECRET, entraStub, ownLog, seed, send, signIn } from './helpers';

// Spec 002 T046/T078: Microsoft Entra ID on the ISC stub with the scripted model (no cost). The screens follow the
// "002 · Microsoft Entra ID" artboards: catalog, new session, Entra administrator, IAM engineer (quickstart §2).

test('catalog and new-session form for Microsoft Entra ID', async ({ browser }) => {
  const s = seed('entra-directory');
  const iam = await signIn(browser, s.iam, s.password);
  await iam.goto('catalog');
  await expect(iam.getByText(/2 available · \d+ planned/)).toBeVisible();
  const card = iam.locator('article.available', { hasText: 'Microsoft Entra ID' });
  await expect(card).toBeVisible();
  await expect(card.locator('.caps li.writes')).toContainText('Provisioning');
  await card.getByRole('link', { name: 'Start a session' }).click();

  await expect(iam.getByRole('heading', { name: 'New Microsoft Entra ID session' })).toBeVisible();
  await expect(iam.getByLabel('Azure subscriptions with Foundry agents')).toHaveCount(0); // only inside its capability
  await iam.locator('.cap', { hasText: 'AI agents' }).getByRole('checkbox').check();
  await expect(iam.getByLabel('Azure subscriptions with Foundry agents')).toBeVisible();
  await iam.locator('.cap', { hasText: 'Provisioning' }).getByRole('checkbox').check();
  const start = iam.getByRole('button', { name: 'Start session' });
  await expect(start).toBeDisabled(); // FR-103: the warning first
  await iam.getByText('I understand and accept this for the session').click();
  await expect(start).toBeEnabled();
  // The "what happens" panel counts permissions from the playbook's permission files (research R15).
  await expect(iam.getByText('Directory: 6 read permissions')).toBeVisible();
  await expect(iam.getByText('Provisioning: 7 write permissions and the User Administrator role')).toBeVisible();
});

test('the secret field, the proof and the vault copy deleted', async ({ browser }) => {
  const s = seed('entra-directory', 0, 'directory,service_principals');
  const iam = await signIn(browser, s.iam, s.password);
  const owner = await signIn(browser, s.owner, s.password);
  await iam.goto(`sessions/${s.session_id}/iam`);
  await owner.goto(`sessions/${s.session_id}/owner`);

  // The Entra administrator's secret field (US2-4/5): a GUID is the secret's ID, not its Value.
  const field = owner.getByRole('region', { name: 'Secret field' });
  await expect(field).toBeVisible();
  await owner.getByLabel('Client secret Value').fill('1b7c2e94-8d3a-4f51-b6e0-2a9c7d4e1f38');
  await owner.getByLabel('Expires').fill('2027-09-30');
  await owner.getByRole('button', { name: 'Send to the vault' }).click();
  await expect(owner.getByRole('alert')).toContainText("This is the secret's ID, not its Value");
  await owner.getByLabel('Client secret Value').fill(E2E_ENTRA_SECRET);
  await owner.getByLabel('Expires').fill('2027-09-30');
  await owner.getByRole('button', { name: 'Send to the vault' }).click();
  await expect(owner.getByLabel('Secret status').locator('li.current')).toHaveText('Received');
  await expect(owner.getByLabel('Client secret Value')).toHaveCount(0); // collapses to the status strip
  await expect(iam.getByRole('region', { name: 'Application secret' })).toContainText('Received');

  // The IAM engineer orders the connector: Test Connection runs before aggregation (header order, R19).
  await send(iam, 'Create the connector and run the checks');
  await agentDone(iam);
  const chips = iam.getByRole('list', { name: 'Onboarding status' }).locator('li');
  await expect(chips.nth(4)).toContainText('Test Connection');
  await expect(chips.nth(5)).toContainText('Aggregation');
  await expect(chip(iam, 'Aggregation')).toHaveClass(/passed/);
  const proof = iam.getByRole('region', { name: 'Proof results' });
  await expect(proof).toContainText('Users');
  await expect(proof).toContainText('64');
  await expect(proof).toContainText('161'); // service principals, counted apart from users
  await expect(iam.getByRole('region', { name: 'Application secret' })).toContainText('vault copy deleted');
  await expect(owner.getByLabel('Secret status').locator('li.current')).toHaveText('Vault copy deleted');
  await expect(iam.locator('section.actions small.summary').first()).toContainText('clientSecret: [vaulted]');
  await expect(owner.getByRole('region', { name: 'Proof results' })).toHaveCount(0); // IAM engineer only
  for (const page of [iam, owner]) await expect(page.getByText(E2E_ENTRA_SECRET)).toHaveCount(0);
});

test('a secret pasted in the chat is masked and replaced', async ({ browser }) => {
  const s = seed('entra-directory');
  const owner = await signIn(browser, s.owner, s.password);
  const iam = await signIn(browser, s.iam, s.password);
  await owner.goto(`sessions/${s.session_id}/owner`);
  await iam.goto(`sessions/${s.session_id}/iam`);
  await send(owner, `here it is: ${E2E_ENTRA_SECRET}`);
  await agentDone(owner);
  const note = ownLog(owner).locator('.note.system.danger[data-code="secret_exposed"]');
  await expect(ownLog(owner).locator('.note.system.danger')).toHaveCount(1); // one note, not two
  await expect(note).toContainText('Treat it as exposed');
  await expect(ownLog(owner).getByText('[masked]').first()).toBeVisible();
  await expect(owner.getByText('A new secret is needed.')).toBeVisible();
  await expect(owner.getByRole('button', { name: "I've put the new secret in the field" })).toBeVisible();
  for (const page of [iam, owner]) await expect(page.getByText(E2E_ENTRA_SECRET)).toHaveCount(0);
});

test('AI agents fall back to the ISC interface on a tenant limitation', async ({ browser }) => {
  const s = seed('entra-directory', 0, 'directory,ai_agents');
  await entraStub(s, 'dataset_unavailable');
  const owner = await signIn(browser, s.owner, s.password);
  const iam = await signIn(browser, s.iam, s.password);
  await owner.goto(`sessions/${s.session_id}/owner`);
  await owner.getByLabel('Client secret Value').fill(E2E_ENTRA_SECRET);
  await owner.getByLabel('Expires').fill('2027-09-30');
  await owner.getByRole('button', { name: 'Send to the vault' }).click();
  await iam.goto(`sessions/${s.session_id}/iam`);
  await send(iam, 'Create the connector and run the checks');
  const reply = await agentDone(iam);
  await expect(reply).toContainText('tenant limitation');
  await expect(iam.getByRole('region', { name: 'Proof results' })).toContainText('Start it in ISC');
  await expect(iam.locator('section.actions li.limited')).toHaveCount(1);
});

test('an invalid secret is diagnosed and a new one requested (E1)', async ({ browser }) => {
  const s = seed('entra-directory');
  await entraStub(s, 'secret_invalid');
  const owner = await signIn(browser, s.owner, s.password);
  const iam = await signIn(browser, s.iam, s.password);
  await owner.goto(`sessions/${s.session_id}/owner`);
  await owner.getByLabel('Client secret Value').fill(E2E_ENTRA_SECRET);
  await owner.getByLabel('Expires').fill('2027-09-30');
  await owner.getByRole('button', { name: 'Send to the vault' }).click();
  await iam.goto(`sessions/${s.session_id}/iam`);
  await send(iam, 'Create the connector and run the checks');
  const reply = await agentDone(iam);
  await expect(reply).toContainText('AADSTS7000215');
  await expect(owner.getByText('A new secret is needed.')).toBeVisible();
});
