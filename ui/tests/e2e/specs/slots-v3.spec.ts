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

  test('NPU rollup section renders when an NPU slot is present', async ({ page }) => {
    await page.goto('/#slots')
    // The NPU/FLM stack now renders as its own "engine" pane (parallel to the
    // ComfyUI + Inference panes) instead of a plain <section><h2>NPU</h2> — the
    // standalone section header was dropped for the pane's own engine header.
    // HAL0_DATA seeds at least one device=npu slot, so the pane appears.
    await expect(page.locator('.npu-pane')).toBeVisible()
    await expect(page.locator('.npu-pane .engine-title')).toContainText('NPU')
  })

  test('NPU trio: chat is a model picker; ASR/embed are read-only labels (model fixed by FLM)', async ({ page }) => {
    await page.goto('/#slots')
    const stack = page.locator('.npu-stack')
    await expect(stack).toBeVisible()
    // Chat (the FLM anchor) keeps a real <select> model picker (the shared
    // slot-card ModelPicker); ASR/embed render an FLM-fixed read-only label.
    await expect(stack.locator('.model-picker')).toHaveCount(1)
    await expect(stack.locator('.npu-mod-fixed')).toHaveCount(2)
    await expect(stack.locator('.npu-mod-fixed .npu-fixed-tag')).toHaveCount(2)
  })
})
