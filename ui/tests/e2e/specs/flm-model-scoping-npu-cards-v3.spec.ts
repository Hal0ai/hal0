/**
 * flm-model-scoping-npu-cards-v3
 *
 * 1. FLM (NPU) models are offered ONLY where an NPU/FLM slot picks a model:
 *    the Inference card model picker of a GPU/CPU llama.cpp slot never lists
 *    them (shared predicate: src/lib/slotModelFit.ts).
 * 2. The NPU occupancy card's per-slot rows carry the same RichSelect model
 *    dropdown the inference cards use, limited to FLM models of the card's
 *    role. A chat pick applies through the drawer's contract: PUT the anchor's
 *    config ({npu:{chat,asr,embed}, model:{default}}) then POST /restart.
 *
 * Set FLM_CARD_SHOTS=<label> to also write desktop + 390px screenshots of the
 * NPU card to ui/test-results/flm-cards/<label>-{desktop,mobile}.png.
 */
import { test, expect, MOCK_DATA, json, type Page } from '../fixtures/apiMock'
import { openRichSelect, pickRichOption } from '../fixtures/richSelect'

const FLM_MODELS = [
  {
    id: 'qwen3:4b',
    name: 'Qwen3 4B',
    capabilities: ['chat'],
    backends: ['npu'],
    provider_effective: 'flm',
    runs_on: ['npu'],
    size_bytes: 3_200_000_000,
    installed: true,
    origin: 'local',
    ns: 'pulled',
  },
  {
    id: 'gemma3:1b',
    name: 'Gemma3 1B',
    capabilities: ['chat'],
    backends: ['npu'],
    provider_effective: 'flm',
    runs_on: ['npu'],
    size_bytes: 1_100_000_000,
    installed: true,
    origin: 'local',
    ns: 'pulled',
  },
  {
    id: 'whisper-v3:turbo',
    name: 'Whisper v3 turbo',
    capabilities: ['transcription'],
    backends: ['npu'],
    provider_effective: 'flm',
    runs_on: ['npu'],
    size_bytes: 800_000_000,
    installed: true,
    origin: 'local',
    ns: 'pulled',
  },
  {
    id: 'embed-gemma:300m',
    name: 'EmbeddingGemma 300M',
    capabilities: ['embed'],
    backends: ['npu'],
    provider_effective: 'flm',
    runs_on: ['npu'],
    size_bytes: 300_000_000,
    installed: true,
    origin: 'local',
    ns: 'pulled',
  },
]

const NPU_SLOTS = [
  {
    name: 'primary', type: 'llm', device: 'gpu-rocm', device_class: 'gpu', backend: 'rocm',
    model: 'qwen3.6-27b-q5kxl', model_id: 'qwen3.6-27b-q5kxl', group: 'chat', state: 'serving',
    port: 8092, isDefault: true, runtime: 'container', profile: 'rocm',
    container_status: 'running', container_health: true, mem_mb: 18_400,
    metrics: { toks: 45, ttft: 220, ctx: 8192, kv: null, mem: 18.8 },
  },
  {
    name: 'flm', type: 'llm', device: 'npu', device_class: 'npu', backend: null,
    model: 'qwen3:4b', model_id: 'qwen3:4b', modelDefault: 'qwen3:4b', group: 'npu', state: 'ready',
    port: 8093, runtime: 'container', profile: 'flm',
    container_status: 'running', container_health: true, mem_mb: 3_200,
    npu: { chat: true, asr: true, embed: true },
    metrics: { toks: 40, ttft: 280, ctx: 4096, kv: 66, mem: 3.1 },
  },
  {
    name: 'flm-stt', type: 'transcription', device: 'npu', device_class: 'npu', backend: null,
    model: 'whisper-v3:turbo', model_id: 'whisper-v3:turbo', group: 'npu', state: 'ready',
    port: 8093, runtime: 'container', profile: 'flm', npu_modality_active: true,
    container_status: 'running', container_health: true, metrics: { mem: 0.8 },
  },
  {
    name: 'flm-embed', type: 'embedding', device: 'npu', device_class: 'npu', backend: null,
    model: 'embed-gemma:300m', model_id: 'embed-gemma:300m', group: 'npu', state: 'ready',
    port: 8093, runtime: 'container', profile: 'flm', npu_modality_active: true,
    container_status: 'running', container_health: true, metrics: { mem: 0.3 },
  },
]

async function setup(page: Page) {
  // `/api/slots` and `/api/models` are answered from the in-bundle HAL0_DATA
  // mock layer (MOCK_ALLOWLIST), not through page.route, so both are seeded
  // before the bundle assigns HAL0_DATA.
  const models = [...MOCK_DATA.models, ...FLM_MODELS]
  await page.addInitScript(
    ({ slots, models }) => {
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
          }
        },
      })
    },
    { slots: NPU_SLOTS, models },
  )
  await page.route('**/api/slots/flm/config', (route) => {
    if (route.request().method() === 'PUT') return json(route, { name: 'flm' })
    return json(route, { name: 'flm', npu: { chat: true, asr: true, embed: true } })
  })
  await page.route('**/api/slots/flm/restart', (route) => json(route, { name: 'flm' }))
}

test.describe('FLM model scoping + NPU slot-card pickers', () => {
  test('a GPU slot card picker never lists FLM models', async ({ page }) => {
    await setup(page)
    await page.goto('/#slots')
    const picker = page.getByTestId('infer-model-primary')
    await expect(picker).toBeVisible()
    await openRichSelect(picker)
    const list = page.getByRole('listbox')
    await expect(list.getByRole('option').first()).toBeVisible()
    for (const flm of ['Qwen3 4B', 'Gemma3 1B', 'Whisper', 'EmbeddingGemma']) {
      await expect(list.getByText(flm)).toHaveCount(0)
    }
  })

  test('each NPU card has a RichSelect limited to FLM models of its role', async ({ page }) => {
    await setup(page)
    await page.goto('/#slots')
    const card = page.locator('.npu-card')
    await expect(card).toBeVisible()
    const chat = page.getByTestId('npu-model-flm')
    await expect(chat).toBeVisible()
    await openRichSelect(chat)
    let list = page.getByRole('listbox')
    await expect(list.getByRole('option', { name: /Qwen3 4B/ })).toBeVisible()
    await expect(list.getByRole('option', { name: /Gemma3 1B/ })).toBeVisible()
    // chat card: no ASR / embed / GGUF rows
    await expect(list.getByText('Whisper')).toHaveCount(0)
    await expect(list.getByText('EmbeddingGemma')).toHaveCount(0)
    await expect(list.getByText('Qwen3.6-27B')).toHaveCount(0)
    // runtime chip in the NPU tone
    await expect(list.locator('.chip.dev-npu').first()).toBeVisible()
    await page.keyboard.press('Escape')

    // STT / Embed cards show their own FLM model and are read-only: FLM's
    // --asr / --embed load one bundled model, there is no per-role write.
    const stt = page.getByTestId('npu-model-flm-stt')
    await expect(stt).toContainText('Whisper v3 turbo')
    await expect(stt).toBeDisabled()
    const embed = page.getByTestId('npu-model-flm-embed')
    await expect(embed).toContainText('EmbeddingGemma 300M')
    await expect(embed).toBeDisabled()
  })

  test('the dropdown popover is not clipped by the NPU card', async ({ page }) => {
    await setup(page)
    await page.goto('/#slots')
    await openRichSelect(page.getByTestId('npu-model-flm'))
    const list = page.getByRole('listbox')
    await expect(list).toBeVisible()
    const box = await list.boundingBox()
    expect(box).not.toBeNull()
    // the popover is hit-testable at its centre (an ancestor with overflow:hidden
    // would hide / clip it)
    const hit = await page.evaluate(
      ({ x, y }) => !!document.elementFromPoint(x, y)?.closest('[role="listbox"]'),
      { x: box!.x + box!.width / 2, y: box!.y + box!.height - 4 },
    )
    expect(hit).toBe(true)
  })

  test('picking a chat model writes the anchor config then restarts it', async ({ page }) => {
    await setup(page)
    const calls: string[] = []
    let putBody: any = null
    await page.route('**/api/slots/flm/config', async (route) => {
      if (route.request().method() === 'PUT') {
        putBody = route.request().postDataJSON()
        calls.push('PUT config')
        return json(route, { name: 'flm' })
      }
      return json(route, { name: 'flm', npu: { chat: true, asr: true, embed: true } })
    })
    await page.route('**/api/slots/flm/restart', (route) => {
      calls.push('POST restart')
      return json(route, { name: 'flm' })
    })
    await page.goto('/#slots')
    await pickRichOption(page.getByTestId('npu-model-flm'), 'gemma3:1b')
    await expect.poll(() => calls).toEqual(['PUT config', 'POST restart'])
    expect(putBody).toEqual({
      npu: { chat: true, asr: true, embed: true },
      model: { default: 'gemma3:1b' },
    })
  })

  test('screenshots of the NPU card', async ({ page }) => {
    const label = process.env.FLM_CARD_SHOTS
    test.skip(!label, 'set FLM_CARD_SHOTS=<label> to capture screenshots')
    await setup(page)
    for (const [name, width, height] of [
      ['desktop', 1440, 900],
      ['mobile', 390, 844],
    ] as const) {
      await page.setViewportSize({ width, height })
      await page.goto('/#slots')
      const card = page.locator('.npu-card')
      await expect(card).toBeVisible()
      await card.scrollIntoViewIfNeeded()
      await card.screenshot({ path: `test-results/flm-cards/${label}-${name}.png` })
      const pick = page.getByTestId('npu-model-flm')
      if (await pick.count()) {
        await openRichSelect(pick)
        await page.screenshot({ path: `test-results/flm-cards/${label}-${name}-open.png` })
        await page.keyboard.press('Escape')
      }
    }
  })
})
