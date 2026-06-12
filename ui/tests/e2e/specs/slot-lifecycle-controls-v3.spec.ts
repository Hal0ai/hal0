/**
 * slot-lifecycle-controls-v3 — SlotCard lifecycle buttons reflect state.
 *
 * Behaviour under test (design 2026-06-04):
 *   - running slot (loaded/serving/ready) → Stop + Restart, no Start
 *   - off slot (idle/unloaded/offline/disabled) → Start, no Stop/Restart
 *   - transitional slot (warming/pulling/unloading) → no Start, Restart disabled
 *
 * Start → POST /load · Stop → POST /unload · Restart → POST /restart.
 *
 * The dashboard renders the slot LIST from in-bundle HAL0_DATA
 * (VITE_MOCK_HAL0=1), so we target real seed slots by exact name:
 *   primary = serving (running) · coder = stopped via HAL0_DATA clobber
 *   (the seed runs its container) · warming-demo = starting (transitional).
 * Mutations still go through fetch, so per-route stubs capture them.
 */
import { test, expect, type Page } from '../fixtures/apiMock'

const cardByName = (page: Page, name: string) =>
  page
    .locator('.slot', { has: page.locator('.slot-name .nm', { hasText: new RegExp(`^${name}$`) }) })
    .first()

test.describe('Slot lifecycle controls (/slots)', () => {
  test('off slot (coder/stopped) shows Start, not Stop/Restart; Start POSTs /load', async ({ page }) => {
    const loads: string[] = []
    await page.route('**/api/slots/coder/load', async (route) => {
      loads.push(route.request().url())
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
    })

    // The seed coder slot runs its container — stop it so the card takes
    // the "off" branch (container stopped → Start only).
    await page.addInitScript(() => {
      const id = setInterval(() => {
        const d = (window as any).HAL0_DATA
        const coder = d?.slots?.find((s: any) => s.name === 'coder')
        if (coder) {
          coder.container_status = 'stopped'
          coder.container_health = false
          coder.state = 'offline'
          clearInterval(id)
        }
      }, 5)
    })

    await page.goto('/#slots')
    const card = cardByName(page, 'coder')
    await expect(card.getByRole("button", { name: "Start", exact: true })).toBeVisible()
    await expect(card.locator('button:has-text("Stop")')).toHaveCount(0)
    await expect(card.locator('button:has-text("Restart")')).toHaveCount(0)

    await card.getByRole("button", { name: "Start", exact: true }).click()
    await expect.poll(() => loads.length).toBeGreaterThan(0)
  })

  test('running slot (primary/serving) shows Stop + Restart, not Start; Stop POSTs /unload', async ({ page }) => {
    const unloads: string[] = []
    await page.route('**/api/slots/primary/unload', async (route) => {
      unloads.push(route.request().url())
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
    })

    await page.goto('/#slots')
    const card = cardByName(page, 'primary')
    await expect(card.locator('button:has-text("Stop")')).toBeVisible()
    await expect(card.locator('button:has-text("Restart")')).toBeVisible()
    await expect(card.getByRole("button", { name: "Start", exact: true })).toHaveCount(0)

    await card.locator('button:has-text("Stop")').click()
    await expect.poll(() => unloads.length).toBeGreaterThan(0)
  })

  test('transitional slot (warming) shows no Start and a disabled Restart', async ({ page }) => {
    await page.goto('/#slots')
    const card = cardByName(page, 'warming-demo')
    await expect(card).toBeVisible()
    await expect(card.getByRole("button", { name: "Start", exact: true })).toHaveCount(0)
    await expect(card.locator('button:has-text("Restart")')).toBeDisabled()
  })
})
