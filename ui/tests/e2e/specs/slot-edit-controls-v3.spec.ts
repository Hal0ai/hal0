/**
 * slot-edit-controls-v3 — Spec 1 (slot edit panel controls).
 *
 * Covers the operator controls added to the slots page:
 *   C3. enabled toggle on the slot CARD → PUT /config { enabled } + fade.
 *   C4. (RETIRED — spec-hw-slot-ownership §1) the Reasoning/MTP/Vision pills
 *       that used to live in the drawer's "Inference" FieldGroup are GONE:
 *       enable_thinking/mtp/vision are model-owned tri-state caps now, edited
 *       in the model drawer (model-drawer-save-info-v3.spec.ts) instead. A
 *       write of any of the three through PUT /config is hard-rejected
 *       server-side (slot.model_owned_key_denied) — the drawer never sends
 *       them. The tests below assert the pills/group are absent.
 *   C5. NGL lives in the typed Hardware grid (spec-hw-slot-ownership §2),
 *       a TOP-LEVEL slot-owned field persisted via PUT /config { n_gpu_layers }
 *       (reversing the §5 fold into [model].n_gpu_layers). -1/empty = the "all
 *       layers" default; an untouched field never rides the PUT. ctx_size keeps
 *       its own PATCH /defaults path. The grid also carries device / THREADS /
 *       BINARY / image_pin, with a non-blocking fit-check warning (§4).
 *   C6. enabled slots sort before disabled ones in the grid.
 *
 * The dashboard renders the slot LIST from in-bundle HAL0_DATA
 * (VITE_MOCK_HAL0=1 short-circuits GET /api/slots before page.route
 * sees it — see src/api/mock.ts). So we control the list by intercepting
 * the `window.HAL0_DATA` assignment via addInitScript (`seedSlots`).
 * Mutations to /config + /defaults are NOT allowlisted, so they fall
 * through to real fetch and page.route captures their bodies.
 */
import { test, expect, type Page } from '../fixtures/apiMock'

const PRIMARY = {
  name: 'primary', type: 'llm', device: 'gpu-rocm', profile: 'rocm',
  model: 'qwen3.6-27b', model_id: 'qwen3.6-27b', modelLong: 'qwen3.6-27b',
  group: 'chat', state: 'serving', port: 8092, isDefault: true,
  enabled: true, enable_thinking: false, n_gpu_layers: -1,
  metrics: { ctx: 8192, toks: 42, ttft: 180, kv: 35 },
}
const EMBED = {
  name: 'embed', type: 'embedding', device: 'gpu-rocm',
  model: 'nomic-embed', model_id: 'nomic-embed', modelLong: 'nomic-embed',
  group: 'embed', state: 'ready', port: 8095, isDefault: true,
  enabled: true, enable_thinking: null, n_gpu_layers: -1,
  metrics: {},
}

/**
 * Override the in-bundle HAL0_DATA.slots for this page. data.jsx assigns
 * `window.HAL0_DATA = {...}` unconditionally at module load, so we install
 * a setter that patches `.slots` as the assignment lands — buildSlots()
 * then reads our list on every poll.
 */
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

// NOTE: the per-card enabled-toggle, the disabled-fade modifier, and the
// enabled-first sort were SlotCard-grid features. Both grids (Chat +
// Capabilities) were retired in favour of the InferencePane, so those tests
// were removed with the surface they covered. The remaining tests exercise the
// slot *edit drawer* (opened via the #slots/:name route), which is unchanged.

test.describe('Slot edit controls (/slots)', () => {
  test('C4 — Reasoning/MTP/Vision pills and the Inference group are gone from the drawer', async ({ page }) => {
    await seedSlots(page, [PRIMARY, EMBED])
    await page.goto('/#slots/primary')
    await expect(page.locator('.drawer')).toBeVisible()
    await expect(page.locator('.drawer .form-row', { hasText: 'Reasoning' })).toHaveCount(0)
    await expect(page.locator('.drawer .form-row', { hasText: /^MTP$/ })).toHaveCount(0)
    await expect(page.locator('.drawer .form-row', { hasText: /^Vision$/ })).toHaveCount(0)
    await expect(
      page.locator('.field-group-label', { hasText: /^Inference$/i }),
    ).toHaveCount(0)
  })

  test('C4 — a slot-config write never carries the model-owned keys', async ({ page }) => {
    // Regression guard for the #1333→#1334 restore-to-green loop: even a
    // full Save must never smuggle mtp/enable_thinking/vision back onto the
    // wire — the server hard-rejects them (slot.model_owned_key_denied).
    const puts: any[] = []
    await page.route('**/api/slots/primary/config', async (route) => {
      if (route.request().method() === 'PUT') puts.push(JSON.parse(route.request().postData() || '{}'))
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
    })
    await page.route('**/api/slots/primary/defaults', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: '{}' }),
    )
    await seedSlots(page, [PRIMARY, EMBED])
    await page.goto('/#slots/primary')
    await page.getByTestId('slot-hw-ngl').fill('24')
    await page.locator('.drawer button:has-text("Save")').click()
    await expect.poll(() => puts.length).toBeGreaterThan(0)
    for (const key of ['mtp', 'enable_thinking', 'vision']) {
      expect(puts[0]).not.toHaveProperty(key)
    }
  })

  test('C5 — NGL lives in the HW grid, editable with the -1 default', async ({ page }) => {
    await seedSlots(page, [PRIMARY, EMBED])

    await page.goto('/#slots/primary')
    // NGL moved out of Advanced into the top-level Hardware grid
    // (spec-hw-slot-ownership §2) — no disclosure to open.
    const input = page.getByTestId('slot-hw-ngl')
    await expect(input).toBeVisible()
    await expect(input).not.toHaveAttribute('readonly', '')
    await expect(input).toHaveValue('-1')
    const row = page.locator('.drawer .form-row', { hasText: 'NGL' })
    const info = row.getByRole('button', { name: 'Info' })
    await info.hover()
    await expect(row.locator('.field-info-pop')).toContainText('emits -ngl')
    await page.mouse.move(0, 0)
    await expect(row.locator('.field-info-pop')).toBeHidden()
  })

  test('Parallel description is available only from its info icon', async ({ page }) => {
    await seedSlots(page, [PRIMARY, EMBED])
    await page.goto('/#slots/primary')

    const row = page.locator('.drawer .form-row').filter({
      has: page.locator('.form-lbl > span', { hasText: /^Parallel$/ }),
    })
    await expect(row).toBeVisible()
    await expect(row.locator('.field-info-pop')).toContainText('How many requests can run at once')
    await expect(row.locator('.form-ctl > .hint')).toHaveCount(0)
  })

  test('C5 — editing NGL Save PUTs /config { n_gpu_layers } (top-level)', async ({ page }) => {
    const puts: any[] = []
    await page.route('**/api/slots/primary/config', async (route) => {
      if (route.request().method() === 'PUT') {
        puts.push(JSON.parse(route.request().postData() || '{}'))
      }
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
    })
    // ctx_size keeps its own /defaults path — stub it so an unrelated write
    // never falls through to real fetch.
    await page.route('**/api/slots/primary/defaults', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: '{}' }),
    )
    await seedSlots(page, [PRIMARY, EMBED])

    await page.goto('/#slots/primary')
    await page.getByTestId('slot-hw-ngl').fill('24')
    await page.locator('.drawer button:has-text("Save")').click()
    await expect.poll(() => puts.length).toBeGreaterThan(0)
    expect(puts[0].n_gpu_layers).toBe(24)
    // NGL is a TOP-LEVEL slot field now, not a [model] default — the untouched
    // ctx_size must not ride the /config PUT.
    expect(puts[0]).not.toHaveProperty('ctx_size')
  })

  test('C5 — clearing NGL back to empty PUTs the -1 default (unset)', async ({ page }) => {
    const puts: any[] = []
    await page.route('**/api/slots/primary/config', async (route) => {
      if (route.request().method() === 'PUT') {
        puts.push(JSON.parse(route.request().postData() || '{}'))
      }
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
    })
    await page.route('**/api/slots/primary/defaults', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: '{}' }),
    )
    // Baseline has an explicit override (24) — clearing resets to the -1 "all
    // layers" default (spec-hw-slot-ownership §2; empty ⇒ -1).
    await seedSlots(page, [{ ...PRIMARY, n_gpu_layers: 24 }, EMBED])

    await page.goto('/#slots/primary')
    await page.getByTestId('slot-hw-ngl').fill('')
    await page.locator('.drawer button:has-text("Save")').click()
    await expect.poll(() => puts.length).toBeGreaterThan(0)
    expect(puts[0]).toHaveProperty('n_gpu_layers', -1)
  })

  test('HW grid — the 4 typed fields + image_pin render (§2)', async ({ page }) => {
    await seedSlots(page, [PRIMARY, EMBED])
    await page.goto('/#slots/primary')
    await expect(page.getByTestId('slot-hw-device')).toBeVisible()
    await expect(page.getByTestId('slot-hw-ngl')).toBeVisible()
    await expect(page.getByTestId('slot-hw-threads')).toBeVisible()
    await expect(page.getByTestId('slot-hw-binary')).toBeVisible()
    await expect(page.getByTestId('slot-hw-image-pin')).toBeVisible()
  })

  test('HW grid — all editable fields persist their wire keys', async ({ page }) => {
    const puts: any[] = []
    await page.route('**/api/slots/primary/config', async (route) => {
      if (route.request().method() === 'PUT') puts.push(JSON.parse(route.request().postData() || '{}'))
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
    })
    await page.route('**/api/slots/primary/defaults', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: '{}' }),
    )
    await page.route('**/api/system-info', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          backends: {
            rocmfpx: {
              backend: 'rocm',
              supported_backends: ['rocm'],
              image: 'ghcr.io/hal0ai/runner:test',
            },
          },
        }),
      }),
    )
    await seedSlots(page, [{ ...PRIMARY, device: 'gpu-rocm', threads: 0, binary: '', image_pin: null }, EMBED])

    await page.goto('/#slots/primary')
    // Pick the runner while the gpu-rocm device still fits it — the Runner
    // options are filtered by the device lane (runner_matches predicate);
    // after the device flips to cpu the pick survives as an out-of-vocab
    // persisted option.
    await page.getByTestId('slot-hw-binary').selectOption({ index: 1 })
    await page.getByTestId('slot-hw-device').selectOption('cpu')
    await page.getByTestId('slot-hw-ngl').fill('0')
    await page.getByTestId('slot-hw-threads').fill('8')
    await page.getByTestId('slot-hw-image-pin').fill('ghcr.io/example/runner:test')
    await page.locator('.drawer button:has-text("Save")').click()
    await expect.poll(() => puts.length).toBeGreaterThan(0)
    expect(puts[0]).toMatchObject({
      device: 'cpu',
      n_gpu_layers: 0,
      threads: 8,
      image_pin: 'ghcr.io/example/runner:test',
    })
    expect(puts[0].binary).toBeTruthy()
  })

  test('HW grid — fit-check warns when device backend ∉ BINARY supported_backends (§4)', async ({ page }) => {
    // rocmfpx serves rocm/vulkan; a cpu-device slot pinned to it does not fit →
    // non-blocking warning. system-info supplies the supported_backends the
    // fit-check reads (spec-hw-slot-ownership §4).
    await page.route('**/api/system-info', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          hardware: {},
          features: {},
          podman_context: 'rootless',
          backends: {
            rocmfpx: {
              image: 'ghcr.io/hal0ai/tb:rocm',
              runtime_family: 'llamacpp',
              device_class: 'gpu',
              backend: 'rocm',
              supported_backends: ['rocm', 'vulkan'],
              format_arch: 'gguf',
              state: 'installed',
            },
          },
        }),
      }),
    )
    await seedSlots(page, [{ ...PRIMARY, device: 'cpu', binary: 'rocmfpx' }, EMBED])
    await page.goto('/#slots/primary')
    await expect(page.getByTestId('slot-hw-fit-warning')).toBeVisible()
  })

  test('extra_args is editable and persists under server', async ({ page }) => {
    const puts: any[] = []
    await page.route('**/api/slots/primary/config', async (route) => {
      if (route.request().method() === 'PUT') puts.push(JSON.parse(route.request().postData() || '{}'))
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
    })
    await page.route('**/api/slots/primary/defaults', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: '{}' }),
    )
    await seedSlots(page, [{ ...PRIMARY, llamacpp_args: '--threads 6' }, EMBED])

    await page.goto('/#slots/primary')
    const input = page.getByTestId('extra-args-input')
    await expect(input).toHaveValue('--threads 6')
    await input.fill('--threads 6 -fa on')
    await page.locator('.drawer button:has-text("Save")').click()
    await expect.poll(() => puts.length).toBeGreaterThan(0)
    expect(puts[0].server).toEqual({ extra_args: '--threads 6 -fa on' })
  })

  test('NPU modality controls remain visible and wire ASR updates', async ({ page }) => {
    const puts: any[] = []
    await page.route('**/api/slots/npu/config', async (route) => {
      if (route.request().method() === 'PUT') puts.push(JSON.parse(route.request().postData() || '{}'))
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
    })
    await page.route('**/api/slots/npu/restart', (route) =>
      route.fulfill({ status: 202, contentType: 'application/json', body: '{}' }),
    )
    await page.route('**/api/slots/flm/models', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ models: [{ model: 'flm-chat', installed: true }] }),
      }),
    )
    await seedSlots(page, [{
      ...PRIMARY,
      name: 'npu',
      device: 'npu',
      profile: 'flm',
      model: 'flm-chat',
      model_id: 'flm-chat',
      npu: { chat: true, asr: false, embed: false },
    }, EMBED])

    await page.goto('/#slots/npu')
    await expect(page.getByText('NPU · Chat')).toBeVisible()
    await expect(page.getByText('NPU · ASR')).toBeVisible()
    await expect(page.getByText('NPU · Embed')).toBeVisible()
    await page.locator('.drawer .form-row', { hasText: 'NPU · ASR' }).getByRole('switch').click()
    await expect.poll(() => puts.length).toBeGreaterThan(0)
    expect(puts[0].npu).toMatchObject({ chat: true, asr: true, embed: false })
  })

  test('C5 — editing ctx_size Save PATCHes /defaults { ctx_size }', async ({ page }) => {
    const patches: any[] = []
    await page.route('**/api/slots/primary/defaults', async (route) => {
      patches.push(JSON.parse(route.request().postData() || '{}'))
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
    })
    await page.route('**/api/slots/primary/config', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: '{}' }),
    )
    await seedSlots(page, [PRIMARY, EMBED])

    await page.goto('/#slots/primary')
    // Context is directly visible in the Model group (not inside Advanced).
    // Label reads "Context (override)" — spec-hw-slot-ownership §1: it is an
    // explicit override of the bound model's own default context_size.
    const modelGroup = page.locator('.drawer .field-group').filter({
      has: page.locator('.field-group-label', { hasText: /^Model$/ }),
    })
    const contextRow = modelGroup.locator('.form-row').filter({
      has: page.locator('.form-lbl > span', { hasText: /^Context \(override\)$/ }),
    })
    await expect(contextRow).toBeVisible()
    await contextRow.locator('input').fill('16384')
    await page.locator('.drawer button:has-text("Save")').click()
    await expect.poll(() => patches.length).toBeGreaterThan(0)
    expect(patches[0].ctx_size).toBe(16384)
    // Untouched n_gpu_layers (seeded -1 = unset) never rides the PATCH.
    expect(patches[0]).not.toHaveProperty('n_gpu_layers')
  })

  // #587: the slot-edit drawer used to seed idle_timeout_s / workers /
  // llamacpp_args from hardcoded constants and send all three
  // unconditionally on Save, clobbering the on-disk values. The fix
  // is two-layered:
  //   - the list payload carries the slot's real on-disk values, so the
  //     drawer seeds from truth;
  //   - the drawer dirty-tracks the seeded values and only ships fields
  //     that actually changed. This test exercises the second layer:
  //     opening the drawer on a slot whose payload lists e.g.
  //     idle_timeout_s=1200, then clicking Save without touching
  //     anything, must NOT send idle_timeout_s on the wire.
  test('#587 — no-op Save does not send idle_timeout_s / workers / extra_args', async ({ page }) => {
    const puts: any[] = []
    await page.route('**/api/slots/primary/config', async (route) => {
      if (route.request().method() === 'PUT') {
        puts.push(JSON.parse(route.request().postData() || '{}'))
      }
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
    })
    await page.route('**/api/slots/primary/defaults', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: '{}' }),
    )
    // PRIMARY carries the real on-disk values for the three clobber-
    // prone fields. The drawer must seed from these and stay quiet on
    // Save when nothing changed.
    const PRIMARY_WITH_DEFAULTS = {
      ...PRIMARY,
      idle_timeout_s: 1200,
      workers: 4,
      llamacpp_args: '--threads 6 --no-mmap',
    }
    await seedSlots(page, [PRIMARY_WITH_DEFAULTS, EMBED])

    await page.goto('/#slots/primary')
    // Click Save immediately — no field edits.
    await page.locator('.drawer button:has-text("Save")').click()
    await expect(page.locator('.drawer')).toHaveCount(0)
    expect(puts).toEqual([])
  })

  test('#587 — drawer has no idle_timeout_s / workers rows (profile-owned)', async ({ page }) => {
    // The clobber-prone per-slot rows were removed outright — runtime
    // tuning is owned by the profile, so the drawer no longer offers them.
    await seedSlots(page, [
      { ...PRIMARY, idle_timeout_s: 300, workers: 2, llamacpp_args: '' },
      EMBED,
    ])

    await page.goto('/#slots/primary')
    await expect(page.locator('.drawer')).toBeVisible()
    await expect(page.locator('.drawer .form-row', { hasText: 'idle_timeout_s' })).toHaveCount(0)
    await expect(page.locator('.drawer .form-row', { hasText: 'workers' })).toHaveCount(0)
  })

  test('drawer fields are grouped under SLOT / HARDWARE / MODEL (no Inference group)', async ({ page }) => {
    await seedSlots(page, [PRIMARY, EMBED])
    await page.goto('/#slots/primary')
    await expect(page.locator('.drawer')).toBeVisible()
    for (const label of ['Slot', 'Hardware', 'Model']) {
      await expect(page.locator('.field-group-label', { hasText: new RegExp(`^${label}$`, 'i') })).toHaveCount(1)
    }
    // spec-hw-slot-ownership §1: the former "Inference" group (Reasoning/MTP/
    // Vision) was removed outright — those caps moved to the model drawer.
    await expect(page.locator('.field-group-label', { hasText: /^Inference$/i })).toHaveCount(0)
    const modelGroup = page.locator('.field-group', { has: page.locator('.field-group-label', { hasText: /^Model$/i }) })
    await expect(modelGroup.locator('.form-row', { hasText: 'Model' }).locator('select')).toBeVisible()
  })

  test('default-for-type row is gone from the edit drawer and Save omits default', async ({ page }) => {
    const puts: any[] = []
    await page.route('**/api/slots/primary/config', async (route) => {
      if (route.request().method() === 'PUT') puts.push(JSON.parse(route.request().postData() || '{}'))
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
    })
    await page.route('**/api/slots/primary/defaults', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: '{}' }))
    await seedSlots(page, [PRIMARY, EMBED])
    await page.goto('/#slots/primary')
    await expect(page.locator('.drawer')).toBeVisible()
    await expect(page.locator('.drawer .form-row', { hasText: 'Default for type' })).toHaveCount(0)
    // Make a real config change so the emitted body can prove `default` stays absent.
    await page.getByTestId('slot-hw-ngl').fill('24')
    await page.locator('.drawer button:has-text("Save")').click()
    await expect.poll(() => puts.length).toBeGreaterThan(0)
    expect(puts[0]).toHaveProperty('n_gpu_layers', 24)
    expect(puts[0]).not.toHaveProperty('default')
  })
})
