import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './e2e', testMatch: 'execution-graph-visual.spec.ts', workers: 1,
  outputDir: '../.tmp/graph-playwright', timeout: 45_000,
  reporter: [['list'], ['./e2e/completion-reporter.ts']],
  use: { baseURL: process.env.OAW_E2E_BASE_URL ?? 'http://127.0.0.1:5177', channel: process.env.PLAYWRIGHT_CHANNEL ?? (process.platform === 'win32' ? 'msedge' : 'chromium'), headless: true, viewport: { width: 1400, height: 900 }, screenshot: 'only-on-failure', trace: 'retain-on-failure' },
});
