/**
 * Playwright config for the website screenshot set (docs/screenshots/).
 *
 * Not part of the e2e suite: the capture specs live under scripts/screenshots/,
 * outside tests/e2e/, so CI never collects them. Run from ui/:
 *
 *   npx playwright test -c playwright.screenshots.config.ts
 *
 * The run renders the dashboard against the same in-bundle mock layer the e2e
 * suite uses (VITE_MOCK_HAL0=1) with the e2e apiMock fixture, plus a showcase
 * overlay (scripts/screenshots/showcase.ts) so every page looks like a busy,
 * healthy box. Images are written to ../docs/screenshots/.
 */
import { defineConfig } from '@playwright/test'

const PORT = process.env.HAL0_SHOTS_PORT || '5391'

export default defineConfig({
  testDir: './scripts/screenshots',
  testMatch: '**/*.shot.ts',
  timeout: 60_000,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list']],
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    browserName: 'chromium',
    colorScheme: 'dark',
    deviceScaleFactor: 2,
    actionTimeout: 5_000,
    navigationTimeout: 15_000,
  },
  webServer: {
    command: `npx vite --port ${PORT} --strictPort --host 127.0.0.1`,
    url: `http://127.0.0.1:${PORT}`,
    reuseExistingServer: false,
    timeout: 120_000,
    stdout: 'ignore',
    stderr: 'pipe',
    env: { VITE_MOCK_HAL0: '1' },
  },
})
