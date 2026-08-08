/**
 * npu-occupancy-v3 — NPU occupancy card UI coverage.
 *
 * Replaces npu-container-v3 (the old NpuFlmStack accordion + trio picker,
 * removed). Verifies the single full-width "NPU occupancy" card:
 *   1. Renders the gauge + 4×8 AIE-ML occupancy grid + a per-FLM-slot card.
 *      A serving FLM lights the whole 8-column array (single-tenant).
 *   2. The slot card's lifecycle control issues the right slot mutation.
 *   3. Degraded mode (columns_available:false) greys the grid + labels the
 *      gauge "column probe unavailable" — every other signal stays.
 *
 * READ path: VITE_MOCK_HAL0=1 short-circuits GET /api/slots and
 * GET /api/npu/occupancy in mockFetch (the latter synthesised by
 * buildNpuOccupancy from the npu-device slots, or read from
 * HAL0_DATA.npu_occupancy when present). Slots/occupancy injected via
 * page.addInitScript → window.HAL0_DATA, the seam used across the v3 suite.
 *
 * WRITE path: mutations use api(..., {raw:true}) → page.route intercepts.
 */
import { test, expect } from '../fixtures/apiMock'

// A serving container-runtime FLM/NPU slot. container_status running + healthy
// → slotCtrlPhase 'running' → the card shows the Stop control + a pulsing dot.
const NPU_SERVING_SLOT = {
  name: 'npu',
  type: 'llm',
  device: 'npu',
  device_class: 'npu',
  backend: null,
  model: 'gemma3-4b-FLM',
  model_id: 'gemma3-4b-FLM',
  group: 'npu',
  state: 'serving',
  port: 8098,
  runtime: 'container',
  profile: 'flm',
  image: 'ghcr.io/hal0ai/hal0-toolbox-flm:0.9.43',
  image_status: 'present',
  container_status: 'running',
  container_health: true,
  mem_mb: 2_400,
  metrics: { toks: 52, ttft: 95 },
}

const seedNpu = (page: any, slot: any, occupancy: any = null) =>
  page.addInitScript(
    ([s, occ]: [any, any]) => {
      document.addEventListener('DOMContentLoaded', () => {
        const D = (window as any).HAL0_DATA
        if (!D) return
        const withoutNpu = (D.slots || []).filter((x: any) => x.device !== 'npu')
        D.slots = [...withoutNpu, s]
        if (occ) D.npu_occupancy = occ
      })
    },
    [slot, occupancy],
  )

test.describe('NPU occupancy card', () => {
  test('renders gauge + 4×8 AIE grid + serving FLM slot card', async ({ page }) => {
    await seedNpu(page, NPU_SERVING_SLOT)
    await page.goto('/#slots')

    const card = page.locator('.npu-card')
    await expect(card).toBeVisible()

    // gauge present
    await expect(card.locator('.gauge')).toBeVisible()

    // 4×8 AIE-ML grid — 8 columns, 4 tiles each
    await expect(card.locator('.aie-grid .aie-col')).toHaveCount(8)
    await expect(card.locator('.aie-grid .aie-col').first().locator('.aie-tile')).toHaveCount(4)

    // single-tenant: a serving FLM lights the whole array → active tiles present
    await expect(card.locator('.aie-tile.active').first()).toBeVisible()

    // partition bracket labels the owning slot — single-tenant: one span-8
    // bracket for the whole array, not one per column
    await expect(card.locator('.aie-part')).toHaveCount(1)
    await expect(card.locator('.aie-part .pl')).toContainText('npu')
    await expect(card.locator('.aie-part .pc')).toHaveText('· 8c')

    // per-slot card: name + model (-FLM stripped) + the serving dot
    const cslot = card.locator('.cslot').filter({ hasText: 'npu' }).first()
    await expect(cslot.locator('.nm')).toHaveText('npu')
    await expect(cslot.locator('.md')).toHaveText('gemma3-4b')
    await expect(cslot.locator('.ldot.serving')).toHaveCount(1)
    // inline tok/s from slot.metrics
    await expect(cslot.locator('.cslot-mx .tps')).toContainText('52')
  })

  test('header Stop control issues POST /api/slots/flm/unload', async ({ page }) => {
    const unloads: string[] = []
    await page.route('**/api/slots/flm/unload', async (route) => {
      unloads.push(route.request().url())
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
    })

    // Lifecycle moved off the per-slot cards to the card-header ▶/■/↻
    // buttons, which target the 'flm' anchor slot (npu→flm rename; the
    // .sctrl controls were replaced by modality pill toggles in #1172).
    await seedNpu(page, { ...NPU_SERVING_SLOT, name: 'flm' })
    await page.goto('/#slots')

    const stop = page.locator('.npu-card .wcard-h button[title="Stop"]')
    await expect(stop).toBeEnabled()
    await stop.click()

    await expect.poll(() => unloads.length, { timeout: 5_000 }).toBeGreaterThan(0)
    expect(unloads[0]).toContain('/api/slots/flm/unload')
  })

  test('degraded probe greys the grid and labels the gauge', async ({ page }) => {
    const DEGRADED_OCC = {
      present: true,
      rows: 4,
      cols: 8,
      tiles: 32,
      tops_peak: 50,
      cols_total: 8,
      cols_used: 8,
      serving: true,
      single_tenant: true,
      columns_available: false,
      slots: [
        { name: 'npu', model: 'gemma3-4b', state: 'serving', cols: [0, 1, 2, 3, 4, 5, 6, 7], gb: 2.4 },
      ],
    }
    await seedNpu(page, NPU_SERVING_SLOT, DEGRADED_OCC)
    await page.goto('/#slots')

    const card = page.locator('.npu-card')
    await expect(card).toBeVisible()
    // grid greys
    await expect(card.locator('.aie.degraded')).toBeVisible()
    // gauge sub-label flips to the probe-unavailable note
    await expect(card.locator('.gauge .sub')).toContainText('column probe unavailable')
  })
})

// ─── #1661: STT/Embed pills must reflect npu_modality_active, not the raw
// [npu] table, on a model-less anchor ──────────────────────────────────────
//
// #1637 guarded the CHAT pill's write against a model-less anchor but left
// the STT/Embed pills reading `npu.asr`/`npu.embed` off the raw config —
// which happily renders ON right after a write lands (or on any pre-seeded
// TOML), even though `hal0.slots.activation.npu_modality_active` — the one
// gate the backend actually dispatches on — is False whenever the anchor
// has no `[model].default`. The trio shadows carry the server-resolved
// answer as `npu_modality_active`; this suite pins the card to read that,
// not the raw table, and confirms a click on a model-less anchor never PUTs.
async function seedTrio(page: any, slots: any[]) {
  await page.addInitScript((slots: any[]) => {
    let real: any
    Object.defineProperty(window, 'HAL0_DATA', {
      configurable: true,
      get() {
        return real
      },
      set(v) {
        real = v
        if (v && typeof v === 'object') v.slots = slots
      },
    })
  }, slots)
}

const MODELLESS_ANCHOR = {
  name: 'flm',
  type: 'llm',
  device: 'npu',
  device_class: 'npu',
  group: 'chat',
  state: 'offline',
  port: 8098,
  // No model bound anywhere — the fresh-install, out-of-the-box state.
  model: null,
  model_id: null,
  modelDefault: null,
  // The write already landed on disk (or is still in flight) — this is
  // exactly the stale-looking-ON config the raw-table read used to trust.
  npu: { chat: true, asr: true, embed: false },
  metrics: {},
}

const STT_SHADOW = {
  name: 'flm-stt',
  type: 'transcription',
  device: 'npu',
  device_class: 'npu',
  group: 'chat',
  state: 'offline',
  port: 8098,
  model: 'flm-stt-placeholder',
  model_id: 'flm-stt-placeholder',
  // Server-resolved: the anchor has no model, so nothing routes — even
  // though the anchor's raw npu.asr above reads true.
  npu_modality_active: false,
  metrics: {},
}

const EMBED_SHADOW = {
  ...STT_SHADOW,
  name: 'flm-embed',
  type: 'embedding',
  npu_modality_active: false,
}

test.describe('NPU pills on a model-less anchor (#1661)', () => {
  test('STT/Embed pills render OFF even though the raw [npu] table says on', async ({ page }) => {
    await seedTrio(page, [MODELLESS_ANCHOR, STT_SHADOW, EMBED_SHADOW])
    await page.goto('/#slots')

    const card = page.locator('.npu-card')
    await expect(card).toBeVisible()

    const sttSwitch = card.locator('.cslot', { hasText: 'flm-stt' }).getByRole('switch', { name: 'STT' })
    const embedSwitch = card.locator('.cslot', { hasText: 'flm-embed' }).getByRole('switch', { name: 'Embed' })
    await expect(sttSwitch).toHaveAttribute('aria-checked', 'false')
    await expect(embedSwitch).toHaveAttribute('aria-checked', 'false')
  })

  test('clicking the STT pill on a model-less anchor never PUTs — it routes to the drawer instead', async ({ page }) => {
    const puts: any[] = []
    await page.route('**/api/slots/flm/config', async (route) => {
      if (route.request().method() === 'PUT') puts.push(route.request().postDataJSON())
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
    })
    await seedTrio(page, [MODELLESS_ANCHOR, STT_SHADOW, EMBED_SHADOW])
    await page.goto('/#slots')

    const card = page.locator('.npu-card')
    await card.locator('.cslot', { hasText: 'flm-stt' }).getByRole('switch', { name: 'STT' }).click()

    // No write ever fires — the guard redirects to the drawer to pick a model.
    await expect(page).toHaveURL(/#slots\/flm$/)
    expect(puts.length).toBe(0)
  })
})
