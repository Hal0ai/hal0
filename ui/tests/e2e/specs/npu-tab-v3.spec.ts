/**
 * npu-tab-v3 — Slots → NPU (#slots/npu).
 *
 * The FLM anchor in the mock is named `npu`, a reserved Slots tab name: its drawer is
 * `#slots/edit/npu`, and `#slots/npu` is the NPU tab.
 *
 * 1. The Inference tab shows a one-line NPU summary that links to #slots/npu.
 * 2. In the NPU tab, add a GGML slot from the lane (POST /api/slots, then /load).
 * 3. Stage two FLM toggles, apply once: exactly one PUT /api/slots/npu/config and one restart.
 */
import { test, expect, MOCK_DATA, json, type Page } from '../fixtures/apiMock'
import { NPU_OCCUPANCY_MOCK, NPU_XDNA_SLOT } from '../fixtures/mock-data'

const GGUF_MODEL = {
  id: 'qwen3-4b-q4',
  name: 'Qwen3 4B Q4',
  capabilities: ['chat'],
  backends: ['vulkan', 'rocm', 'cpu'],
  installed: true,
  ns: 'pulled',
  size_bytes: 2_500_000_000,
}
const FLM_ANCHOR_MODEL = {
  id: 'qwen3-0.6b-npu',
  name: 'Qwen3 0.6B',
  capabilities: ['chat'],
  backends: ['npu'],
  provider_effective: 'flm',
  installed: true,
  ns: 'pulled',
}

async function setup(page: Page) {
  const anchor = MOCK_DATA.slots.find((s: any) => s.name === 'npu')
  const slots = [anchor, NPU_XDNA_SLOT]
  const models = [...MOCK_DATA.models, GGUF_MODEL, FLM_ANCHOR_MODEL]
  await page.addInitScript(
    ({ slots, models, occ }) => {
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
            v.models = models
            v.npu_occupancy = occ
          }
        },
      })
    },
    { slots, models, occ: NPU_OCCUPANCY_MOCK },
  )
  await page.route('**/api/runners/xdna/fit', (route) =>
    json(route, { models: { 'qwen3-4b-q4': { xdna_path: 'fused', reason: 'all ops fit' } } }),
  )
  await page.route('**/api/runners/xdna/knobs', (route) =>
    json(route, { pmodes: ['turbo', 'performance'], env: [], host_xrt: { doc: '' } }),
  )
  await page.route('**/api/profiles', (route) =>
    json(route, [
      ...(MOCK_DATA.profiles ?? []),
      { name: 'xdna', runner: 'xdna', runtime_family: 'llama-server', ctx_size: 8192, vision: 'off', seed: true, used_by: ['npu-gguf'] },
    ]),
  )
  await page.route('**/api/slots/flm/models', (route) =>
    json(route, {
      probe_ok: true,
      models: [{ model: 'qwen3-0.6b-npu', installed: true, capabilities: ['chat'] }],
    }),
  )
}

test.describe('NPU tab', () => {
  test('the Inference tab links to the NPU tab', async ({ page }) => {
    await setup(page)
    await page.goto('/#slots')
    const link = page.getByTestId('npu-summary-link')
    await expect(link).toBeVisible()
    await expect(link).toHaveAttribute('href', '#slots/npu')
    await link.click()
    await expect(page.getByTestId('npu-lanes')).toBeVisible()
  })

  test('clicking an NPU slot in the rail opens the NPU lanes and flashes its lane', async ({ page }) => {
    await setup(page)
    await page.goto('/#slots')
    await page.locator('[data-testid="slot-rail-row"][data-slot="npu-gguf"]').click()
    await expect(page).toHaveURL(/#slots\/npu\/lanes$/)
    await expect(page.getByTestId('ggml-card-npu-gguf')).toBeVisible()
    await expect(page.getByTestId('ggml-card-npu-gguf')).toHaveClass(/flash/)
  })

  test('adds a GGML slot from the lane', async ({ page }) => {
    await setup(page)
    const calls: string[] = []
    let created: any = null
    await page.route('**/api/slots', (route) => {
      if (route.request().method() !== 'POST') return route.fallback()
      created = route.request().postDataJSON()
      calls.push('POST /api/slots')
      return json(route, { name: created.name, state: 'offline' }, 201)
    })
    await page.route('**/api/slots/*/load', (route) => {
      calls.push(`POST ${new URL(route.request().url()).pathname}`)
      return json(route, { ok: true })
    })
    await page.goto('/#slots/npu')
    await page.getByTestId('ggml-add').click()
    await expect(page.getByTestId('ggml-slot-form')).toBeVisible()
    await page.getByTestId('ggml-model-list').getByRole('option', { name: /qwen3-4b-q4/ }).click()
    await page.getByTestId('ggml-create-start').click()
    await expect.poll(() => created?.name).toBeTruthy()
    expect(created).toMatchObject({ binary: 'xdna', device: 'npu', runtime: 'container', model: 'qwen3-4b-q4' })
    await expect.poll(() => calls.length).toBe(2)
    expect(calls[0]).toBe('POST /api/slots')
    expect(calls[1]).toBe(`POST /api/slots/${created.name}/load`)
  })

  test('two staged FLM toggles apply with one PUT and one restart', async ({ page }) => {
    await setup(page)
    const puts: any[] = []
    let restarts = 0
    await page.route('**/api/slots/npu/config', (route) => {
      if (route.request().method() === 'PUT') {
        puts.push(route.request().postDataJSON())
        return json(route, { name: 'npu' })
      }
      return json(route, { name: 'npu', npu: { chat: true, asr: true, embed: false }, model: { default: 'qwen3-0.6b-npu' } })
    })
    await page.route('**/api/slots/npu/restart', (route) => {
      restarts += 1
      return json(route, { name: 'npu' })
    })
    await page.goto('/#slots/npu')
    await page.getByTestId('flm-row-asr').getByTestId('flm-cap-asr').click()
    await page.getByTestId('flm-row-embed').getByTestId('flm-cap-embed').click()
    await expect(page.getByTestId('flm-applybar')).toContainText('2 changes staged')
    expect(puts).toHaveLength(0)
    await page.getByTestId('flm-apply').click()
    await expect.poll(() => puts.length).toBe(1)
    await expect.poll(() => restarts).toBe(1)
    expect(puts[0].npu).toEqual({ chat: true, asr: false, embed: true })
    await expect(page.getByTestId('flm-applybar')).toHaveCount(0)
    expect(puts).toHaveLength(1)
    expect(restarts).toBe(1)
  })

  test('the FLM lane Edit opens the drawer and closing it returns to the NPU tab; it is blocked while changes are staged', async ({ page }) => {
    await setup(page)
    await page.route('**/api/slots/npu/config', (route) =>
      json(route, { name: 'npu', npu: { chat: true, asr: false, embed: false }, model: { default: 'qwen3-0.6b-npu' } }),
    )
    await page.goto('/#slots/npu')
    await page.getByTestId('flm-cap-asr').click()
    const edit = page.getByTestId('flm-lane').getByTestId('slot-ctrl-edit')
    await expect(edit).toBeDisabled()
    await expect(edit).toHaveAttribute('title', 'Apply or discard the staged FLM changes first')
    await page.getByTestId('flm-discard').click()
    await edit.click()
    await expect(page).toHaveURL(/#slots\/edit\/npu$/)
    await expect(page.locator('.drawer.open')).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(page).toHaveURL(/#slots\/npu(\/lanes)?$/)
    await expect(page.getByTestId('npu-lanes')).toBeVisible()
  })

  test('the Slots drawer for the FLM anchor links to the NPU lanes instead of carrying the matrix', async ({ page }) => {
    await setup(page)
    await page.goto('/#slots/edit/npu')
    await expect(page.getByTestId('npu-lane-link')).toHaveAttribute('href', '#slots/npu/lanes')
    await expect(page.getByTestId('npu-capability-matrix')).toHaveCount(0)
  })
})
