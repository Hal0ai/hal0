/**
 * apiMock fixture — page.route stubs for the `/api/*` + `/v1/*` endpoints
 * the v3 React dashboard will start touching in Phase B1. Phase A (current)
 * is HAL0_DATA-driven and renders without any fetch, so the fixture's main
 * job today is to catch stray calls so they don't leak to the vite proxy
 * and hit a live backend by accident.
 *
 * Each spec installs the fixture via `test.use({ cleanState: true })`-style
 * extension below, then overrides per-route as it grows. Phase B1 should
 * fold real response shapes into MOCK_DATA without touching specs that
 * don't need them.
 *
 * Live-mode bypass: when HAL0_E2E_LIVE=1 the fixture installs no routes;
 * the dev-server proxy in vite.config.ts forwards /api+/v1 to 127.0.0.1:8080.
 */
import { test as base, Page, Route } from '@playwright/test'
import { MOCK_DATA } from './mock-data'

export const LIVE = process.env.HAL0_E2E_LIVE === '1'
export { MOCK_DATA } from './mock-data'

/* ── Default mock state (cloned per spec) ────────────────────────── */

export type MockState = {
  host: typeof MOCK_DATA.host
  slots: typeof MOCK_DATA.slots
  models: typeof MOCK_DATA.models
  backends: typeof MOCK_DATA.backends
  approvals: any[]
}

export function makeMockState(): MockState {
  return JSON.parse(JSON.stringify(MOCK_DATA))
}

/* ── helper: JSON fulfil ─────────────────────────────────────────── */

export function json(route: Route, body: any, status = 200) {
  return route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  })
}

/* ── Install default mocks on a page ─────────────────────────────── */

export async function installDefaultMocks(page: Page, state: MockState) {
  if (LIVE) return

  // Catch-all FIRST so per-route registrations after this win
  // (Playwright matches routes in reverse-registration order).
  //
  // Patterns are anchored on the URL origin so they don't accidentally
  // intercept Vite module imports under `/src/api/…` (which would be
  // fulfilled with JSON and break React mount with a MIME error).
  await page.route(/^https?:\/\/[^/]+\/api\//, (route) => json(route, {}))
  await page.route(/^https?:\/\/[^/]+\/v1\//, (route) => json(route, {}))

  await page.route('**/api/status', (route) =>
    json(route, {
      version: '0.4.0',
      update_available: false,
      slots: state.slots,
      hardware: state.host,
      // 0.4 gate: the dashboard hides the Agent (Memory) nav unless
      // /api/status reports memory live. The γ-suite exercises the memory
      // UI, so the default mock keeps it on; a dedicated spec flips it off.
      memory_enabled: true,
    }),
  )
  await page.route('**/api/hardware', (route) => json(route, state.host))
  await page.route('**/api/models', (route) =>
    json(route, { models: state.models, count: state.models.length }),
  )
  await page.route('**/api/slots', (route) => json(route, { slots: state.slots }))
  await page.route('**/api/slots/metrics', (route) => json(route, {}))
  await page.route('**/api/backends', (route) => json(route, { backends: state.backends }))
  await page.route('**/api/profiles', (route) => json(route, MOCK_DATA.profiles ?? []))
  await page.route('**/api/agent/approvals', (route) =>
    json(route, { approvals: state.approvals }),
  )
}

/* ── Test fixture wiring ─────────────────────────────────────────── */

type Fixtures = {
  mockState: MockState
  cleanState: void
}

export const test = base.extend<Fixtures>({
  mockState: async ({}, use) => {
    await use(makeMockState())
  },
  cleanState: [
    async ({ page, mockState }, use) => {
      await installDefaultMocks(page, mockState)
      await use()
    },
    { auto: true },
  ],
})

export { expect, type Page } from '@playwright/test'
