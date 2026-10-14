import { defineConfig } from '@playwright/test';
import { randomUUID } from 'node:crypto';

// Every run has its own disposable artifacts; later checks retain earlier receipts.
const runId = process.env.NEONOTE_BROWSER_RUN_ID ?? randomUUID();
process.env.NEONOTE_BROWSER_RUN_ID = runId;

export default defineConfig({
  testDir: './tests/browser',
  fullyParallel: false,
  workers: 1,
  timeout: 30000,
  retries: 0,
  reporter: 'list',
  outputDir: `../../.cache/browser-tests/${runId}`,
  globalTeardown: './tests/browser/teardown.ts',
  use: {
    baseURL: 'http://127.0.0.1:18000',
    channel: 'chrome',
    headless: true,
    viewport: { width: 1440, height: 1080 },
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
  webServer: {
    command: 'conda run --no-capture-output -n piano python ../../scripts/run_e2e_server.py',
    url: 'http://127.0.0.1:18000/api/health',
    reuseExistingServer: false,
    timeout: 45000,
  },
});
