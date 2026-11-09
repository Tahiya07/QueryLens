import { defineConfig, devices } from '@playwright/test';
export default defineConfig({ testDir: './e2e', reporter: 'list', use: { baseURL: 'http://localhost:3000', ...devices['Desktop Chrome'], screenshot: 'only-on-failure' }, webServer: { command: 'npm run dev', url: 'http://localhost:3000', reuseExistingServer: !process.env.CI, timeout: 120_000 } });
