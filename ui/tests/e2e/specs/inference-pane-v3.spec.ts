/**
 * inference-pane-v3 — the Inference "engine" pane (slots-page Inference tab),
 * P2 card direction (design_handoff_inference_slots).
 *
 * Behaviour under test:
 *   - collapsed hero strip renders from HAL0_DATA slots: epill, iGPU GTT
 *     memory map, combined-throughput tile, and compact slot CARDS
 *   - the serving card's status pill shows live tok/s (no fabricated numbers)
 *   - the profile pill surfaces the slot's runtime profile name (slot.profile)
 *   - NPU/FLM slots are cordoned off — absent from the inference pane, present
 *     in the NPU · FLM stack pane below
 *   - the qcaret toggles the engine open (→ full cards with tok/s · ttft · ctx)
 *   - a lifecycle control fires the real mutation (Stop → POST /unload)
 *   - the full card exposes a real model-picker <select> (useModels)
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
  test('collapsed strip renders epill + hero band + compact slot cards', async ({ page }) => {
    await page.goto('/#slots')
    await expect(pane(page)).toBeVisible()
    await expect(engine(page)).not.toHaveClass(/\bopen\b/)
    // engine state pill summarises serving/loaded counts (primary is serving).
    await expect(page.getByTestId('infer-epill')).toContainText('serving')
    // collapsed hero strip: iGPU GTT memory map + throughput tile + cards.
    const strip = page.getByTestId('infer-strip')
    await expect(strip).toBeVisible()
    await expect(strip.locator('.mem .blk-h')).toContainText('memory · iGPU GTT')
    await expect(strip.locator('.tp-tile')).toBeVisible()
    // the serving primary slot renders as a compact CARD whose status pill
    // carries the live tok/s (45 in the seed).
    const card = strip.getByTestId('infer-slot-primary')
    await expect(card).toBeVisible()
    await expect(card).toHaveClass(/\bscard\b/)
    await expect(card.locator('.spill')).toContainText('45 tok/s')
  })

  test('profile pill surfaces the runtime profile name (slot.profile)', async ({ page }) => {
    await page.goto('/#slots')
    // primary carries profile "rocm" in the seed; the [ device |
    // PROFILE ] provider tag renders it on the compact card too.
    const pill = page
      .getByTestId('infer-strip')
      .getByTestId('infer-profile-primary')
    await expect(pill).toBeVisible()
    await expect(pill).toContainText('rocm')
  })

  test('NPU/FLM slots are cordoned off to the NPU pane', async ({ page }) => {
    await page.goto('/#slots')
    await expect(pane(page)).toBeVisible()
    // the seed's NPU slots (agent / stt-npu / embed-npu) must NOT appear in
    // the inference pane — collapsed or expanded.
    for (const name of ['agent', 'stt-npu', 'embed-npu']) {
      await expect(pane(page).locator(`[data-testid="infer-slot-${name}"]`)).toHaveCount(0)
    }
    // …they live in the NPU · FLM stack pane below.
    await expect(page.locator('.npu-pane').first()).toBeVisible()
  })

  test('qcaret toggles the engine open → full cards with meta row', async ({ page }) => {
    await page.goto('/#slots')
    await expect(engine(page)).not.toHaveClass(/\bopen\b/)
    await page.getByTestId('infer-qcaret').click()
    await expect(engine(page)).toHaveClass(/\bopen\b/)
    // full-card grid + the hero band's throughput tile live in the body.
    await expect(body(page).locator('.scards.full')).toHaveCount(1)
    await expect(body(page).locator('.tp-tile')).toHaveCount(1)
    // the full card's meta row reports real metrics for the serving primary
    // slot (toks 45 · ttft 220 in the seed; no fabricated numbers).
    const meta = body(page).getByTestId('infer-slot-primary').locator('.scard-meta')
    await expect(meta.locator('.m .v').nth(0)).toContainText('45')
    await expect(meta.locator('.m .v').nth(1)).toContainText('220ms')
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

  test('full card exposes a real model-picker <select> (useModels)', async ({ page }) => {
    await page.goto('/#slots')
    // The full-card model picker lives in the (collapsible) engine body; it is
    // present in the DOM regardless of open state, so assert on it directly —
    // no flaky expand-click needed for a structural check.
    const picker = body(page).getByTestId('infer-slot-primary').locator('select.model-picker')
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
