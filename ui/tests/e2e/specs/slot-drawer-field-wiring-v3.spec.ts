/**
 * slot-drawer-field-wiring-v3 — field → wire-key contract for the slot edit
 * drawer (`#slots/:name`).
 *
 * Companion to slot-edit-controls-v3 (HW grid / ctx). This file closes the
 * remaining gaps where a drawer control had NO assertion that its edit
 * actually reaches the wire with the right key on the right route:
 *
 *   W1. Model select (Model group) → POST /api/slots/{name}/swap { model_id }
 *       — its OWN route, never folded into the batched PUT /config.
 *   W2. Same select on a LIVE container slot → ConfirmDialog gate; cancel
 *       fires nothing, confirm fires the same POST body.
 *   W4. NPU · Embed toggle → PUT /config { npu: { chat, asr, embed } } + restart
 *       (the ASR lane is covered in slot-edit-controls-v3; embed was not).
 *   W5. NPU chat-model pick, installed tag → PUT /config { npu, model.default }.
 *   W6. NPU chat-model pick, NOT-installed tag → POST /api/models/{tag}/pull
 *       FIRST, no config write until the pull stream reports `completed`, then
 *       the same { model: { default } } apply.
 *
 * Mechanics mirror slot-edit-controls-v3: the slot LIST comes from in-bundle
 * HAL0_DATA (VITE_MOCK_HAL0=1 short-circuits GET /api/slots before page.route
 * sees it), so `seedSlots` patches `window.HAL0_DATA.slots` via addInitScript.
 * Mutations are NEVER mock-substituted (src/api/mock.ts is GET-only), so
 * page.route is authoritative for every POST/PUT asserted below.
 *
 * NOTE: this file deliberately does not touch the drawer HEADER toggle — that
 * surface is owned by slot-pin-toggle-v3.spec.ts.
 */
import { test, expect, type Page } from '../fixtures/apiMock'

const PRIMARY = {
  name: 'primary', type: 'llm', device: 'gpu-rocm', profile: 'rocm',
  model: 'qwen3.6-27b-mtp', model_id: 'qwen3.6-27b-mtp',
  modelLong: 'Qwen3.6-27B-MTP',
  group: 'chat', state: 'serving', port: 8092, isDefault: true,
  enabled: true, n_gpu_layers: -1,
  metrics: { ctx: 8192, toks: 42, ttft: 180, kv: 35 },
}
const EMBED = {
  name: 'embed', type: 'embedding', device: 'gpu-rocm',
  model: 'nomic-v1.5', model_id: 'nomic-v1.5', modelLong: 'nomic-embed-text-v1.5',
  group: 'embed', state: 'ready', port: 8095, isDefault: true,
  enabled: true, n_gpu_layers: -1,
  metrics: {},
}
// Same slot, cold. `slotButtonPhase` reads container_status/state, so this row
// resolves to the "off" phase and the drawer swaps without the live confirm.
const OFFLINE_PRIMARY = {
  ...PRIMARY,
  state: 'offline',
  runtime: 'container',
  container_status: 'stopped',
  container_health: false,
  metrics: {},
}
// A second llm row already lives in HAL0_DATA.models, so the drawer's
// compatible-models filter offers it as a swap target.
const SWAP_TARGET = 'qwen3-coder-30b'

/** Override the in-bundle HAL0_DATA.slots (see slot-edit-controls-v3). */
async function seedSlots(page: Page, slots: any[]) {
  await page.addInitScript((slots) => {
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

/** Capture every PUT /api/slots/{name}/config body; stub the sibling writes. */
async function captureConfig(page: Page, name: string) {
  const puts: any[] = []
  await page.route(`**/api/slots/${name}/config`, async (route) => {
    if (route.request().method() === 'PUT') puts.push(route.request().postDataJSON())
    await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
  })
  await page.route(`**/api/slots/${name}/defaults`, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: '{}' }),
  )
  return puts
}

/** Capture POST /api/slots/{name}/swap bodies. */
async function captureSwaps(page: Page, name: string) {
  const swaps: any[] = []
  await page.route(`**/api/slots/${name}/swap`, async (route) => {
    swaps.push(route.request().postDataJSON())
    await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
  })
  return swaps
}

test.describe('Slot drawer — model swap wiring', () => {
  test('W1 — picking a model POSTs /swap { model_id } and never folds it into PUT /config', async ({ page }) => {
    const swaps = await captureSwaps(page, 'primary')
    const puts = await captureConfig(page, 'primary')
    // OFFLINE slot: nothing is loaded, so the swap fires straight through
    // without the live-container confirm gate (that path is W2 below).
    await seedSlots(page, [OFFLINE_PRIMARY, EMBED])

    await page.goto('/#slots/primary')
    await expect(page.locator('.drawer')).toBeVisible()
    const select = page.locator('.drawer').getByLabel('Model for primary')
    await expect(select).toHaveValue('qwen3.6-27b-mtp')
    await select.selectOption(SWAP_TARGET)

    await expect.poll(() => swaps.length).toBe(1)
    expect(swaps[0]).toEqual({ model_id: SWAP_TARGET })
    // The swap is its own route — the batched Save must not also carry a model.
    expect(puts).toEqual([])
  })

  test('W1 — re-picking the current model is a no-op (no /swap)', async ({ page }) => {
    const swaps = await captureSwaps(page, 'primary')
    await seedSlots(page, [OFFLINE_PRIMARY, EMBED])

    await page.goto('/#slots/primary')
    const select = page.locator('.drawer').getByLabel('Model for primary')
    await select.selectOption('qwen3.6-27b-mtp')
    await page.waitForTimeout(200)
    expect(swaps).toEqual([])
  })

  test('W2 — a LIVE container slot confirms before swapping; cancel fires nothing', async ({ page }) => {
    const swaps = await captureSwaps(page, 'primary')
    await seedSlots(page, [
      { ...PRIMARY, runtime: 'container', container_status: 'running', container_health: true },
      EMBED,
    ])

    await page.goto('/#slots/primary')
    await page.locator('.drawer').getByLabel('Model for primary').selectOption(SWAP_TARGET)

    const dialog = page.locator('.modal-shell', { hasText: 'Swap model' })
    await expect(dialog).toBeVisible()
    expect(swaps).toEqual([])

    await dialog.getByRole('button', { name: 'Cancel' }).click()
    await expect(dialog).toHaveCount(0)
    await page.waitForTimeout(200)
    expect(swaps).toEqual([])
    // Cancelling reverts the select to the bound model (value={cur}).
    await expect(page.locator('.drawer').getByLabel('Model for primary')).toHaveValue('qwen3.6-27b-mtp')
  })

  test('W2 — confirming the live-swap dialog POSTs /swap { model_id }', async ({ page }) => {
    const swaps = await captureSwaps(page, 'primary')
    await seedSlots(page, [
      { ...PRIMARY, runtime: 'container', container_status: 'running', container_health: true },
      EMBED,
    ])

    await page.goto('/#slots/primary')
    await page.locator('.drawer').getByLabel('Model for primary').selectOption(SWAP_TARGET)
    await page.locator('.modal-shell').getByRole('button', { name: 'Swap model' }).click()

    await expect.poll(() => swaps.length).toBe(1)
    expect(swaps[0]).toEqual({ model_id: SWAP_TARGET })
  })
})

// W3 (Parallel) and W7 (chat-template override) were removed with their
// controls in #1379. Both wrote slot-tier keys that are INERT at launch
// (spec-flags-ownership §1/§4), and the template write additionally fired a
// cold restart to apply nothing. Their replacement is the wire-level guarantee
// in slot-drawer-sunset-removal-v3: no Save may put `parallel`, `chat_template`
// or `server.extra_args` on the wire, even when the slot TOML still carries
// them. The #1372 clear-path contract W7 protected now lives entirely in the
// backend (tests/api/test_slot_config_validation.py) and in the fold migrator
// `hal0 slot migrate-flags` (#1396/#1397).

test.describe('Slot drawer — NPU modality + chat-model wiring', () => {
  const NPU_SLOT = {
    ...PRIMARY,
    name: 'npu',
    device: 'npu',
    profile: 'flm',
    model: 'flm-chat',
    model_id: 'flm-chat',
    npu: { chat: true, asr: false, embed: false },
  }

  /** Stub the FLM catalogue the NPU lane selects read. */
  async function stubFlmModels(page: Page, models: any[]) {
    await page.route('**/api/slots/flm/models', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ models }),
      }),
    )
  }

  async function captureRestarts(page: Page, name: string) {
    const restarts: string[] = []
    await page.route(`**/api/slots/${name}/restart`, async (route) => {
      restarts.push(route.request().url())
      await route.fulfill({ status: 202, contentType: 'application/json', body: '{}' })
    })
    return restarts
  }

  test('W4 — the Embed toggle instant-applies PUT /config { npu.embed } + restart', async ({ page }) => {
    const puts = await captureConfig(page, 'npu')
    const restarts = await captureRestarts(page, 'npu')
    await stubFlmModels(page, [{ model: 'flm-chat', installed: true }])
    await seedSlots(page, [NPU_SLOT, EMBED])

    await page.goto('/#slots/npu')
    await page.locator('.drawer .form-row', { hasText: 'NPU · Embed' }).getByRole('switch').click()

    await expect.poll(() => puts.length).toBe(1)
    // The whole modality triple rides every write — the backend replaces [npu]
    // wholesale, so a partial body would silently clear the other lanes.
    expect(puts[0].npu).toEqual({ chat: true, asr: false, embed: true })
    await expect.poll(() => restarts.length).toBe(1)
  })

  test('W4 — the Chat toggle instant-applies PUT /config { npu.chat: false }', async ({ page }) => {
    const puts = await captureConfig(page, 'npu')
    await captureRestarts(page, 'npu')
    await stubFlmModels(page, [{ model: 'flm-chat', installed: true }])
    await seedSlots(page, [NPU_SLOT, EMBED])

    await page.goto('/#slots/npu')
    await page.locator('.drawer .form-row', { hasText: 'NPU · Chat' }).getByRole('switch').click()

    await expect.poll(() => puts.length).toBe(1)
    expect(puts[0].npu).toEqual({ chat: false, asr: false, embed: false })
    // Chat off ⇒ no positional model tag is sent.
    expect(puts[0]).not.toHaveProperty('model')
  })

  test('W5 — picking an INSTALLED chat model PUTs { npu, model: { default } }', async ({ page }) => {
    const puts = await captureConfig(page, 'npu')
    const restarts = await captureRestarts(page, 'npu')
    await stubFlmModels(page, [
      { model: 'flm-chat', installed: true },
      { model: 'flm-chat-xl', installed: true },
    ])
    await seedSlots(page, [NPU_SLOT, EMBED])

    await page.goto('/#slots/npu')
    const chatRow = page.locator('.drawer .form-row', { hasText: 'NPU · Chat' })
    await expect(chatRow.locator('select')).toHaveValue('flm-chat')
    await chatRow.locator('select').selectOption('flm-chat-xl')

    await expect.poll(() => puts.length).toBe(1)
    expect(puts[0].npu).toEqual({ chat: true, asr: false, embed: false })
    // The tag is nested under [model] so the backend's one-level merge keeps
    // sibling keys (context_size, n_gpu_layers) instead of clobbering them.
    expect(puts[0].model).toEqual({ default: 'flm-chat-xl' })
    await expect.poll(() => restarts.length).toBe(1)
  })

  test('W6 — picking a NOT-installed chat model pulls first, then applies', async ({ page }) => {
    const puts = await captureConfig(page, 'npu')
    const restarts = await captureRestarts(page, 'npu')
    await stubFlmModels(page, [
      { model: 'flm-chat', installed: true },
      { model: 'flm-chat-xl', installed: false },
    ])

    const pulls: string[] = []
    let releaseStream: () => void = () => {}
    const streamGate = new Promise<void>((resolve) => {
      releaseStream = resolve
    })
    await page.route('**/api/models/flm-chat-xl/pull', async (route) => {
      pulls.push(route.request().method())
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ id: 'job-1', state: 'queued' }),
      })
    })
    // Hold the SSE body open until the test has proven no config write happened
    // while the download was still in flight, then flush a `completed` frame.
    await page.route('**/api/models/flm-chat-xl/pull/stream', async (route) => {
      await streamGate
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: 'event: completed\ndata: {"state":"completed","bytes_downloaded":10,"bytes_total":10}\n\n',
      })
    })

    await seedSlots(page, [NPU_SLOT, EMBED])
    await page.goto('/#slots/npu')
    const chatRow = page.locator('.drawer .form-row', { hasText: 'NPU · Chat' })
    // Non-installed tags are offered with a ⬇ marker rather than hidden.
    await expect(chatRow.locator('select option', { hasText: 'flm-chat-xl' })).toHaveText(/⬇ download/)
    await chatRow.locator('select').selectOption('flm-chat-xl')

    // Pull is fired first; the slot config is NOT written yet.
    await expect.poll(() => pulls.length).toBe(1)
    expect(pulls[0]).toBe('POST')
    await page.waitForTimeout(300)
    expect(puts).toEqual([])

    releaseStream()

    // Weights landed → the same apply path as the installed pick.
    await expect.poll(() => puts.length, { timeout: 10_000 }).toBe(1)
    expect(puts[0].npu).toEqual({ chat: true, asr: false, embed: false })
    expect(puts[0].model).toEqual({ default: 'flm-chat-xl' })
    await expect.poll(() => restarts.length).toBe(1)
  })
})
