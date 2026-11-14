/**
 * npu-tab-two-column-v3 — the NPU tab lays out in two equal columns on wide screens (#3672).
 *
 * At 1500px: FLM | GGML lanes, FLM | GGUF models, readiness | engines, profiles full width.
 * At 900px (below the 980px breakpoint) everything stacks into one column.
 */
import { test, expect, MOCK_DATA, json, type Page } from '../fixtures/apiMock'
import { NPU_OCCUPANCY_MOCK, NPU_XDNA_SLOT } from '../fixtures/mock-data'

async function setup(page: Page) {
  const anchor = MOCK_DATA.slots.find((s: any) => s.name === 'npu')
  const slots = [anchor, NPU_XDNA_SLOT]
  await page.addInitScript(
    ({ slots, occ }) => {
      let real: any
      Object.defineProperty(window, 'HAL0_DATA', {
        configurable: true,
        get() {
          return real
        },
        set(v) {
          real = v
          if (v && typeof v === 'object') {
            v.slots = slots
            v.npu_occupancy = occ
          }
        },
      })
    },
    { slots, occ: NPU_OCCUPANCY_MOCK },
  )
  await page.route('**/api/runners/xdna/fit', (route) =>
    json(route, { models: { 'qwen3-4b-q4': { xdna_path: 'fused', reason: 'all ops fit' } } }),
  )
  await page.route('**/api/runners/xdna/knobs', (route) =>
    json(route, { pmodes: ['turbo', 'performance'], env: [], host_xrt: { doc: '' } }),
  )
  await page.route('**/api/slots/flm/models', (route) =>
    json(route, { probe_ok: true, models: [{ model: 'qwen3-0.6b-npu', installed: true, capabilities: ['chat'] }] }),
  )
}

async function box(page: Page, id: string) {
  const b = await page.getByTestId(id).boundingBox()
  if (!b) throw new Error(`no box for ${id}`)
  return b
}

async function expectSideBySide(page: Page, left: string, right: string) {
  const l = await box(page, left)
  const r = await box(page, right)
  expect(r.x).toBeGreaterThanOrEqual(l.x + l.width)
  expect(Math.abs(r.y - l.y)).toBeLessThan(1)
}

async function expectStacked(page: Page, top: string, bottom: string) {
  const t = await box(page, top)
  const b = await box(page, bottom)
  expect(b.y).toBeGreaterThanOrEqual(t.y + t.height)
}

test.describe('NPU tab two-column layout', () => {
  test('wide: engines sit side by side, profiles span both columns', async ({ page }) => {
    await page.setViewportSize({ width: 1500, height: 1100 })
    await setup(page)

    await page.goto('/#slots/npu/lanes')
    await expect(page.getByTestId('ggml-lane')).toBeVisible()
    await expectSideBySide(page, 'flm-lane', 'ggml-lane')

    await page.goto('/#slots/npu/models')
    await expect(page.getByTestId('gguf-models')).toBeVisible()
    await expectSideBySide(page, 'flm-models', 'gguf-models')

    await page.goto('/#slots/npu/engines')
    await expect(page.getByTestId('npu-profiles')).toBeVisible()
    await expectSideBySide(page, 'npu-readiness', 'npu-engine-flm')
    const flm = await box(page, 'npu-engine-flm')
    const xdna = await box(page, 'npu-engine-xdna')
    expect(Math.abs(xdna.x - flm.x)).toBeLessThan(1)
    expect(xdna.y).toBeGreaterThanOrEqual(flm.y + flm.height)
    // Stacked at the design gap, not spread to the readiness column's height.
    expect(xdna.y - (flm.y + flm.height)).toBeLessThan(15)
    const engines = await box(page, 'npu-engines')
    const prof = await box(page, 'npu-profiles')
    expect(prof.width).toBeGreaterThanOrEqual(engines.width - 2)
    expect(prof.y).toBeGreaterThanOrEqual(xdna.y + xdna.height)
  })

  test('narrow: everything stacks below 980px', async ({ page }) => {
    await page.setViewportSize({ width: 900, height: 1100 })
    await setup(page)

    await page.goto('/#slots/npu/lanes')
    await expect(page.getByTestId('ggml-lane')).toBeVisible()
    await expectStacked(page, 'flm-lane', 'ggml-lane')

    await page.goto('/#slots/npu/models')
    await expect(page.getByTestId('gguf-models')).toBeVisible()
    await expectStacked(page, 'flm-models', 'gguf-models')

    await page.goto('/#slots/npu/engines')
    await expect(page.getByTestId('npu-profiles')).toBeVisible()
    await expectStacked(page, 'npu-readiness', 'npu-engine-flm')
    await expectStacked(page, 'npu-engine-flm', 'npu-engine-xdna')
    await expectStacked(page, 'npu-engine-xdna', 'npu-profiles')
  })
})
