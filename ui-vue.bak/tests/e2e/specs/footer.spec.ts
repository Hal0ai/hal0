/**
 * footer.spec.ts — v2 chrome Footer (slice #168).
 *
 * Replaces the v1 4-tab footer spec; the old FooterBar/FooterPane DOM
 * was deleted with the rest of the v1 footer subtree. This file
 * preserves the v1 test intent (collapsed dock + expand toggle +
 * persistence) but asserts against the new chip-row + journal-pane
 * structure. Additional behaviour (filter / search / empty state) is
 * covered by chrome.spec.ts; this file is the narrower smoke for
 * footer-only flow.
 */
import { test, expect, json } from '../fixtures/apiMock'
import { installSseHarness } from '../fixtures/sseHarness'

test.beforeEach(async ({ page, mockState, cleanState }) => {
  void cleanState
  await page.setViewportSize({ width: 1366, height: 900 })
  await installSseHarness(page)
  await page.route('**/api/events?**', (route) =>
    json(route, { events: [], next_since: 0 }),
  )
  await page.route('**/api/events/stream*', (route) =>
    route.fulfill({ status: 200, contentType: 'text/event-stream', body: '' }),
  )
  await page.route('**/api/agent/approvals/events*', (route) =>
    route.fulfill({ status: 200, contentType: 'text/event-stream', body: '' }),
  )
  await page.route('**/v1/health', (route) =>
    json(route, { loaded: [], max_loaded: 4 }),
  )
  mockState.status.hostname = 'hal0-test'
})

test('renders chip row + last-3 peek row when collapsed', async ({ page }) => {
  await page.goto('/')
  await expect(page.locator('[data-testid="foot-chips"]')).toBeVisible()
  await expect(page.locator('[data-testid="foot-journal-peek"]')).toBeVisible()
  // Pane stays mounted-out until toggled.
  await expect(page.locator('[data-testid="foot-pane"]')).toHaveCount(0)
})

test('click toggle expands and collapses the journal pane', async ({ page }) => {
  await page.goto('/')
  const toggle = page.locator('[data-testid="foot-toggle"]')
  await expect(toggle).toHaveAttribute('aria-expanded', 'false')

  await toggle.click()
  await expect(page.locator('[data-testid="foot-pane"]')).toBeVisible()
  await expect(toggle).toHaveAttribute('aria-expanded', 'true')

  await toggle.click()
  await expect(page.locator('[data-testid="foot-pane"]')).toHaveCount(0)
  await expect(toggle).toHaveAttribute('aria-expanded', 'false')
})

test('expanded state survives reload via sessionStorage', async ({ page }) => {
  await page.goto('/')
  // Pre-seed sessionStorage and reload — pane should mount expanded.
  await page.evaluate(() => sessionStorage.setItem('hal0:journal-pane', '1'))
  await page.reload()
  await expect(page.locator('[data-testid="foot-pane"]')).toBeVisible()
})

test('Esc closes an expanded journal pane', async ({ page }) => {
  await page.goto('/')
  await page.locator('[data-testid="foot-toggle"]').click()
  await expect(page.locator('[data-testid="foot-pane"]')).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.locator('[data-testid="foot-pane"]')).toHaveCount(0)
})
