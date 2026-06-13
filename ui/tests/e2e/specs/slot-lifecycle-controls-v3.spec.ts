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
 * Chat/LLM slots moved into the InferencePane; the SlotCard grid now holds the
 * Capabilities slots, so we target a capability (reranking) `.slot` card in
 * each lifecycle state. Slots are injected via window.HAL0_DATA (VITE_MOCK_HAL0);
 * mutations still go through fetch, so per-route stubs capture them.
 */
import { test, expect, type Page } from '../fixtures/apiMock'

const cardByName = (page: Page, name: string) =>
  page
    .locator('.slot', { has: page.locator('.slot-name .nm', { hasText: new RegExp(`^${name}$`) }) })
    .first()

// Inject a single capability slot (renders in the Capabilities grid as a
// `.slot` card) via the HAL0_DATA setter the dashboard reads on boot.
async function seedCapSlot(page: Page, slot: any) {
  await page.addInitScript((s) => {
    let real: any
    Object.defineProperty(window, 'HAL0_DATA', {
      configurable: true,
      get() { return real },
      set(v) { real = v; if (v && typeof v === 'object') v.slots = [s] },
    })
  }, slot)
}

const CAP = {
  name: 'rerank', type: 'reranking', device: 'gpu-rocm', group: 'embed',
  model: 'bge-reranker-v2-m3', model_id: 'bge-reranker-v2-m3',
  enabled: true, runtime: 'container', port: 8083,
}

test.describe('Slot lifecycle controls (/slots)', () => {
  test('off slot (stopped) shows Start, not Stop/Restart; Start POSTs /load', async ({ page }) => {
    const loads: string[] = []
    await page.route('**/api/slots/rerank/load', async (route) => {
      loads.push(route.request().url())
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
    })
    await seedCapSlot(page, {
      ...CAP, state: 'offline', container_status: 'stopped', container_health: false,
    })

    await page.goto('/#slots')
    const card = cardByName(page, 'rerank')
    await expect(card.getByRole('button', { name: 'Start', exact: true })).toBeVisible()
    await expect(card.locator('button:has-text("Stop")')).toHaveCount(0)
    await expect(card.locator('button:has-text("Restart")')).toHaveCount(0)

    await card.getByRole('button', { name: 'Start', exact: true }).click()
    await expect.poll(() => loads.length).toBeGreaterThan(0)
  })

  test('running slot (serving) shows Stop + Restart, not Start; Stop POSTs /unload', async ({ page }) => {
    const unloads: string[] = []
    await page.route('**/api/slots/rerank/unload', async (route) => {
      unloads.push(route.request().url())
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
    })
    await seedCapSlot(page, {
      ...CAP, state: 'serving', container_status: 'running', container_health: true,
    })

    await page.goto('/#slots')
    const card = cardByName(page, 'rerank')
    await expect(card.locator('button:has-text("Stop")')).toBeVisible()
    await expect(card.locator('button:has-text("Restart")')).toBeVisible()
    await expect(card.getByRole('button', { name: 'Start', exact: true })).toHaveCount(0)

    await card.locator('button:has-text("Stop")').click()
    await expect.poll(() => unloads.length).toBeGreaterThan(0)
  })

  test('transitional slot (warming) shows no Start and a disabled Restart', async ({ page }) => {
    await seedCapSlot(page, {
      ...CAP, state: 'warming', container_status: 'starting', container_health: false,
    })
    await page.goto('/#slots')
    const card = cardByName(page, 'rerank')
    await expect(card).toBeVisible()
    await expect(card.getByRole('button', { name: 'Start', exact: true })).toHaveCount(0)
    await expect(card.locator('button:has-text("Restart")')).toBeDisabled()
  })
})
