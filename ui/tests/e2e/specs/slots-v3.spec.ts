/**
 * slots-v3 — `#slots` route. The standalone Chat + Capabilities SlotCard grids
 * were retired: the InferencePane is now the single per-slot surface for all
 * iGPU/CPU inference slots, with the NPU·FLM stack in its own pane. (Per-slot
 * lifecycle/swap coverage lives in inference-pane-v3.)
 */
import { test, expect } from '../fixtures/apiMock'

test.describe('Slots v3 (/slots)', () => {
  test('renders the Inference engine pane with slot cards', async ({ page }) => {
    await page.goto('/#slots')
    await expect(page.locator('.view .vh h1')).toHaveText('Slots')
    // The InferencePane is the single per-slot surface (no group grids).
    await expect(page.locator('.infer-pane').first()).toBeVisible()
    expect(await page.locator('.infer-pane .scard').count()).toBeGreaterThan(0)
    // The legacy group grids are gone.
    await expect(page.locator('.slots-grid')).toHaveCount(0)
  })

  test('exposes New-slot button (create modal trigger)', async ({ page }) => {
    await page.goto('/#slots')
    const newBtn = page.locator('.view .vh button:has-text("New slot")')
    await expect(newBtn).toBeVisible()
  })

  test('NPU occupancy card renders when an NPU slot is present', async ({ page }) => {
    await page.goto('/#slots')
    // The NPU/FLM surface is now a single full-width "NPU occupancy" card
    // (gauge + 4×8 AIE-ML map + per-FLM-slot rail), replacing the old
    // NpuFlmStack accordion + trio picker. HAL0_DATA seeds at least one
    // device=npu slot, so the card appears.
    await expect(page.locator('.npu-card')).toBeVisible()
    await expect(page.locator('.npu-card .wcard-h .ttl')).toContainText('NPU occupancy')
  })

  test('NPU occupancy card shows the AIE grid + a per-FLM-slot card', async ({ page }) => {
    await page.goto('/#slots')
    const card = page.locator('.npu-card')
    await expect(card).toBeVisible()
    // 4×8 AIE-ML occupancy grid (single-tenant: one FLM claims the whole array)
    await expect(card.locator('.aie-grid .aie-col')).toHaveCount(8)
    // at least one resident FLM slot card with its column strip
    await expect(card.locator('.cslot .cslot-strip').first()).toBeVisible()
  })
})
