/**
 * dashboard-layout-save-error-v3 — #1460.
 *
 * `useSaveDashLayout` optimistically updates the cache and then swallowed
 * EVERY PUT failure ("Backend not yet shipping this endpoint — silently
 * swallow"). With the backend rejecting the v3 body as `layout.invalid`, a
 * customization appeared to stick and then reverted on reload, with nothing
 * on screen to say so.
 *
 * Contract pinned here:
 *   1. a non-404 PUT failure is surfaced to the operator (the codebase's
 *      existing `window.__hal0Toast` channel, rendered as `.hal0-toast`),
 *   2. a 404 stays fail-soft — an older backend without the route must not
 *      nag on every swap.
 */
import { test, expect, json, type Page } from '../fixtures/apiMock'

/** The backend's structured error envelope (hal0.api.middleware.error_codes). */
function envelope(code: string, message: string, details: Record<string, unknown> = {}) {
  return JSON.stringify({ error: { code, message, details } })
}

async function routeLayout(page: Page, put: { status: number; body?: string }) {
  await page.route('**/api/user/dashboard-layout', (route) => {
    if (route.request().method() !== 'PUT') return json(route, {})
    return route.fulfill({
      status: put.status,
      contentType: 'application/json',
      body: put.body ?? '',
    })
  })
}

async function toggleQuickActions(page: Page) {
  await page.goto('/#dashboard')
  await expect(page.locator('.rd-hero')).toBeVisible({ timeout: 10_000 })
  await page.locator('.rd-hero button:has-text("customize")').click()
  await page.locator('.rd-qa-toggle').click()
}

test.describe('Dashboard layout save errors are surfaced (#1460)', () => {
  test('a 422 from PUT /api/user/dashboard-layout reaches the operator', async ({ page }) => {
    await routeLayout(page, {
      status: 422,
      body: envelope('layout.invalid', 'dashboard layout failed schema validation', {
        v: 'layout version must be 3, got 2',
      }),
    })

    await toggleQuickActions(page)

    const toast = page.locator('.hal0-toast')
    await expect(toast).toBeVisible({ timeout: 10_000 })
    await expect(toast.locator('.toast-msg')).toContainText(
      'dashboard layout failed schema validation',
    )
  })

  test('a 404 stays fail-soft — no nag from a backend without the route', async ({ page }) => {
    await routeLayout(page, {
      status: 404,
      body: envelope('system.not_found', 'Not Found'),
    })

    await toggleQuickActions(page)

    // Give the mutation a beat to settle, then assert nothing was raised.
    await page.waitForTimeout(1_000)
    await expect(page.locator('.hal0-toast')).toHaveCount(0)
  })
})
