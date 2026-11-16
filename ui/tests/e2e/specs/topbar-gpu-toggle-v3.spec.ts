/**
 * topbar-gpu-toggle-v3 — the top bar's right side: Docs and the notification bell as plain icons,
 * then the launcher group (the GPU mode toggle beside Agent Chat), and the toggle collapsing to
 * icons at phone width. The toggle's behaviour (hold, keep, stop and their confirms) is in
 * image-gen-setup-v3.spec.ts and the vitest suite; this spec is the layout.
 */
import { test, expect } from '../fixtures/apiMock'
import { gotoImageGen, makeImageGenMock, referenceBoxMemory, routeImageGen } from '../fixtures/imageGen'

test.describe('Top bar: GPU toggle and launchers', () => {
  test('order, left to right: docs, bell, GPU toggle, Agent Chat; docs and bell are plain icons', async ({ page }) => {
    await routeImageGen(page, makeImageGenMock({ memory: referenceBoxMemory() }))
    await gotoImageGen(page)

    const box = async (testid: string) => (await page.getByTestId(testid).boundingBox())!
    const docs = await box('tb-docs')
    const bell = await box('tb-bell')
    const toggle = await box('tb-gpu-toggle')
    const chat = await box('tb-launch-chat')
    expect(docs.x + docs.width).toBeLessThanOrEqual(bell.x)
    expect(bell.x + bell.width).toBeLessThanOrEqual(toggle.x)
    expect(toggle.x + toggle.width).toBeLessThanOrEqual(chat.x)

    // One group: the toggle and the launcher sit in the same container, at the same height.
    const group = page.locator('.tb-launch')
    await expect(group.getByTestId('tb-gpu-toggle')).toBeVisible()
    await expect(group.getByTestId('tb-launch-chat')).toBeVisible()
    expect(Math.abs(toggle.height - chat.height)).toBeLessThanOrEqual(1)

    // Docs and the bell have no button chrome, and keep their names.
    for (const id of ['tb-docs', 'tb-bell']) {
      const el = page.getByTestId(id)
      await expect(el).toHaveCSS('border-top-width', '0px')
      await expect(el).toHaveCSS('background-color', 'rgba(0, 0, 0, 0)')
    }
    await expect(page.getByTestId('tb-docs')).toHaveAttribute('aria-label', 'Documentation')
    await expect(page.getByTestId('tb-bell')).toHaveAttribute('aria-label', /^Notifications/)

    // Still reachable by keyboard.
    await page.getByTestId('tb-docs').focus()
    await expect(page.getByTestId('tb-docs')).toBeFocused()
  })

  test('the toggle shares the launcher button family: same border, radius and background', async ({ page }) => {
    await routeImageGen(page, makeImageGenMock({ memory: referenceBoxMemory() }))
    await gotoImageGen(page)
    const toggle = page.getByTestId('tb-gpu-toggle')
    const chat = page.getByTestId('tb-launch-chat')
    for (const prop of ['border-top-width', 'border-top-left-radius', 'background-color', 'font-size']) {
      const a = await toggle.evaluate((el, p) => getComputedStyle(el).getPropertyValue(p), prop)
      const b = await chat.evaluate((el, p) => getComputedStyle(el).getPropertyValue(p), prop)
      expect(a, prop).toBe(b)
    }
  })

  test.describe('phone width', () => {
    test.use({ viewport: { width: 375, height: 812 } })

    test('the toggle is icons only, nothing overflows, and the hamburger stays last', async ({ page }) => {
      await routeImageGen(page, makeImageGenMock({ memory: referenceBoxMemory() }))
      await gotoImageGen(page)

      await expect(page.getByTestId('tb-gpu-toggle')).toBeVisible()
      await expect(page.locator('.tb-gpu-lbl').first()).toBeHidden()
      // The segments keep their names without their words.
      await expect(page.getByRole('radio', { name: 'Chat' })).toBeVisible()
      await expect(page.getByRole('radio', { name: 'Image' })).toBeVisible()
      // The Agent Chat launcher is folded into the nav drawer on phones.
      await expect(page.getByTestId('tb-launch-chat')).toBeHidden()

      const overflow = await page.evaluate(() => ({
        doc: document.documentElement.scrollWidth - document.documentElement.clientWidth,
        bar: (() => {
          const bar = document.querySelector('.topbar') as HTMLElement
          return bar.scrollWidth - bar.clientWidth
        })(),
      }))
      expect(overflow.doc).toBeLessThanOrEqual(0)
      expect(overflow.bar).toBeLessThanOrEqual(0)

      const toggle = (await page.getByTestId('tb-gpu-toggle').boundingBox())!
      const burger = (await page.locator('.tb-menu').boundingBox())!
      expect(toggle.x + toggle.width).toBeLessThanOrEqual(burger.x)
      expect(burger.x + burger.width).toBeLessThanOrEqual(375)

      // The question opens within the viewport.
      await page.getByTestId('tb-gpu-image').click()
      const pop = (await page.getByTestId('tb-gpu-pop').boundingBox())!
      expect(pop.x).toBeGreaterThanOrEqual(0)
      expect(pop.x + pop.width).toBeLessThanOrEqual(375)
    })
  })
})
