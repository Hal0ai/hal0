/**
 * dashboard-v3 — root `#dashboard` route renders the topbar, sidebar,
 * snapshot strip, and composer. Smoke-level: enough to catch a bundle
 * regression that would blank the view.
 */
import { test, expect } from '../fixtures/apiMock'

test.describe('Dashboard v3 (/)', () => {
  test('renders chrome + snapshot + composer', async ({ page }) => {
    await page.goto('/')
    // wait for React mount (Sidebar is route-aware, only renders after App)
    await expect(page.locator('.topbar')).toBeVisible()
    await expect(page.locator('.sidebar')).toBeVisible()
    await expect(page.locator('.main .view')).toBeVisible()
    // composer (empty or active) renders inside dash-main
    await expect(page.locator('.composer').first()).toBeVisible()
    // snapshot strip renders in dash-side
    await expect(page.locator('.snap')).toBeVisible()
    // sidebar active row should be Dashboard
    await expect(page.locator('.sb-row.active .lbl')).toHaveText('Dashboard')
  })

  test('topbar exposes brand + command-palette button + bell', async ({ page }) => {
    await page.goto('/')
    await expect(page.locator('.tb-brand')).toBeVisible()
    await expect(page.locator('.tb-cmdk')).toBeVisible()
    await expect(page.locator('.tb-bell')).toBeVisible()
  })
})
