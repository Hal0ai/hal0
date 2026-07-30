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
 *
 * Port: derived per worktree (see `tests/e2e/port.ts`). This repo is worked in
 * several parallel git worktrees; with a constant port plus
 * `reuseExistingServer`, the second worktree's run attached to the first's Vite
 * server and tested the wrong branch. Override with HAL0_E2E_PORT.
 */
import { fileURLToPath } from 'node:url'

import { defineConfig, devices } from '@playwright/test'

import { resolveE2EPort } from './tests/e2e/port'

const LIVE = process.env.HAL0_E2E_LIVE === '1'
// Per-worktree by default so two local checkouts can't share one dev server —
// `reuseExistingServer` below would otherwise silently attach this run to
// whatever branch started first, reporting results for code it never checked
// out (#1399). HAL0_E2E_PORT still wins; CI keeps the fixed port. The full
// rationale (and a repro) lives in tests/e2e/port.ts.
const PORT = String(
  resolveE2EPort({
    env: process.env,
    dir: fileURLToPath(new URL('.', import.meta.url)),
  }),
)

export default defineConfig({
  testDir: './tests/e2e',
  // Playwright's default testMatch also globs `*.test.ts`, which would collect
  // the vitest unit file next to the fixtures (tests/e2e/port.test.ts) and
  // abort collection for the ENTIRE suite on its `vitest` import. Every e2e
  // file here is a `.spec.ts`, so pin that explicitly (#1399).
  testMatch: '**/*.spec.ts',
  timeout: LIVE ? 180_000 : 30_000,
  globalTimeout: LIVE ? 30 * 60_000 : 12 * 60_000,
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
