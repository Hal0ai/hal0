/**
 * flm-model-scoping-npu-cards-v3
 *
 * 1. FLM (NPU) models are offered ONLY where an NPU/FLM slot picks a model:
 *    the Inference card model picker of a GPU/CPU llama.cpp slot never lists
 *    them (shared predicate: src/lib/slotModelFit.ts).
 * 2. The NPU tab (#slots/npu) FLM lane's chat select is limited to FLM chat models; STT and
 *    Embed show their bundled model. A staged chat pick applies through the drawer's contract:
 *    PUT the anchor's config ({npu:{chat,asr,embed}, model:{default}}) then POST /restart.
 */
import { test, expect, MOCK_DATA, json, type Page } from '../fixtures/apiMock'
import { openRichSelect } from '../fixtures/richSelect'

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
    return json(route, { name: 'flm', npu: { chat: true, asr: true, embed: true }, model: { default: 'qwen3:4b' } })
  })
  await page.route('**/api/slots/flm/restart', (route) => json(route, { name: 'flm' }))
  await page.route('**/api/slots/flm/models', (route) =>
    json(route, {
      probe_ok: true,
      models: [
        { model: 'qwen3:4b', installed: true, capabilities: ['chat'] },
        { model: 'gemma3:1b', installed: true, capabilities: ['chat'] },
        { model: 'whisper-v3:turbo', installed: true, capabilities: ['stt'] },
        { model: 'embed-gemma:300m', installed: true, capabilities: ['embed'] },
      ],
    }),
  )
}

test.describe('FLM model scoping + NPU tab chat model', () => {
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

  test('the NPU tab chat select lists only FLM chat models; STT/Embed show their bundled model', async ({ page }) => {
    await setup(page)
    await page.goto('/#slots/npu')
    const chat = page.getByTestId('flm-chat-model')
    await expect(chat).toBeVisible()
    await expect(chat.locator('option')).toHaveText(['qwen3:4b', 'gemma3:1b'])
    // STT / Embed load one bundled model with FLM's --asr / --embed: no per-role pick.
    await expect(page.getByTestId('flm-row-asr')).toContainText('whisper-v3:turbo')
    await expect(page.getByTestId('flm-row-embed')).toContainText('embed-gemma:300m')
  })

  test('picking a chat model stages it; Apply writes the anchor config then restarts it', async ({ page }) => {
    await setup(page)
    const calls: string[] = []
    let putBody: any = null
    await page.route('**/api/slots/flm/config', async (route) => {
      if (route.request().method() === 'PUT') {
        putBody = route.request().postDataJSON()
        calls.push('PUT config')
        return json(route, { name: 'flm' })
      }
      return json(route, { name: 'flm', npu: { chat: true, asr: true, embed: true }, model: { default: 'qwen3:4b' } })
    })
    await page.route('**/api/slots/flm/restart', (route) => {
      calls.push('POST restart')
      return json(route, { name: 'flm' })
    })
    await page.goto('/#slots/npu')
    await page.getByTestId('flm-chat-model').selectOption('gemma3:1b')
    expect(calls).toEqual([])
    await page.getByTestId('flm-apply').click()
    await expect.poll(() => calls).toEqual(['PUT config', 'POST restart'])
    expect(putBody).toEqual({
      npu: { chat: true, asr: true, embed: true },
      model: { default: 'gemma3:1b' },
    })
  })
})
