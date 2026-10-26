/**
 * slot-rail-v3 — the slot rail beside the Inference pane (Slots page). It replaced the Activity
 * sidebar: a roster of every slot, grouped by device, with a "N / M ready" count that matches the
 * dock's slots pip, and a row flash when the dock reports an event about a slot. The Image Gen
 * route has no sidebar at all.
 *
 * `/api/slots` is served from `window.HAL0_DATA.slots` in the forced-mock build, so the roster is
 * seeded there (`seedSlots`). The rail lists EVERY slot; the dock's slots pip counts the slots
 * with a model bound (useRuntimeRollup). They agree only when every slot has a model, so the
 * seed binds one to each.
 */
const SLOTS = (MOCK_DATA.slots as any[]).filter((s) => !s._synthetic && (s.model || s.model_default))
const NAMES = SLOTS.map((s) => s.name)

test.beforeEach(async ({ page }) => {
  await seedSlots(page, SLOTS)
})

import { test, expect, seedSlots } from '../fixtures/apiMock'
import { MOCK_DATA } from '../fixtures/mock-data'

const fraction = (text: string | null) => {
  const m = /(\d+)\s*\/\s*(\d+)\s*ready/.exec(text ?? '')
  return m ? [Number(m[1]), Number(m[2])] : null
}

test.describe('Slot rail (#slots/inference)', () => {
  test('is the Slots sidebar, with one row per slot', async ({ page }) => {
    await page.goto('/#slots/inference')
    const rail = page.locator('.dash-side [data-testid="slot-rail"]')
    await expect(rail).toBeVisible()
    await expect(page.getByTestId('slot-rail-new')).toBeVisible()
    await expect(page.getByTestId('slot-rail-footer')).toBeVisible()

    const names = NAMES
    expect(names.length).toBeGreaterThan(0)
    const rows = rail.getByTestId('slot-rail-row')
    await expect(rows).toHaveCount(names.length)
    const seen = await rows.evaluateAll((els) => els.map((el) => el.getAttribute('data-slot')))
    expect([...seen].sort()).toEqual([...names].sort())
  })

  test('its ready count matches the dock slots fraction', async ({ page }) => {
    await page.goto('/#slots/inference')
    const count = page.getByTestId('slot-rail-count')
    await expect(count).toContainText('ready')
    const dock = page.getByTestId('foot-health-runtimes').locator('.lbl .v')
    await expect(dock).toContainText('ready')

    const rail = fraction(await count.textContent())
    const foot = fraction(await dock.textContent())
    expect(rail).not.toBeNull()
    expect(rail).toEqual(foot)
  })

  test('is absent on #slots/image, which is solo', async ({ page }) => {
    await page.goto('/#slots/image')
    await expect(page.locator('.dash.dash-solo')).toBeVisible()
    await expect(page.getByTestId('slot-rail')).toHaveCount(0)
    await expect(page.locator('.dash-side')).toHaveCount(0)
  })

  test('hal0:slot-event flashes the row', async ({ page }) => {
    await page.goto('/#slots/inference')
    const name = NAMES[0]
    const row = page.locator(`[data-testid="slot-rail-row"][data-slot="${name}"]`)
    await expect(row).toBeVisible()
    await expect(row).not.toHaveClass(/\bflash\b/)

    await page.evaluate((slot) => window.dispatchEvent(new CustomEvent('hal0:slot-event', { detail: { slot } })), name)
    await expect(row).toHaveClass(/\bflash\b/)
    // The flash is a window, not a state: it clears by itself.
    await expect(row).not.toHaveClass(/\bflash\b/, { timeout: 4_000 })

    // An event about something that is not a slot flashes nothing.
    await page.evaluate(() =>
      window.dispatchEvent(new CustomEvent('hal0:slot-event', { detail: { slot: 'no-such-slot' } })),
    )
    await expect(page.locator('[data-testid="slot-rail-row"].flash')).toHaveCount(0)
  })
})
