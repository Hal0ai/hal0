/**
 * dashboard-v3 — root `#dashboard` route renders the topbar, sidebar,
 * hardware section, and snapshot strip. The dashboard is the
 * system-overview page. (v0.4: the standalone web-chat `#chat` route was
 * removed — chat lives in the `hermes chat` TUI.)
 */
import { test, expect } from '../fixtures/apiMock'

test.describe('Dashboard v3 (/)', () => {
  test('renders chrome + hardware section + snapshot', async ({ page }) => {
    await page.goto('/')
    // wait for React mount (Sidebar is route-aware, only renders after App)
    await expect(page.locator('.topbar')).toBeVisible()
    await expect(page.locator('.sidebar')).toBeVisible()
    await expect(page.locator('.main .view')).toBeVisible()
    // hardware section is the main column on /dashboard
    await expect(page.locator('.hw-section')).toBeVisible()
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
