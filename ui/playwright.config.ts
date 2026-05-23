/**
 * Playwright config — hal0 γ (E2E) suite.
 *
 * Mode policy:
 *   - Default: run against the Vite dev server (started for us by the
 *     `webServer` block) with every backend call mocked via page.route.
 *     This is what CI runs.
 *   - Live mode (`HAL0_E2E_LIVE=1`): skip the mock fixture so specs hit
 *     a real hal0 backend. The dev-server proxy in `vite.config.js`
 *     forwards `/api/*` + `/v1/*` to `HAL0_API_URL` (default
 *     `http://10.0.1.230:8080` — the hal0-test LXC). Each spec checks
 *     the env at install time of the `cleanState` fixture and either
 *     intercepts or no-ops.
 *
 * Worker count: 1. The mocked specs are independent but the live-mode
 * runs share backend state (one slot manager, one model registry), and
 * we don't want race conditions silently corrupting test order.
 *
 * Snapshots: disabled by default. Only `dashboard.spec.ts` (folded
 * into the firstrun + slot lifecycle specs) opts in for a single hero
 * visual snapshot on the unified-memory bar in v0.2.
 *
 * Budget (PLAN §10.3): the seven specs together must fit in ~8 min on
 * CI. Any single spec running >90s is a smell worth flagging; the
 * default `timeout` of 30s catches runaway specs early.
 */
import { defineConfig, devices } from '@playwright/test'

const LIVE = process.env.HAL0_E2E_LIVE === '1'

export default defineConfig({
  testDir: './tests/e2e/specs',
  /* Per-test timeout. Mocked specs finish in 2-10s; live mode needs
     more headroom for actual model pulls + slot warmup. */
  timeout: LIVE ? 180_000 : 30_000,
  /* Total test-runner wall-clock. Hard cap below PLAN §10.3's 8 min
     so a hung worker doesn't eat the CI budget. */
  globalTimeout: LIVE ? 30 * 60_000 : 8 * 60_000,
  expect: {
    timeout: 5_000,
  },
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: process.env.CI ? [['html', { open: 'never' }], ['line']] : 'list',
  use: {
    /* Port may be overridden via HAL0_E2E_PORT when a colliding vite
       on 5173 is already running (e.g. parallel worktree teammates). */
    baseURL: `http://127.0.0.1:${process.env.HAL0_E2E_PORT || 5173}`,
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
    /* firefox + webkit deferred to v0.2 — the chromium suite is the v1
       gate. Keep the projects array structured so adding them is a
       one-block change. */
  ],
  webServer: {
    /* Use `vite` directly (not `npm run dev`) so a single child process
       is started. The dev server proxies /api + /v1 to the configured
       backend; in mocked mode page.route short-circuits before any
       proxy hop. */
    command: `npx vite --port ${process.env.HAL0_E2E_PORT || 5173} --strictPort --host 127.0.0.1`,
    url: `http://127.0.0.1:${process.env.HAL0_E2E_PORT || 5173}`,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
    stdout: 'ignore',
    stderr: 'pipe',
  },
})
