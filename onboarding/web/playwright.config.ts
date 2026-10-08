import { defineConfig } from '@playwright/test';

/**
 * Two-browser e2e against the dev stack (deploy/scripts/dev.sh: web :4300, API :8080, ISC stub, real Claude Haiku).
 * Run through deploy/scripts/e2e.sh, which starts the stack and seeds users, tenant and session before each spec.
 */
export default defineConfig({
  testDir: 'e2e',
  fullyParallel: false,
  workers: 1,
  timeout: 6 * 60_000,
  expect: { timeout: 15_000 },
  reporter: [['list']],
  use: {
    baseURL: process.env['ONB_WEB'] ?? 'http://127.0.0.1:4300/onboarding/',
    trace: 'retain-on-failure',
  },
});
