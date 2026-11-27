/**
 * mobile-tabbar-v3 — the phone bottom tab bar (≤720px).
 *
 * Overview, Slots, Models and Agents navigate; More opens the NavDrawer, which keeps the full nav
 * and is the only way to open it (the top-bar hamburger is gone at phone width). The bar and the
 * compact dock together must leave the page most of a 390x844 screen, and nothing may hide under
 * the bar. Desktop width never shows it. (The first bottom-tab bar never showed at all, because
 * its hidden base rule sat after its media query; the first test pins that it is really visible.)
 */
import { test, expect } from '../fixtures/apiMock'

const PHONE = { width: 390, height: 844 }

test.describe('Phone tab bar (≤720px)', () => {
  test.use({ viewport: PHONE })

  test('the bar is visible, labelled Primary, and its tabs are at least 48px tall', async ({ page }) => {
    await page.goto('/#dashboard')
    const bar = page.getByRole('navigation', { name: 'Primary' })
    await expect(bar).toBeVisible()
    for (const label of ['Overview', 'Slots', 'Models', 'Agents', 'More']) {
      const tab = bar.getByText(label, { exact: true })
      await expect(tab).toBeVisible()
    }
    for (const id of ['dashboard', 'slots', 'models', 'agent']) {
      expect((await page.getByTestId('tabbar-' + id).boundingBox())!.height).toBeGreaterThanOrEqual(48)
    }
    expect((await page.getByTestId('tabbar-more').boundingBox())!.height).toBeGreaterThanOrEqual(48)
    // It is a `<button>` or `<a>`, never a div.
    expect(await page.getByTestId('tabbar-more').evaluate((el) => el.tagName)).toBe('BUTTON')
    expect(await page.getByTestId('tabbar-slots').evaluate((el) => el.tagName)).toBe('A')
  })

  test('each tab navigates and carries aria-current', async ({ page }) => {
    await page.goto('/#dashboard')
    for (const [id, hash] of [['slots', /#slots/], ['models', /#models/], ['agent', /#agent/], ['dashboard', /#dashboard/]] as const) {
      await page.getByTestId('tabbar-' + id).click()
      await expect(page).toHaveURL(hash)
      await expect(page.getByTestId('tabbar-' + id)).toHaveAttribute('aria-current', 'page')
      await expect(page.locator('.tabbar [aria-current="page"]')).toHaveCount(1)
    }
  })

  test('a tab stays active across its whole section; #mcp counts as Agents', async ({ page }) => {
    await page.goto('/#mcp')
    await expect(page.getByTestId('tabbar-agent')).toHaveAttribute('aria-current', 'page')
    await page.goto('/#models/providers')
    await expect(page.getByTestId('tabbar-models')).toHaveAttribute('aria-current', 'page')
    await page.goto('/#slots/endpoints')
    await expect(page.getByTestId('tabbar-slots')).toHaveAttribute('aria-current', 'page')
    // A page only the drawer reaches lights More, not a tab, and More is never aria-current.
    await page.goto('/#logs')
    await expect(page.locator('.tabbar [aria-current="page"]')).toHaveCount(0)
    await expect(page.getByTestId('tabbar-more')).toHaveClass(/\bactive\b/)
    await expect(page.getByTestId('tabbar-more')).not.toHaveAttribute('aria-current', /.*/)
  })

  test('More opens the drawer, which closes on Escape, backdrop and navigation', async ({ page }) => {
    await page.goto('/#dashboard')
    const more = page.getByTestId('tabbar-more')
    const drawer = page.locator('.nav-drawer')
    await expect(page.locator('.tb-menu')).toBeHidden()
    await expect(more).toHaveAttribute('aria-expanded', 'false')
    await more.click()
    await expect(drawer).toHaveClass(/\bopen\b/)
    await expect(more).toHaveAttribute('aria-expanded', 'true')
    await expect(drawer.getByTestId('nav-drawer-memory')).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(drawer).not.toHaveClass(/\bopen\b/)
    await more.click()
    await page.locator('.nav-drawer-backdrop').click({ position: { x: 10, y: 10 } })
    await expect(drawer).not.toHaveClass(/\bopen\b/)
    await more.click()
    await drawer.getByTestId('nav-drawer-services').click()
    await expect(page).toHaveURL(/#services/)
    await expect(drawer).not.toHaveClass(/\bopen\b/)
  })

  test('the bar and the dock together take no more than 104px, and sit flush at the bottom', async ({ page }) => {
    await page.goto('/#dashboard')
    const bar = (await page.getByTestId('tabbar').boundingBox())!
    const dock = (await page.getByTestId('dock').boundingBox())!
    expect(bar.height + dock.height).toBeLessThanOrEqual(104)
    expect(bar.y + bar.height).toBeCloseTo(PHONE.height, 0)
    expect(dock.y + dock.height).toBeCloseTo(bar.y, 0)
    // The toggle is on screen and big enough to tap, and the dock still expands.
    const toggle = page.getByTestId('dock-toggle')
    const tb = (await toggle.boundingBox())!
    expect(tb.x + tb.width).toBeLessThanOrEqual(PHONE.width)
    expect(tb.width).toBeGreaterThanOrEqual(44)
    await toggle.click()
    await expect(page.getByTestId('dock-expanded')).toBeVisible()
    await expect(toggle).toHaveAttribute('aria-expanded', 'true')
  })

  test('the last element of the Overview scrolls fully above the bar', async ({ page }) => {
    await page.goto('/#dashboard')
    await page.locator('.main').evaluate((el) => { el.scrollTop = el.scrollHeight })
    const barTop = (await page.getByTestId('tabbar').boundingBox())!.y
    const last = await page.locator('.main .view').evaluate((view) => {
      const kids = Array.from(view.querySelectorAll('*')).filter((e) => e.getBoundingClientRect().height > 0)
      return Math.max(...kids.map((e) => e.getBoundingClientRect().bottom))
    })
    expect(last).toBeLessThanOrEqual(barTop)
  })

  test('the notifications popover and a toast are not covered by the bar', async ({ page }) => {
    await page.goto('/#dashboard')
    await page.getByTestId('tb-bell').click()
    const pop = page.locator('.notif-pop')
    await expect(pop).toBeVisible()
    const pb = (await pop.boundingBox())!
    expect(pb.y + pb.height).toBeLessThanOrEqual(PHONE.height)
    const topEl = await page.evaluate(({ x, y }) => document.elementFromPoint(x, y)?.closest('.tabbar') != null, { x: pb.x + pb.width / 2, y: pb.y + pb.height - 2 })
    expect(topEl).toBe(false)
    await page.keyboard.press('Escape')

    await page.evaluate(() => (window as any).__hal0Toast?.('phone toast', 'info'))
    const toast = page.locator('.hal0-toast', { hasText: 'phone toast' })
    await expect(toast).toBeVisible()
    const tb = (await toast.boundingBox())!
    const barTop = (await page.getByTestId('tabbar').boundingBox())!.y
    expect(tb.y + tb.height).toBeLessThanOrEqual(barTop)
    const covered = await page.evaluate(({ x, y }) => document.elementFromPoint(x, y)?.closest('.tabbar') != null, { x: tb.x + tb.width / 2, y: tb.y + tb.height - 2 })
    expect(covered).toBe(false)
  })

  test('drawers and the nav drawer sit above the bar', async ({ page }) => {
    await page.goto('/#dashboard')
    await page.getByTestId('tabbar-more').click()
    await expect(page.locator('.nav-drawer')).toHaveClass(/\bopen\b/)
    // A point on the bar's right edge is the drawer (it is z 100, the bar 80), not a tab.
    // Polled: the drawer slides in over 0.22s.
    await expect.poll(() => page.evaluate(() => {
      const r = document.querySelector('.tabbar')!.getBoundingClientRect()
      const el = document.elementFromPoint(window.innerWidth - 20, r.top + 20)
      return el?.closest('.nav-drawer') != null
    })).toBe(true)
  })
})

test.describe('Desktop (>720px)', () => {
  test('the tab bar is hidden and the sidebar is the nav', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 })
    await page.goto('/#dashboard')
    await expect(page.locator('.tabbar')).toBeHidden()
    await expect(page.locator('.sidebar')).toBeVisible()
    // The dock is the desktop one: two rows under the chips, not the one-line phone dock.
    expect((await page.getByTestId('dock').boundingBox())!.height).toBeGreaterThanOrEqual(46)
  })
})
