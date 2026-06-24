/**
 * dashboard-v3 — root `#dashboard` route renders the topbar, sidebar, and
 * the dashboard surface.
 *
 * dashboard-overhaul (feat/dashboard-overhaul): the static system-overview
 * (the .dash-5050 Memory|Throughput row + .snap slot snapshot + sidebar
 * .sys-card) was REPLACED by the customizable widget board
 * (DashboardOverhaulView, dash-grid.jsx). The slot list is now the in-grid
 * anchor card and the page exposes a Customize toggle. The detailed grid /
 * edit-mode / dot-state coverage lives in dashboard-overhaul-v3.spec.ts;
 * this spec pins the chrome + that the overhaul surface mounts on the route.
 */
import { test, expect } from '../fixtures/apiMock'

test.describe('Dashboard v3 (/)', () => {
  test('renders chrome + the overhaul widget board surface', async ({ page }) => {
    await page.goto('/')
    // wait for React mount (Sidebar is route-aware, only renders after App)
    await expect(page.locator('.topbar')).toBeVisible()
    await expect(page.locator('.sidebar')).toBeVisible()
    await expect(page.locator('.main .view')).toBeVisible()
    // The overhaul board renders the slot-list anchor (its StatusDots are the
    // clearest, data-independent marker that the new surface mounted) …
    await expect(page.locator('.sdot').first()).toBeVisible()
    // … and the dashboard exposes the Customize (edit-mode) toggle.
    await expect(page.getByRole('button', { name: /customize/i }).first()).toBeVisible()
    // sidebar active row should be the dashboard (labeled "Overview" in v0.5 nav)
    await expect(page.locator('.sb-row.active .lbl')).toHaveText('Overview')
  })

  test('topbar exposes brand + Kanban/Agent Chat launchers without stale host/bell chrome', async ({ page }) => {
    await page.goto('/')
    await expect(page.locator('.tb-brand')).toBeVisible()
    // The old "Quick actions" button was replaced by two launchers.
    await expect(page.locator('[data-testid="tb-launch-board"]')).toBeVisible()
    await expect(page.locator('[data-testid="tb-launch-board"]')).toContainText('Kanban')
    await expect(page.locator('[data-testid="tb-launch-chat"]')).toBeVisible()
    await expect(page.locator('[data-testid="tb-launch-chat"]')).toContainText('Agent Chat')
    await expect(page.locator('.tb-cmdk')).toHaveCount(0)
    await expect(page.locator('.tb-host')).toHaveCount(0)
    await expect(page.locator('.tb-bell')).toHaveCount(0)
  })
})
