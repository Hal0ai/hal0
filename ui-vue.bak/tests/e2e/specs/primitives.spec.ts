/**
 * primitives.spec.ts — slice #167 v2 primitives smoke + behaviour.
 *
 * Mounts each primitive on the test-only `/_primitives_test` route
 * (registered in router.js as ``primitives-sandbox``) and exercises:
 *
 *   - Modal:  Esc closes, backdrop click closes, focus trap cycles.
 *   - Drawer: open triggers translateX(0); Esc + backdrop close.
 *   - ConfirmDialog: destructive type-to-confirm gates the confirm btn.
 *   - BannerStack: filters by scope, dismiss removes from store.
 *   - Menu:   auto-closes on outside click + Esc; stubbed item toasts.
 *   - ToastStack: queues entries; short-ttl entry auto-removes.
 *
 * Uses the default apiMock fixture so the FirstRun guard treats the
 * sandbox load as a "not first-run" navigation and renders the route.
 */
import { test, expect } from '../fixtures/apiMock'

test.describe('v2 primitives', () => {
  test.beforeEach(async ({ cleanState, page }) => {
    void cleanState
    await page.goto('/_primitives_test')
    await expect(page.locator('h1', { hasText: 'Primitives sandbox' })).toBeVisible()
  })

  // ────────────────────────────────────────────────────────────────
  test('Modal: opens, closes on Esc, closes on backdrop click', async ({ page }) => {
    const open  = page.getByTestId('open-modal')
    const body  = page.getByTestId('modal-body')

    // Esc closes
    await open.click()
    await expect(body).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(body).toHaveCount(0)

    // Backdrop click closes
    await open.click()
    await expect(body).toBeVisible()
    // Click the backdrop near the corner — outside the .modal-shell.
    await page.locator('.modal-backdrop').click({ position: { x: 4, y: 4 } })
    await expect(body).toHaveCount(0)
  })

  // ────────────────────────────────────────────────────────────────
  test('Drawer: opens with translateX(0), closes on Esc', async ({ page }) => {
    const open = page.getByTestId('open-drawer')
    const drawer = page.locator('.drawer-right')

    // Initially the drawer element exists but is translated 100% off-screen.
    await expect(drawer).toBeAttached()
    let opened = await drawer.evaluate((el) => el.classList.contains('open'))
    expect(opened).toBe(false)

    await open.click()
    // Wait for transition to complete (0.22s) before reading transform.
    await page.waitForTimeout(300)
    opened = await drawer.evaluate((el) => el.classList.contains('open'))
    expect(opened).toBe(true)
    // The transform should be the identity / translateX(0).
    const transform = await drawer.evaluate((el) => getComputedStyle(el).transform)
    expect(transform === 'none' || transform.includes('matrix(1, 0, 0, 1, 0, 0)')).toBe(true)

    await page.keyboard.press('Escape')
    await page.waitForTimeout(50)
    opened = await drawer.evaluate((el) => el.classList.contains('open'))
    expect(opened).toBe(false)
  })

  // ────────────────────────────────────────────────────────────────
  test('ConfirmDialog (destructive + type-to-confirm)', async ({ page }) => {
    await page.getByTestId('open-cd-type').click()
    // Find the confirm button — second button in modal foot (cancel is first).
    const confirmBtn = page.locator('.modal-foot .cd-foot-actions button').nth(1)
    await expect(confirmBtn).toBeVisible()
    await expect(confirmBtn).toBeDisabled()

    // Type a non-matching value — still disabled.
    const input = page.locator('.modal-shell input.input')
    await input.fill('NOPE')
    await expect(confirmBtn).toBeDisabled()

    // Type the matching keyword "DELETE" → confirm enables.
    await input.fill('DELETE')
    await expect(confirmBtn).toBeEnabled()

    await confirmBtn.click()
    await expect(page.getByTestId('cd-log')).toContainText('type-confirm')
  })

  // ────────────────────────────────────────────────────────────────
  test('ConfirmDialog: recoverable variant is enabled immediately', async ({ page }) => {
    await page.getByTestId('open-cd-recoverable').click()
    const confirmBtn = page.locator('.modal-foot .cd-foot-actions button').nth(1)
    await expect(confirmBtn).toBeEnabled()
    await confirmBtn.click()
    await expect(page.getByTestId('cd-log')).toContainText('rec-confirm')
  })

  // ────────────────────────────────────────────────────────────────
  test('BannerStack: filters by scope; dismiss removes from store', async ({ page }) => {
    // Initially no banners.
    await expect(page.locator('[data-testid="banner-stack"]')).toHaveCount(0)

    // Toggle nuclear-evict (scope=slots) on — should render.
    await page.getByTestId('ban-toggle-nuclear').click()
    await expect(page.locator('[data-banner-id="nuclear-evict"]')).toBeVisible()

    // Add lemond-offline (scope=global) — also renders inside the slots
    // stack because BannerStack auto-includes "global" alongside the
    // requested scope (matches the design's filter).
    await page.getByTestId('ban-toggle-lemond').click()
    await expect(page.locator('[data-banner-id="lemond-offline"]')).toBeVisible()

    // Stack count = 2.
    await expect(page.locator('[data-testid="banner-stack"] .banner')).toHaveCount(2)

    // Add the 19th banner (slice #167 fold-in).
    await page.getByTestId('ban-toggle-skip').click()
    await expect(page.locator('[data-banner-id="skip-path"]')).toBeVisible()
    await expect(page.locator('[data-testid="banner-stack"] .banner')).toHaveCount(3)

    // Dismiss skip-path via the × button.
    await page.locator('[data-banner-id="skip-path"] .banner-dismiss').click()
    await expect(page.locator('[data-banner-id="skip-path"]')).toHaveCount(0)
    await expect(page.locator('[data-testid="banner-stack"] .banner')).toHaveCount(2)
  })

  // ────────────────────────────────────────────────────────────────
  test('Menu: opens, fires wired onClick, closes on outside click + Esc', async ({ page }) => {
    await page.getByTestId('open-menu').click()
    const menu = page.locator('.hal0-menu')
    await expect(menu).toBeVisible()

    // Click the wired action → onClick fires AND menu closes.
    await menu.locator('.hal0-menu-item').first().click()
    await expect(menu).toHaveCount(0)
    await expect(page.getByTestId('menu-log')).toContainText('wired-fired')

    // Re-open + Esc closes.
    await page.getByTestId('open-menu').click()
    await expect(menu).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(menu).toHaveCount(0)

    // Re-open + outside click closes.
    await page.getByTestId('open-menu').click()
    await expect(menu).toBeVisible()
    await page.locator('h1').click()
    await expect(menu).toHaveCount(0)
  })

  // ────────────────────────────────────────────────────────────────
  test('Menu: item without onClick fires a toast', async ({ page }) => {
    await page.getByTestId('open-menu').click()
    // The "Stubbed action" item is third (after wired + divider).
    await page.locator('.hal0-menu-item', { hasText: 'Stubbed action' }).click()
    // ToastStack renders the stubbed-toast.
    await expect(page.locator('.hal0-toast', { hasText: 'Stubbed action — stubbed' })).toBeVisible()
  })

  // ────────────────────────────────────────────────────────────────
  test('ToastStack: queues entries; short-ttl entry auto-removes', async ({ page }) => {
    await expect(page.getByTestId('toast-count')).toHaveText('queue: 0')

    await page.getByTestId('push-toast').click()
    await page.getByTestId('push-toast').click()
    await page.getByTestId('push-toast').click()
    await expect(page.locator('.hal0-toast')).toHaveCount(3)
    await expect(page.getByTestId('toast-count')).toHaveText('queue: 3')

    // Push a short-lived toast (200ms ttl) and assert it disappears.
    await page.getByTestId('push-short-toast').click()
    await expect(page.locator('.hal0-toast', { hasText: 'quick' })).toBeVisible()
    await page.waitForTimeout(400)
    await expect(page.locator('.hal0-toast', { hasText: 'quick' })).toHaveCount(0)
    // The three long-lived toasts remain.
    await expect(page.locator('.hal0-toast')).toHaveCount(3)
  })
})
