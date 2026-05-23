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
  lemond: typeof MOCK_DATA.lemond
  slots: typeof MOCK_DATA.slots
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
  // IMPORTANT: glob `**/api/**` matches ANY path containing `/api/` — that
  // includes Vite-served source modules like `/src/api/hooks/useSlots.ts`,
  // which then get fulfilled with `{}` (application/json) and refuse to load
  // as ESM modules ("Expected JavaScript module" MIME error). The React tree
  // never mounts.
  //
  // Scope the catch-all to absolute /api/* + /v1/* on the origin (i.e. paths
  // that start with /api/ or /v1/) so dev-server module URLs pass through.
  await page.route(/\/(api|v1)\//, (route) => {
    const u = new URL(route.request().url())
    if (u.pathname.startsWith('/api/') || u.pathname.startsWith('/v1/')) {
      return json(route, {})
    }
    return route.continue()
  })

  await page.route('**/api/status', (route) =>
    json(route, {
      version: state.lemond.version,
      update_available: false,
      slots: state.slots,
      hardware: state.host,
    }),
  )
  await page.route('**/api/hardware', (route) => json(route, state.host))
  await page.route('**/api/slots', (route) => json(route, { slots: state.slots }))
  await page.route('**/api/slots/metrics', (route) => json(route, {}))
  await page.route('**/api/backends', (route) => json(route, { backends: state.backends }))
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

export { expect } from '@playwright/test'
