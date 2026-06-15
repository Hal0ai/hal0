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

  test('serving slot Stop control issues POST /api/slots/npu/unload', async ({ page }) => {
    const unloads: string[] = []
    await page.route('**/api/slots/npu/unload', async (route) => {
      unloads.push(route.request().url())
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
    })

    await seedNpu(page, NPU_SERVING_SLOT)
    await page.goto('/#slots')

    const cslot = page.locator('.npu-card .cslot').filter({ hasText: 'npu' }).first()
    const stop = cslot.locator('.sctrl.stop')
    await expect(stop).toHaveCount(1)
    await stop.click()

    await expect.poll(() => unloads.length, { timeout: 5_000 }).toBeGreaterThan(0)
    expect(unloads[0]).toContain('/api/slots/npu/unload')
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
