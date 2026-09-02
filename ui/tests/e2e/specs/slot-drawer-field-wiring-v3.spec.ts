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
import { openRichSelect, pickRichOption } from '../fixtures/richSelect'

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
    // RichSelect's closed trigger renders the selected row's text directly
    // (Task 11c) — `longName` here, not a native <select> value.
    await expect(select).toContainText('Qwen3.6-27B-MTP')
    await pickRichOption(select, SWAP_TARGET)

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
    await pickRichOption(select, 'qwen3.6-27b-mtp')
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
    await pickRichOption(page.locator('.drawer').getByLabel('Model for primary'), SWAP_TARGET)

    const dialog = page.locator('.modal-shell', { hasText: 'Swap model' })
    await expect(dialog).toBeVisible()
    expect(swaps).toEqual([])

    await dialog.getByRole('button', { name: 'Cancel' }).click()
    await expect(dialog).toHaveCount(0)
    await page.waitForTimeout(200)
    expect(swaps).toEqual([])
    // Cancelling reverts the select to the bound model (value={cur}).
    await expect(page.locator('.drawer').getByLabel('Model for primary')).toContainText('Qwen3.6-27B-MTP')
  })

  test('W2 — confirming the live-swap dialog POSTs /swap { model_id }', async ({ page }) => {
    const swaps = await captureSwaps(page, 'primary')
    await seedSlots(page, [
      { ...PRIMARY, runtime: 'container', container_status: 'running', container_health: true },
      EMBED,
    ])

    await page.goto('/#slots/primary')
    await pickRichOption(page.locator('.drawer').getByLabel('Model for primary'), SWAP_TARGET)
    await page.locator('.modal-shell').getByRole('button', { name: 'Swap model' }).click()

    await expect.poll(() => swaps.length).toBe(1)
    expect(swaps[0]).toEqual({ model_id: SWAP_TARGET })
  })
})

// ─── GTT feasibility (Task 11d, host-truth signal 993ea3b6) ────────────────
//
// Task 12: cascades the drawer's two feasibility call sites into this file
// (rather than a new spec) since both hang off the same Model group this
// file already exercises. Mocked at the Playwright layer, following the
// mockProfiles/mockChatTemplates idiom (page.route per spec, request body
// captured/echoed rather than a fixed canned response) — the wire contract
// is `POST /api/models/feasibility {models:[{model_id, ctx?}]}` →
// `{results: [{model_id, verdict, needed_mb, gtt_free_mb, gtt_total_mb}]}`
// (`useModelsFeasibility.ts`). warn-never-block: a `fits`/`tight`/`exceeds`/
// `exceeds_total` verdict maps to `feasibility-copy.ts`'s exact hint text;
// this file only pins that the right verdict reaches the right surface, not
// the copy itself (already vitest-pinned in `feasibility-hint.test.ts`).
test.describe('Slot drawer — GTT feasibility (ceiling hint + model-row fit chips)', () => {
  /** Echo a per-model-id verdict for every probed model in the request body. */
  function mockFeasibility(page: Page, verdictById: Record<string, string>) {
    return page.route('**/api/models/feasibility', async (route) => {
      const body = route.request().postDataJSON() as { models: { model_id: string; ctx?: number }[] }
      const results = body.models.map((m) => ({
        model_id: m.model_id,
        verdict: verdictById[m.model_id] ?? 'unknown',
        needed_mb: 14_000,
        gtt_free_mb: 15_000,
        gtt_total_mb: 24_000,
      }))
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ results }) })
    })
  }

  function ctxCeilingInput(page: Page) {
    const hwGroup = page.locator('.drawer .field-group').filter({
      has: page.locator('.field-group-label', { hasText: /^How it runs$/ }),
    })
    return hwGroup
      .locator('.form-row')
      .filter({ has: page.locator('.form-lbl > span', { hasText: /^Context \(ceiling\)$/ }) })
      .locator('input')
  }

  test('the ceiling hint renders per verdict and re-probes (debounced) on a ctx edit', async ({ page }) => {
    await mockFeasibility(page, { 'qwen3.6-27b-mtp': 'fits' })
    await seedSlots(page, [OFFLINE_PRIMARY, EMBED])
    await page.goto('/#slots/primary')
    await expect(page.locator('.drawer')).toBeVisible()

    const hint = page.getByTestId('slot-ctx-feasibility')
    // Fires on drawer open (400ms debounce) — no keystroke needed yet.
    await expect(hint).toContainText('fits', { timeout: 2_000 })
    await expect(hint).toContainText('14 GB')

    // Re-mock to a verdict that maps to the "exceeds" copy, then edit ctx —
    // the SAME probe re-fires (debounced) for the new ceiling.
    await mockFeasibility(page, { 'qwen3.6-27b-mtp': 'exceeds' })
    await ctxCeilingInput(page).fill('32768')
    await expect(hint).toContainText('exceeds', { timeout: 2_000 })
  })

  test('an absent/unknown verdict renders no hint at all (warn-never-block)', async ({ page }) => {
    // No model_id in the map ⇒ every probed row echoes back 'unknown'.
    await mockFeasibility(page, {})
    await seedSlots(page, [OFFLINE_PRIMARY, EMBED])
    await page.goto('/#slots/primary')
    await expect(page.locator('.drawer')).toBeVisible()
    await page.waitForTimeout(700) // past the 400ms debounce
    await expect(page.getByTestId('slot-ctx-feasibility')).toHaveCount(0)
  })

  test('model dropdown rows carry a per-model fit chip from ONE batch probe fired on open', async ({ page }) => {
    const probes: any[] = []
    await page.route('**/api/models/feasibility', async (route) => {
      const body = route.request().postDataJSON() as { models: { model_id: string }[] }
      // The 400ms-debounced CEILING probe (fired on drawer open and on ctx
      // edits — pinned by the ceiling-hint tests above) shares this endpoint
      // but always carries exactly ONE model: the bound one. Count only
      // BATCH probes (every compatible model) here — this test pins the
      // dropdown's own contract (one batch per open, never per keystroke or
      // per row), not a ban on the ceiling probe's independent traffic.
      // Counting both made this test a race between the poll assertions and
      // the ceiling debounce timer.
      if (body.models.length > 1) probes.push(body)
      const verdictById: Record<string, string> = {
        'qwen3.6-27b-mtp': 'fits',
        [SWAP_TARGET]: 'exceeds',
      }
      const results = body.models.map((m) => ({
        model_id: m.model_id,
        verdict: verdictById[m.model_id] ?? 'unknown',
        needed_mb: 14_000,
        gtt_free_mb: 15_000,
        gtt_total_mb: 24_000,
      }))
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ results }) })
    })
    await seedSlots(page, [OFFLINE_PRIMARY, EMBED])
    await page.goto('/#slots/primary')
    await expect(page.locator('.drawer')).toBeVisible()

    const trigger = page.locator('.drawer').getByLabel('Model for primary')
    const listbox = await openRichSelect(trigger)
    // Fired once on the open transition — never per keystroke, never per row.
    await expect.poll(() => probes.length).toBe(1)
    await expect(listbox.locator('[data-option-id="qwen3.6-27b-mtp"]')).toContainText('● fits · ~14 GB')
    await expect(listbox.locator(`[data-option-id="${SWAP_TARGET}"]`)).toContainText("○ won't fit")
    // Each fresh open→true transition fires exactly one more probe (still
    // never per keystroke/per row); closing itself fires nothing. Close via
    // a click on a plain label (RichSelect's click-outside listener);
    // Escape would work too now that rich-select.jsx consumes it on an open
    // listbox, but the click path is what this test has always pinned.
    await page.locator('.drawer .form-lbl', { hasText: 'Runtime' }).first().click()
    await openRichSelect(trigger)
    await expect.poll(() => probes.length).toBe(2)
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
