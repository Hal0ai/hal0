/**
 * Playwright config — hal0 v3 React dashboard E2E suite (Phase B2).
 *
 * Mode policy:
 *   - Default: Vite dev server (started by the `webServer` block) renders
 *     the React+HAL0_DATA mock-only dashboard. No backend calls happen
 *     until Phase B1 wires API hooks; the apiMock fixture seeds /api/*
 *     stubs anyway so future-Phase-B1 specs and live-mode runs stay
 *     symmetric.
 *   - Live mode (`HAL0_E2E_LIVE=1`): the fixture skips routing so the
 *     dev-server proxy in vite.config.ts forwards /api+/v1 to the real
 *     hal0-api on 127.0.0.1:8080.
 *
 * Workers: 4 (Phase A is mock-only and view-isolated, no shared store).
 * Live-mode collapses to 1 worker — the real backend is single-flight.
 */
import { defineConfig, devices } from '@playwright/test'

const LIVE = process.env.HAL0_E2E_LIVE === '1'
const PORT = process.env.HAL0_E2E_PORT || '5173'

export default defineConfig({
  testDir: './tests/e2e',
  timeout: LIVE ? 180_000 : 30_000,
  globalTimeout: LIVE ? 30 * 60_000 : 8 * 60_000,
  expect: { timeout: 5_000 },
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: LIVE ? 1 : 4,
  reporter: process.env.CI
    ? [['html', { open: 'never' }], ['line']]
    : [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL: process.env.HAL0_E2E_BASE_URL || `http://127.0.0.1:${PORT}`,
    trace: 'on-first-retry',
    video: 'retain-on-failure',
    screenshot: 'only-on-failure',
    actionTimeout: 5_000,
    navigationTimeout: 15_000,
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  webServer: {
    command: `npx vite --port ${PORT} --strictPort --host 127.0.0.1`,
    url: `http://127.0.0.1:${PORT}`,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
    stdout: 'ignore',
    stderr: 'pipe',
    env: {
      // Force mock data so specs see steady-state markup, not real-fetch loading lag.
      // Real API integration is exercised via separate manual smoke tests.
      VITE_MOCK_HAL0: '1',
    },
  },
})
