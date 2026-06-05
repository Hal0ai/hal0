/**
 * dashboard-v3 — root `#dashboard` route renders the topbar, sidebar, and
 * the system-overview layout. Home-redesign (2026-06-05): the main column
 * now leads with the live surface — a 50/50 Memory-map | Throughput row
 * (.dash-5050) above the full-width slot snapshot (.snap) — while the
 * sidebar holds the condensed SystemCard (.sys-card). The verbose Hardware
 * spread (.hw-section) was demoted into that card and no longer renders.
 */
import { test, expect } from '../fixtures/apiMock'

test.describe('Dashboard v3 (/)', () => {
  test('renders chrome + live surface + snapshot + system card', async ({ page }) => {
    await page.goto('/')
    // wait for React mount (Sidebar is route-aware, only renders after App)
    await expect(page.locator('.topbar')).toBeVisible()
    await expect(page.locator('.sidebar')).toBeVisible()
    await expect(page.locator('.main .view')).toBeVisible()
    // main column leads with the 50/50 Memory | Throughput row …
    await expect(page.locator('.dash-main .dash-5050')).toBeVisible()
    // … above the full-width slot snapshot
    await expect(page.locator('.dash-main .snap')).toBeVisible()
    // condensed System identity card lives in the sidebar
    await expect(page.locator('.dash-side .sys-card')).toBeVisible()
    // the verbose hardware spread is demoted — no longer in the main area
    await expect(page.locator('.hw-section')).toHaveCount(0)
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
