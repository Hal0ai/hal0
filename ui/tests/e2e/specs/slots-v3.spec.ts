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

  test('the NPU summary row renders when an NPU slot is present', async ({ page }) => {
    await page.goto('/#slots')
    // HAL0_DATA seeds at least one device=npu slot; the Inference tab shows a one-line summary.
    await expect(page.getByTestId('npu-summary-row')).toBeVisible()
  })

  test('the NPU tab shows the AIE grid + an FLM lane', async ({ page }) => {
    await page.goto('/#slots/npu')
    const tab = page.getByTestId('npu-pane')
    await expect(tab).toBeVisible()
    // 4×8 AIE-ML occupancy grid (single-tenant: one FLM claims the whole array)
    await expect(tab.locator('.aie-grid .aie-col')).toHaveCount(8)
    await expect(page.getByTestId('flm-lane')).toBeVisible()
  })
})
