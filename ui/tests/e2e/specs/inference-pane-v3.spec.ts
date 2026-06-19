/**
 * inference-pane-v3 — the Inference "engine" pane (slots-page Inference tab),
 * P2 card direction (design_handoff_inference_slots).
 *
 * Behaviour under test:
 *   - the page-level hero band (InferenceHeroBand, above the tabs) renders the
 *     iGPU GTT memory map + combined-throughput tile from HAL0_DATA
 *   - the engine pane renders the epill + ALL slots as full cards, always
 *     visible (the old collapse/expand accordion + qcaret were removed)
 *   - the serving card's status pill shows live tok/s (no fabricated numbers)
 *   - the profile pill surfaces the slot's runtime profile name (slot.profile)
 *   - NPU/FLM slots are cordoned off — absent from the inference pane, present
 *     in the NPU · FLM stack pane below
 *   - a lifecycle control fires the real mutation (Stop → POST /unload)
 *   - the full card exposes a real model-picker <select> (useModels)
 *   - the NPU/FLM pane renders its own engine shell + trio
 *   - the Inference tab carries the yellow `infer` accent class
 *
 * The slot LIST comes from in-bundle HAL0_DATA (VITE_MOCK_HAL0=1); mutations
 * go through fetch, so per-route stubs capture the write path.
 *
 * NOTE: the page now carries TWO `.infer-pane` roots — the hero band
 * (`.infer-hero-top`, above the tabs) and the engine pane below it. The engine
 * locators scope to `.infer-pane:not(.infer-hero-top)` to target the pane; the
 * hero band is reached via its `infer-hero-band` testid.
 */
import { test, expect, type Page } from '../fixtures/apiMock'

const pane = (page: Page) => page.locator('.infer-pane:not(.infer-hero-top)').first()
const engine = (page: Page) => pane(page).locator('.engine').first()
const body = (page: Page) => pane(page).locator('.engine-b').first()
const hero = (page: Page) => page.getByTestId('infer-hero-band')

test.describe('Inference engine pane (/slots · Inference tab)', () => {
  test('hero band renders memory + throughput; engine renders epill + full slot cards', async ({ page }) => {
    await page.goto('/#slots')
    // page-level hero band (above the tabs): iGPU GTT memory map + throughput.
    await expect(hero(page)).toBeVisible()
    await expect(hero(page).locator('.mem .blk-h')).toContainText('memory · iGPU GTT')
    await expect(hero(page).locator('.tp-tile')).toBeVisible()
    // engine pane: state pill summarises serving/loaded counts (primary serving).
    await expect(pane(page)).toBeVisible()
    await expect(page.getByTestId('infer-epill')).toContainText('serving')
    // headline (chat · agent) slots render as FULL cards. The status pill is
    // gone — the header now shows the slot PORT (mono, pushed right); readiness
    // is the dot, and tok/s lives in the meta row.
    await expect(body(page).locator('.scards.full')).toHaveCount(1)
    const card = body(page).getByTestId('infer-slot-primary')
    await expect(card).toBeVisible()
    await expect(card).toHaveClass(/\bscard\b/)
    // serving primary → yellow serving dot + the port (:8092 in the seed).
    await expect(card.locator('.scard-h .sdot')).toHaveClass(/\bserving\b/)
    await expect(card.locator('.scard-h .sport')).toContainText(':8092')
  })

  test('profile pill surfaces the runtime profile name (slot.profile)', async ({ page }) => {
    await page.goto('/#slots')
    // primary carries profile "rocm" in the seed; the [ device | PROFILE ]
    // provider tag renders it on the full card.
    const pill = body(page).getByTestId('infer-profile-primary')
    await expect(pill).toBeVisible()
    await expect(pill).toContainText('rocm')
  })

  test('NPU/FLM slots are cordoned off to the NPU pane', async ({ page }) => {
    await page.goto('/#slots')
    await expect(pane(page)).toBeVisible()
    // the seed's NPU slots (agent / stt-npu / embed-npu) must NOT appear in
    // the inference pane.
    for (const name of ['agent', 'stt-npu', 'embed-npu']) {
      await expect(pane(page).locator(`[data-testid="infer-slot-${name}"]`)).toHaveCount(0)
    }
    // …they live in the NPU occupancy card below.
    await expect(page.locator('.npu-card').first()).toBeVisible()
  })

  test('engine renders full slot cards with the metric meta row (no accordion)', async ({ page }) => {
    await page.goto('/#slots')
    // full-card grid is always present; the throughput tile now lives in the
    // page hero band, not the engine body.
    await expect(body(page).locator('.scards.full')).toHaveCount(1)
    await expect(hero(page).locator('.tp-tile')).toHaveCount(1)
    await expect(body(page).locator('.tp-tile')).toHaveCount(0)
    // the full card's meta row reports real metrics for the serving primary
    // slot (toks 45 · ttft 220 in the seed; no fabricated numbers).
    const meta = body(page).getByTestId('infer-slot-primary').locator('.scard-meta')
    await expect(meta.locator('.m .v').nth(0)).toContainText('45')
    await expect(meta.locator('.m .v').nth(1)).toContainText('220ms')
  })

  test('Stop control POSTs /unload for a running slot', async ({ page }) => {
    const unloads: string[] = []
    await page.route('**/api/slots/primary/unload', async (route) => {
      unloads.push(route.request().url())
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
    })
    await page.goto('/#slots')
    await body(page).getByTestId('infer-slot-primary').locator('.sctrl.stop').click()
    await expect.poll(() => unloads.length).toBeGreaterThan(0)
  })

  test('Stop stays enabled mid-load (transitional) → cancel fires /unload without waiting', async ({ page }) => {
    // Regression for the non-blocking control rework: a slot that is still
    // loading (container_status "starting" → transitional phase) must keep its
    // Stop control LIVE so the user can cancel a slow model-load instead of
    // waiting for it to finish. Previously Stop was `disabled` during
    // transitional.
    const LOADING_LLM_SLOT = {
      name: 'loading-llm',
      type: 'llm',
      device: 'gpu-rocm',
      device_class: 'gpu',
      backend: 'rocm',
      model: 'qwen3.6-35b-a3b-q4_k_m',
      model_id: 'qwen3.6-35b-a3b',
      group: 'chat',
      state: 'starting',
      port: 8099,
      runtime: 'container',
      profile: 'rocm',
      container_status: 'starting',
      container_health: false,
      mem_mb: 0,
      enabled: true,
      isDefault: false,
      metrics: { toks: 0, ttft: null, ctx: 0, kv: null },
    }
    const unloads: string[] = []
    await page.route('**/api/slots/loading-llm/unload', async (route) => {
      unloads.push(route.request().url())
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
    })
    await page.addInitScript((slot) => {
      document.addEventListener('DOMContentLoaded', () => {
        const w = window as any
        if (w.HAL0_DATA) {
          const existing = (w.HAL0_DATA.slots || []).filter((s: any) => s.name !== slot.name)
          w.HAL0_DATA.slots = [slot, ...existing]
        }
      })
    }, LOADING_LLM_SLOT)

    await page.goto('/#slots')

    const stop = body(page).getByTestId('infer-slot-loading-llm').locator('.sctrl.stop')
    await expect(stop).toBeVisible()
    await expect(stop).toBeEnabled() // ← the cancel-mid-load affordance
    await stop.click()
    await expect.poll(() => unloads.length).toBeGreaterThan(0)
  })

  test('Restart control POSTs /restart for a running slot', async ({ page }) => {
    const restarts: string[] = []
    await page.route('**/api/slots/primary/restart', async (route) => {
      restarts.push(route.request().url())
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
    })
    await page.goto('/#slots')
    await body(page).getByTestId('infer-slot-primary').locator('.sctrl.restart').click()
    await expect.poll(() => restarts.length).toBeGreaterThan(0)
  })

  test('Start control POSTs /load for an off slot', async ({ page }) => {
    const loads: string[] = []
    await page.route('**/api/slots/legacy/load', async (route) => {
      loads.push(route.request().url())
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
    })
    await page.goto('/#slots')
    // `legacy` is the disabled (off) seed slot — now always rendered as a card.
    await body(page).getByTestId('infer-slot-legacy').locator('.sctrl.start').click()
    await expect.poll(() => loads.length).toBeGreaterThan(0)
  })

  test('full-card model-picker change POSTs /swap with model_id', async ({ page }) => {
    const swaps: any[] = []
    await page.route('**/api/slots/primary/swap', async (route) => {
      try { swaps.push(JSON.parse(route.request().postData() || '{}')) } catch { swaps.push({}) }
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
    })
    await page.goto('/#slots')
    const picker = body(page).getByTestId('infer-slot-primary').locator('select.model-picker')
    const opts = await picker.locator('option').evaluateAll((els) =>
      els.map((e) => (e as HTMLOptionElement).value).filter(Boolean),
    )
    const cur = await picker.inputValue()
    const next = opts.find((v) => v !== cur)
    test.skip(!next, 'need ≥2 llm models in the catalog to swap')
    await picker.selectOption(next!)
    await expect.poll(() => swaps.length).toBeGreaterThan(0)
    expect(typeof swaps[0].model_id).toBe('string')
  })

  test('full card exposes a real model-picker <select> (useModels)', async ({ page }) => {
    await page.goto('/#slots')
    // The full-card model picker is always rendered in the engine body now.
    const picker = body(page).getByTestId('infer-slot-primary').locator('select.model-picker')
    await expect(picker).toHaveCount(1)
    // the picker is populated (at minimum the current model is an option).
    await expect.poll(async () => picker.locator('option').count()).toBeGreaterThan(0)
  })

  test('utility-type slots route to the support footer regardless of group', async ({ page }) => {
    await page.goto('/#slots')
    await expect(pane(page)).toBeVisible()
    const util = pane(page).getByTestId('infer-util')
    await expect(util).toBeVisible()
    // embed + rerank (util types) live in the footer…
    await expect(util.getByTestId('infer-slot-embed')).toHaveCount(1)
    await expect(util.getByTestId('infer-slot-rerank')).toHaveCount(1)
    // …and the tts slot does too, EVEN THOUGH its mock group is "chat"
    // (type-driven routing overrides the mislabeled group).
    await expect(util.getByTestId('infer-slot-tts')).toHaveCount(1)
    // it must NOT appear among the headline (full) cards.
    await expect(body(page).locator('.scards.full').getByTestId('infer-slot-tts')).toHaveCount(0)
  })
})

test.describe('NPU occupancy card (/slots · Inference tab)', () => {
  test('renders the occupancy card — gauge + 4×8 AIE-ML grid', async ({ page }) => {
    await page.goto('/#slots')
    const card = page.locator('.npu-card')
    await expect(card).toBeVisible()
    await expect(card.locator('.gauge')).toBeVisible()
    await expect(card.locator('.aie-grid .aie-col')).toHaveCount(8)
  })

  test('lists a per-FLM-slot card with lifecycle controls', async ({ page }) => {
    await page.goto('/#slots')
    const card = page.locator('.npu-card')
    await expect(card.locator('.cslot').first()).toBeVisible()
    await expect(card.locator('.cslot .slot-ctrls').first()).toBeVisible()
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
