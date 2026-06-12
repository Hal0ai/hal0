/**
 * inference-pane-v3 — the Inference "engine" pane (slots-page Inference tab).
 *
 * Behaviour under test:
 *   - collapsed hero strip renders from HAL0_DATA slots (epill + active list)
 *   - the qcaret toggles the engine open (→ full slot list + by-device split)
 *   - per-slot tok/s is shown for a serving slot (no fabricated numbers)
 *   - a lifecycle control fires the real mutation (Stop → POST /unload)
 *   - the expanded list exposes a real model-picker <select> (useModels)
 *   - the NPU/FLM pane renders its own engine shell + trio
 *   - the Inference tab carries the yellow `infer` accent class
 *
 * The slot LIST comes from in-bundle HAL0_DATA (VITE_MOCK_HAL0=1); mutations
 * go through fetch, so per-route stubs capture the write path.
 *
 * NOTE on expand assertions: the engine body animates via `max-height:0;
 * overflow:hidden`, which clips but does NOT zero the bounding box of its
 * children — Playwright still reports them "visible". So collapse/expand is
 * asserted on the `.engine.open` class (the real state signal), not on inner
 * content visibility.
 */
import { test, expect, type Page } from '../fixtures/apiMock'

const pane = (page: Page) => page.locator('.infer-pane').first()
const engine = (page: Page) => page.locator('.infer-pane .engine').first()
const body = (page: Page) => page.locator('.infer-pane .engine-body').first()

test.describe('Inference engine pane (/slots · Inference tab)', () => {
  test('collapsed strip renders with epill + active slot rows', async ({ page }) => {
    await page.goto('/#slots')
    await expect(pane(page)).toBeVisible()
    await expect(engine(page)).not.toHaveClass(/\bopen\b/)
    // engine state pill summarises serving/loaded counts (primary is serving).
    await expect(page.getByTestId('infer-epill')).toContainText('serving')
    // collapsed hero strip is visible and lists the serving primary slot.
    const strip = page.getByTestId('infer-strip')
    await expect(strip).toBeVisible()
    await expect(strip.getByTestId('infer-slot-primary')).toBeVisible()
    await expect(strip.locator('.mem .blk-h')).toContainText('memory map')
  })

  test('qcaret toggles the engine open → full list + by-device split', async ({ page }) => {
    await page.goto('/#slots')
    await expect(engine(page)).not.toHaveClass(/\bopen\b/)
    await page.getByTestId('infer-qcaret').click()
    await expect(engine(page)).toHaveClass(/\bopen\b/)
    // full-list header (actions column) + by-device split live in the body.
    await expect(body(page).locator('.slist .sh')).toContainText('actions')
    await expect(body(page).locator('.tp-split')).toHaveCount(1)
    // per-slot tok/s rendered for the serving primary slot (45 in the seed).
    await expect(body(page).getByTestId('infer-slot-primary').locator('.met').first()).toContainText(
      '45',
    )
    // toggle back closed.
    await page.getByTestId('infer-qcaret').click()
    await expect(engine(page)).not.toHaveClass(/\bopen\b/)
  })

  test('expanded Stop control POSTs /unload for a running slot', async ({ page }) => {
    const unloads: string[] = []
    await page.route('**/api/slots/primary/unload', async (route) => {
      unloads.push(route.request().url())
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
    })
    await page.goto('/#slots')
    await page.getByTestId('infer-qcaret').click()
    await expect(engine(page)).toHaveClass(/\bopen\b/)
    await body(page).getByTestId('infer-slot-primary').locator('.sctrl.stop').click()
    await expect.poll(() => unloads.length).toBeGreaterThan(0)
  })

  test('expanded list exposes a real model-picker <select> (useModels)', async ({ page }) => {
    await page.goto('/#slots')
    // The full-list model picker lives in the (collapsible) engine body; it is
    // present in the DOM regardless of open state, so assert on it directly —
    // no flaky expand-click needed for a structural check.
    const picker = body(page).getByTestId('infer-slot-primary').locator('select.slist-picker')
    await expect(picker).toHaveCount(1)
    // the picker is populated (at minimum the current model is an option).
    await expect.poll(async () => picker.locator('option').count()).toBeGreaterThan(0)
  })
})

test.describe('NPU · FLM engine pane (/slots · Inference tab)', () => {
  test('renders an engine shell with a collapsed strip', async ({ page }) => {
    await page.goto('/#slots')
    await expect(page.locator('.npu-pane').first()).toBeVisible()
    await expect(page.getByTestId('npu-epill')).toBeVisible()
    await expect(page.getByTestId('npu-strip')).toBeVisible()
  })

  test('caret toggles the trio open (Chat / ASR / Embed)', async ({ page }) => {
    await page.goto('/#slots')
    const npuEngine = page.locator('.npu-pane .engine').first()
    await expect(npuEngine).not.toHaveClass(/\bopen\b/)
    await page.getByTestId('npu-qcaret').click()
    await expect(npuEngine).toHaveClass(/\bopen\b/)
    await expect(page.locator('.npu-pane .engine-body .npu-mod')).toHaveCount(3)
  })
})

test.describe('Inference tab accent', () => {
  test('the Inference tab carries the yellow infer accent class', async ({ page }) => {
    await page.goto('/#slots')
    const tab = page.locator('.slot-tab.infer').first()
    await expect(tab).toBeVisible()
    await expect(tab).toHaveClass(/\bon\b/)
  })
})
