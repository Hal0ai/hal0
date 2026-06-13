/**
 * mobile-nav-v3 — at ≤720px the desktop sidebar is hidden and navigation
 * moves into a top-right hamburger (.tb-menu) that opens a slide-in
 * NavDrawer: command-palette launcher (folded in from the topbar), the full
 * nav (useNavItems — incl. Logs + MCP the old bottom-tabs never reached),
 * and the runtime status widget. Replaces the never-shown bottom-tab bar.
 */
import { test, expect } from '../fixtures/apiMock'

const MOBILE = { width: 375, height: 812 }

test.describe('Mobile nav drawer (≤720px)', () => {
  test.use({ viewport: MOBILE })

  test('hamburger replaces the sidebar and opens a full-nav drawer', async ({ page }) => {
    await page.goto('/')
    await expect(page.locator('.topbar')).toBeVisible()
    // the desktop sidebar collapses out at mobile width
    await expect(page.locator('.sidebar')).toBeHidden()

    // hamburger present, drawer starts closed (off-canvas → no .open class)
    const burger = page.locator('.tb-menu')
    await expect(burger).toBeVisible()
    await expect(burger).toHaveAttribute('aria-expanded', 'false')
    const drawer = page.locator('.nav-drawer')
    await expect(drawer).not.toHaveClass(/\bopen\b/)

    // open
    await burger.click()
    await expect(drawer).toHaveClass(/\bopen\b/)
    await expect(burger).toHaveAttribute('aria-expanded', 'true')
    await expect(page.locator('.nav-drawer-backdrop.open')).toBeVisible()

    // full nav — including the destinations the dead bottom tabs never reached
    for (const label of ['Dashboard', 'Slots', 'Models', 'Logs', 'Connections', 'Settings']) {
      await expect(drawer.locator('.sb-row .lbl', { hasText: label })).toBeVisible()
    }
    // command palette folded into the drawer on mobile
    await expect(drawer.locator('.nav-drawer-cmdk')).toBeVisible()
    // runtime widget re-shown inside the drawer (despite the 1080px global hide)
    await expect(drawer.locator('.sb-status').first()).toBeVisible()
  })

  test('selecting a destination navigates and closes the drawer', async ({ page }) => {
    await page.goto('/')
    await page.locator('.tb-menu').click()
    const drawer = page.locator('.nav-drawer')
    await expect(drawer).toHaveClass(/\bopen\b/)

    await drawer.locator('.sb-row', { hasText: 'Models' }).click()
    await expect(page).toHaveURL(/#models/)
    await expect(drawer).not.toHaveClass(/\bopen\b/)
  })

  test('backdrop click closes the drawer', async ({ page }) => {
    await page.goto('/')
    await page.locator('.tb-menu').click()
    const drawer = page.locator('.nav-drawer')
    await expect(drawer).toHaveClass(/\bopen\b/)

    // top-left corner is backdrop (the panel itself sits on the right)
    await page.locator('.nav-drawer-backdrop').click({ position: { x: 10, y: 10 } })
    await expect(drawer).not.toHaveClass(/\bopen\b/)
  })

  test('Escape closes the drawer', async ({ page }) => {
    await page.goto('/')
    await page.locator('.tb-menu').click()
    const drawer = page.locator('.nav-drawer')
    await expect(drawer).toHaveClass(/\bopen\b/)

    await page.keyboard.press('Escape')
    await expect(drawer).not.toHaveClass(/\bopen\b/)
  })
})

test.describe('Desktop nav (>720px)', () => {
  test('hamburger is hidden and the sidebar is the nav', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 })
    await page.goto('/')
    await expect(page.locator('.sidebar')).toBeVisible()
    await expect(page.locator('.tb-menu')).toBeHidden()
  })
})
