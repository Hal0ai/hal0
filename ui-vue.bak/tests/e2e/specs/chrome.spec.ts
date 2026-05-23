/**
 * chrome.spec.ts — v2 dashboard chrome (slice #168).
 *
 * Covers TopBar / Sidebar / Footer / BottomTabs behaviour across the
 * four breakpoints called out in issue #168:
 *
 *   ≥1280  full sidebar
 *   1080-1279  icon-collapse sidebar
 *   720-1079   drawer overlay (hamburger trigger)
 *   <720       sidebar hidden + BottomTabs visible
 *
 * Plus the journal pane upgrades that landed with v0.3 (source filter,
 * search highlight, empty state, "Open full logs →").
 */
import { test, expect, json } from '../fixtures/apiMock'
import { installSseHarness } from '../fixtures/sseHarness'

test.beforeEach(async ({ page, mockState, cleanState }) => {
  // ``cleanState`` is destructured so the default API mocks (incl. the
  // catch-all ``**/v1/**``) are installed FIRST; the page.route calls
  // below then take precedence (Playwright matches routes in
  // reverse-registration order).
  void cleanState
  await page.setViewportSize({ width: 1366, height: 900 })
  await installSseHarness(page)

  await page.route('**/api/events?**', (route) =>
    json(route, { events: [], next_since: 0 }),
  )
  await page.route('**/api/events', (route) =>
    json(route, { events: [], next_since: 0 }),
  )
  await page.route('**/api/events/stream*', (route) =>
    route.fulfill({ status: 200, contentType: 'text/event-stream', body: '' }),
  )
  await page.route('**/api/agent/approvals/events*', (route) =>
    route.fulfill({ status: 200, contentType: 'text/event-stream', body: '' }),
  )

  // Lemonade /v1/health — give the sidebar+footer something meaningful
  // to render. maxModels=4, loaded=2 → loaded chip shows "2/4".
  await page.route('**/v1/health', (route) =>
    json(route, {
      loaded: [
        { model_name: 'llama-3.2-3b', backend_url: 'http://127.0.0.1:8081' },
        { model_name: 'phi-3-mini',   backend_url: 'http://127.0.0.1:8082' },
      ],
      max_loaded: 4,
      version: 'v10.6.0',
      throughput_mbps: 12.3,
    }),
  )

  mockState.status.hostname = 'hal0-test'
})

/* ── TopBar ─────────────────────────────────────────────────────── */

test('TopBar renders wordmark, version pill, ⌘K, host chip, and agent bell', async ({ page }) => {
  await page.goto('/')
  const topbar = page.locator('.topbar')
  await expect(topbar).toBeVisible()
  await expect(topbar.locator('.wordmark')).toBeVisible()
  await expect(topbar.locator('.ver')).toContainText(/^v\d/)
  await expect(topbar.locator('.tb-cmdk')).toBeVisible()
  await expect(topbar.locator('.tb-host')).toContainText('hal0-test')
  // Agent bell sits in the TopBar.
  await expect(topbar.locator('button.bell, button[aria-label*="Pending approvals"]')).toBeVisible()
})

/* ── Sidebar breakpoints ─────────────────────────────────────────── */

test('Sidebar variants per breakpoint: full / collapsed / drawer / hidden', async ({ page }) => {
  // ≥1280 — full
  await page.setViewportSize({ width: 1366, height: 900 })
  await page.goto('/')
  const sidebar = page.locator('.sidebar')
  await expect(sidebar).toBeVisible()
  await expect(sidebar).not.toHaveClass(/collapsed/)
  await expect(sidebar).not.toHaveClass(/drawer/)

  // 1080-1279 — icon-collapse rail
  await page.setViewportSize({ width: 1100, height: 900 })
  await expect(sidebar).toHaveClass(/collapsed/)
  // Labels collapse in this band — query the rendered text of one item.
  const dashboardRow = sidebar.locator('.sb-row').first()
  await expect(dashboardRow).toBeVisible()

  // 720-1079 — overlay drawer (hidden until hamburger toggles).
  await page.setViewportSize({ width: 900, height: 900 })
  await expect(sidebar).toHaveClass(/drawer/)
  await expect(sidebar).not.toHaveClass(/drawer-open/)
  // Hamburger lives in the TopBar at drawer/mobile widths.
  await page.locator('.tb-hamburger').click()
  await expect(sidebar).toHaveClass(/drawer-open/)
  // Close by clicking the backdrop.
  await page.locator('.mobile-backdrop').click()
  await expect(sidebar).not.toHaveClass(/drawer-open/)

  // <720 — sidebar removed entirely.
  await page.setViewportSize({ width: 600, height: 900 })
  await expect(page.locator('.sidebar')).toHaveCount(0)
})

/* ── BottomTabs ─────────────────────────────────────────────────── */

test('BottomTabs only render <720', async ({ page }) => {
  await page.setViewportSize({ width: 1366, height: 900 })
  await page.goto('/')
  await expect(page.locator('[data-testid="bottom-tabs"]')).toHaveCount(0)

  await page.setViewportSize({ width: 600, height: 900 })
  await expect(page.locator('[data-testid="bottom-tabs"]')).toBeVisible()
  // Five fixed tabs.
  await expect(page.locator('[data-testid^="bottom-tab-"]')).toHaveCount(5)
  await expect(page.locator('[data-testid="bottom-tab-more"]')).toBeVisible()
})

test('BottomTabs "More" toggles a sheet with secondary surfaces', async ({ page }) => {
  await page.setViewportSize({ width: 600, height: 900 })
  await page.goto('/')
  await page.locator('[data-testid="bottom-tab-more"]').click()
  const sheet = page.locator('[data-testid="bottom-tabs-more"]')
  await expect(sheet).toBeVisible()
  await expect(sheet).toContainText('Hardware')
  await expect(sheet).toContainText('Settings')
})

/* ── Footer chip row + journal pane ──────────────────────────────── */

test('Footer chip row reflects lemonade store and toggles journal pane', async ({ page }) => {
  await page.goto('/')
  const chips = page.locator('[data-testid="foot-chips"]')
  await expect(chips).toBeVisible()
  // Lemonade chip — /v1/health stub returns up + 2 loaded out of 4.
  await expect(page.locator('[data-testid="foot-chip-lemond"]')).toHaveText('up')
  await expect(page.locator('[data-testid="foot-chip-loaded"]')).toContainText('2/4')

  // Toggle pane open.
  await page.locator('[data-testid="foot-toggle"]').click()
  await expect(page.locator('[data-testid="foot-pane"]')).toBeVisible()
  // Toggle pane closed.
  await page.locator('[data-testid="foot-toggle"]').click()
  await expect(page.locator('[data-testid="foot-pane"]')).toHaveCount(0)
})

test('Footer journal pane: source filter switches active chip', async ({ page }) => {
  await page.goto('/')
  await page.locator('[data-testid="foot-toggle"]').click()
  const merged = page.locator('[data-testid="foot-pane-filter-merged"]')
  const hal0   = page.locator('[data-testid="foot-pane-filter-hal0"]')
  await expect(merged).toHaveClass(/on/)
  await hal0.click()
  await expect(hal0).toHaveClass(/on/)
  await expect(merged).not.toHaveClass(/on/)
})

test('Footer journal pane: empty state + clear-filters link surface on no match', async ({ page }) => {
  await page.goto('/')
  await page.locator('[data-testid="foot-toggle"]').click()
  // No events seeded → the body should render the empty-state immediately.
  const empty = page.locator('[data-testid="foot-pane-empty"]')
  await expect(empty).toBeVisible()
  await expect(empty).toContainText('No journal entries match')
  await expect(empty.locator('.foot-pane-clear')).toBeVisible()
})

test('Footer journal pane: search highlights matching tokens', async ({ page, mockState }) => {
  // Backfill /api/events with a single match so the search highlight
  // can actually fire on the mark element.
  await page.route('**/api/events?**', (route) =>
    json(route, {
      events: [
        { id: 1, ts: 1716490000, type: 'log', severity: 'info', source: 'hal0', message: 'session ftr-001 opened' },
      ],
      next_since: 1,
    }),
  )
  await page.goto('/')
  await page.locator('[data-testid="foot-toggle"]').click()
  await page.locator('[data-testid="foot-pane-search"]').fill('session')
  // The line should render a <mark class="hl"> wrapping "session".
  const mark = page.locator('[data-testid="foot-pane-body"] mark.hl')
  await expect(mark.first()).toBeVisible()
  await expect(mark.first()).toHaveText(/session/i)
})

test('Footer pane "Open full logs →" navigates to /logs', async ({ page }) => {
  await page.goto('/')
  await page.locator('[data-testid="foot-toggle"]').click()
  await page.locator('[data-testid="foot-pane-open-logs"]').click()
  await expect(page).toHaveURL(/\/logs(\?|$)/)
})

test('Footer pane open/close persists in sessionStorage', async ({ page }) => {
  await page.goto('/')
  await page.locator('[data-testid="foot-toggle"]').click()
  await expect(page.locator('[data-testid="foot-pane"]')).toBeVisible()
  // Reload — sessionStorage survives, so the pane reopens.
  await page.reload()
  await expect(page.locator('[data-testid="foot-pane"]')).toBeVisible()
})

/* ── Sidebar Lemonade status block ──────────────────────────────── */

test('Lemonade status block reflects mock store', async ({ page }) => {
  await page.goto('/')
  await expect(page.locator('[data-testid="lemond-state"]')).toContainText('up')
  await expect(page.locator('[data-testid="lemond-loaded"]')).toContainText('2/4')
})

test('Lemonade status click routes to /logs?source=lemond', async ({ page }) => {
  await page.goto('/')
  await page.locator('.sb-status').click()
  await expect(page).toHaveURL(/\/logs\?source=lemond/)
})

/* ── Agents · v0.3 sidebar group ────────────────────────────────── */

test('Agents · v0.3 group shows when an agent is installed', async ({ page, mockState }) => {
  // Seed an installed agent so the group renders.
  mockState.agentInstalled = [
    { name: 'pi-coder', installed_at: 1716000000, status: 'installed' },
  ]
  await page.goto('/')
  const sidebar = page.locator('.sidebar')
  await expect(sidebar.locator('.sb-group-h')).toContainText('Agents · v0.3')
  // Slice #14 (#180) unblocked the MCP Servers row; only Memory
  // remains gated until Phase 9.
  await expect(sidebar.locator('.sb-row.sb-sub.disabled')).toHaveCount(1)
  await expect(sidebar.locator('.sb-row.sb-sub.disabled')).toContainText('Memory')
})

test('Sidebar shows "Set up agent →" CTA when no agent installed', async ({ page }) => {
  // mockState.agentInstalled defaults to [] in apiMock.
  await page.goto('/')
  const sidebar = page.locator('.sidebar')
  await expect(sidebar.locator('.sb-cta')).toContainText(/Set up agent/i)
  // Group header NOT shown in this branch.
  await expect(sidebar.locator('.sb-group-h')).toHaveCount(0)
})
