/**
 * slot-edit-controls-v3 — Spec 1 (slot edit panel controls).
 *
 * Covers the operator controls added to the slots page:
 *   C3. (RETIRED — #1369) the enabled toggle on the slot CARD is GONE:
 *       `SlotConfig.enabled` no longer exists, so a slot is activated by
 *       binding `[model].default` and the card fades on "no model" instead.
 *       A write of `enabled` through PUT /config is hard-rejected server-side
 *       (slot.removed_key_denied). The drawer's header toggle is Pinned/
 *       Unpinned now (#1367, slot-pin-toggle-v3.spec.ts).
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
 *       the Runtime-first cascade (runtime-cascade redesign, ADR-0006): a
 *       single Runtime select (`slot-hw-runtime`, hw-cascade.js's
 *       runnerOptions/applyRunnerChoice) replaces the old Device + Runner
 *       Image + Backend trio, with a Lane picker (`slot-hw-lane`) for a
 *       dual-lane runner and a collapsed Advanced disclosure
 *       (`slot-hw-advanced`) holding the read-only resolved image and a
 *       debug-only image_pin escape hatch (`slot-hw-image-pin`) — never an
 *       enumerated union. An out-of-vocab persisted runtime keeps its own
 *       "· not in this catalog" option instead of a blocking warning.
 *   C6. configured slots (a model bound) sort before unconfigured ones.
 *   (RETIRED — #1379) the Parallel and Extra Args controls are GONE, with
 *       the Template override and the Regenerate overlay. All three were
 *       inert at launch (spec-flags-ownership §1/§4); their absence and the
 *       wire-level guarantee are covered by slot-drawer-sunset-removal-v3.
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
  enable_thinking: false, n_gpu_layers: -1,
  metrics: { ctx: 8192, toks: 42, ttft: 180, kv: 35 },
}
const EMBED = {
  name: 'embed', type: 'embedding', device: 'gpu-rocm',
  model: 'nomic-embed', model_id: 'nomic-embed', modelLong: 'nomic-embed',
  group: 'embed', state: 'ready', port: 8095, isDefault: true,
  enable_thinking: null, n_gpu_layers: -1,
  metrics: {},
}

// hw-cascade.js's runnerOptions() vetoes a runner only when hw is KNOWN and
// NONE of its lanes are feasible (hostHwFlags reads a bare `hardware: {}` —
// no gpus[0] — as unknown, never a veto, since the Task 12 fix). Report both
// lanes capable anyway so a test that drives the rocm/vulkan Lane pills has
// a real feasible pair to pick from, rather than relying on a specific
// runtime's own declared lanes.
const HW_CAPABLE = { gpus: [{ compute_capable: true, vulkan_capable: true }] }

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

// NOTE: the per-card enable toggle, the fade modifier, and the
// configured-first sort were SlotCard-grid features. Both grids (Chat +
// Capabilities) were retired in favour of the InferencePane, so those tests
// were removed with the surface they covered. (#1369 then removed the
// `enabled` field the toggle wrote; the fade and sort key off model-presence.) The remaining tests exercise the
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
    // #1683: the popup portals to document.body (so overflow:hidden panels
    // can't clip it), so it's no longer a DOM descendant of `row` — find it
    // via the button's aria-describedby instead of a row-scoped locator.
    const popupId = await info.getAttribute('aria-describedby')
    // useId() ids contain colons (e.g. ":r0:"), invalid in a raw #id CSS
    // selector — use an attribute selector, which doesn't need escaping.
    const popup = page.locator(`[id="${popupId}"]`)
    await info.hover()
    await expect(popup).toContainText('emits -ngl')
    await page.mouse.move(0, 0)
    await expect(popup).toBeHidden()
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

  test('HW grid — Runtime + Threads + NGL render; Device select is gone; image pin hidden until Advanced opens (§2)', async ({ page }) => {
    await seedSlots(page, [PRIMARY, EMBED])
    await page.goto('/#slots/primary')
    // The standalone Device select is GONE (hw-cascade): `device` is derived
    // from the Runtime pick (and its Lane, for a dual-lane runner), so
    // Device/Runtime can no longer be driven into a mismatch from this
    // drawer. The old Runner Image catalog dropdown + Backend select are
    // gone too — replaced by the single Runtime select.
    await expect(page.locator('.drawer')).toBeVisible()
    await expect(page.getByTestId('slot-hw-device')).toHaveCount(0)
    await expect(page.getByTestId('slot-hw-ngl')).toBeVisible()
    await expect(page.getByTestId('slot-hw-threads')).toBeVisible()
    await expect(page.getByTestId('slot-hw-runtime')).toBeVisible()
    await expect(page.getByTestId('slot-hw-binary')).toHaveCount(0)
    // The image/version truth + debug pin live behind the collapsed
    // Advanced disclosure — not visible until it's opened.
    await expect(page.getByTestId('slot-hw-image-pin')).toHaveCount(0)
    await page.getByTestId('slot-hw-advanced').click()
    await expect(page.getByTestId('slot-hw-advanced-image')).toBeVisible()
  })

  test('HW grid — picking the Vulkan lane Save PUTs /config { device: gpu-vulkan } and restarts', async ({ page }) => {
    const puts: any[] = []
    const restarts: string[] = []
    await page.route('**/api/slots/primary/config', async (route) => {
      if (route.request().method() === 'PUT') puts.push(JSON.parse(route.request().postData() || '{}'))
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
    })
    await page.route('**/api/slots/primary/defaults', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: '{}' }),
    )
    await page.route('**/api/slots/primary/restart', async (route) => {
      restarts.push(route.request().method())
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
    })
    await page.route('**/api/system-info', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          hardware: HW_CAPABLE,
          features: {},
          podman_context: 'rootless',
          backends: {
            rocmfpx: {
              image: 'ghcr.io/hal0ai/tb:dual',
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
    await seedSlots(page, [{ ...PRIMARY, binary: 'rocmfpx' }, EMBED])
    await page.goto('/#slots/primary')
    // rocmfpx is a dual-lane runner (rocm + vulkan) — the Runtime select
    // already resolves to it (the slot's persisted binary), so the Lane
    // pills render immediately; picking the Vulkan lane derives
    // device=gpu-vulkan without touching Runtime at all.
    await expect(page.getByTestId('slot-hw-runtime')).toHaveValue('rocmfpx')
    await page.getByTestId('slot-hw-lane').getByRole('button', { name: 'Vulkan' }).click()
    await page.locator('.drawer button:has-text("Save")').click()
    await expect.poll(() => puts.length).toBeGreaterThan(0)
    expect(puts[0]).toMatchObject({ device: 'gpu-vulkan' })
    // A device flip is a hardware change — the cold restart fires in the
    // background after the config write lands.
    await expect.poll(() => restarts.length).toBeGreaterThan(0)
  })

  test('HW grid — Device select is absent on a non-GPU slot too', async ({ page }) => {
    await seedSlots(page, [{ ...PRIMARY, name: 'cpuish', device: 'cpu' }, EMBED])
    await page.goto('/#slots/cpuish')
    await expect(page.locator('.drawer')).toBeVisible()
    await expect(page.getByTestId('slot-hw-device')).toHaveCount(0)
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
              title: 'ROCm FPX',
              backend: 'rocm',
              device_class: 'gpu',
              supported_backends: ['rocm'],
              image: 'ghcr.io/hal0ai/runner:test',
            },
          },
        }),
      }),
    )
    await seedSlots(page, [{ ...PRIMARY, device: 'gpu-rocm', threads: 0, binary: '', image_pin: null }, EMBED])

    await page.goto('/#slots/primary')
    // Pick the Runtime — a single-lane runner's device is derived from it.
    await page.getByTestId('slot-hw-runtime').selectOption('rocmfpx')
    await page.getByTestId('slot-hw-ngl').fill('0')
    await page.getByTestId('slot-hw-threads').fill('8')
    // The debug pin is a free-text escape hatch behind the Advanced
    // disclosure now — never an enumerated catalog dropdown.
    await page.getByTestId('slot-hw-advanced').click()
    await page.getByTestId('slot-hw-debug-pin-open').click()
    await page.getByTestId('slot-hw-image-pin').fill('ghcr.io/example/runner:test')
    await page.locator('.drawer button:has-text("Save")').click()
    await expect.poll(() => puts.length).toBeGreaterThan(0)
    expect(puts[0]).toMatchObject({
      n_gpu_layers: 0,
      threads: 8,
      image_pin: 'ghcr.io/example/runner:test',
    })
    expect(puts[0].binary).toBe('rocmfpx')
  })

  // ADR-0006: every shipped runner image gets a RUNNER_IMAGES entry — the
  // Runtime select lists REGISTRY entries by title, not a raw-image-first
  // catalog dropdown. The #2170 "catalogued · downloaded" optgroup (a
  // downloaded-but-uncatalogued image class) has no members left once every
  // shipped image is registered, and is retired with it.
  test('HW grid — Runtime select lists registry entries by title (ADR-0006)', async ({ page }) => {
    await page.route('**/api/system-info', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          hardware: HW_CAPABLE,
          features: {},
          podman_context: 'rootless',
          backends: {
            rocmfpx: {
              title: 'ROCm FPX (default)',
              image: 'ghcr.io/hal0ai/tb:dual',
              runtime_family: 'llamacpp',
              device_class: 'gpu',
              backend: 'rocm',
              supported_backends: ['rocm', 'vulkan'],
              format_arch: 'gguf',
              state: 'installed',
              is_default: true,
            },
            strix: {
              title: 'Strix (Vulkan optional)',
              image: 'ghcr.io/hal0ai/tb:strix',
              runtime_family: 'llamacpp',
              device_class: 'gpu',
              backend: 'vulkan',
              supported_backends: ['vulkan'],
              format_arch: 'gguf',
              state: 'installed',
            },
          },
        }),
      }),
    )
    await seedSlots(page, [{ ...PRIMARY, binary: 'rocmfpx' }, EMBED])
    await page.goto('/#slots/primary')

    const sel = page.getByTestId('slot-hw-runtime')
    await expect(sel).toBeVisible()
    // Registry entries by TITLE — no optgroup split (the old
    // "catalogued · downloaded" grouping is gone) and no separate Runner
    // Image / Backend selects.
    await expect(sel.locator('option', { hasText: 'ROCm FPX (default)' })).toHaveCount(1)
    await expect(sel.locator('option', { hasText: 'Strix (Vulkan optional)' })).toHaveCount(1)
    await expect(sel.locator('optgroup')).toHaveCount(0)
    await expect(page.getByTestId('slot-hw-binary')).toHaveCount(0)
  })

  test('HW grid — a legacy image_pin surfaces the Advanced debug warning, not an enumerated Backend union', async ({ page }) => {
    await page.route('**/api/system-info', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          hardware: HW_CAPABLE,
          features: {},
          podman_context: 'rootless',
          backends: {
            rocmfpx: {
              title: 'ROCm FPX',
              image: 'ghcr.io/hal0ai/tb:dual',
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
    // #2118/ADR-0006: combined-upstream is a pin-only ref retired from the
    // release catalogue — a slot still carrying it (an older TOML) must
    // surface the Advanced debug warning + supersession banner, never an
    // enumerated Backend/Runner-Image union.
    await seedSlots(page, [
      { ...PRIMARY, binary: 'rocmfpx', image_pin: 'ghcr.io/hal0ai/hal0-combined-upstream:0829' },
      EMBED,
    ])
    await page.goto('/#slots/primary')

    await expect(page.getByTestId('slot-hw-runtime')).toHaveValue('rocmfpx')
    await page.getByTestId('slot-hw-advanced').click()
    await expect(page.getByTestId('slot-hw-image-pin')).toHaveValue(
      'ghcr.io/hal0ai/hal0-combined-upstream:0829',
    )
    await expect(page.getByTestId('slot-hw-debug-pin-warning')).toBeVisible()
    await expect(page.getByTestId('slot-hw-supersession-banner')).toBeVisible()
    // No enumerated Backend/Runner-Image union anywhere in the drawer.
    await expect(page.getByTestId('slot-hw-binary')).toHaveCount(0)
  })

  test('HW grid — an out-of-vocab persisted runtime keeps its own option instead of vanishing', async ({ page }) => {
    // rocmfpx is gpu-only; a cpu-device slot pinned to it does not fit. The
    // cascade can no longer CREATE that state, but a persisted TOML can
    // still carry it — selectedRunnerKey() returns null and the Runtime
    // select renders the persisted binary as its own "· not in this
    // catalog" option so the drawer never silently rewrites it (unit-tested
    // in hw-cascade.test.ts; this is the DOM-level guarantee).
    await page.route('**/api/system-info', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          hardware: HW_CAPABLE,
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
    const sel = page.getByTestId('slot-hw-runtime')
    await expect(sel).toHaveValue('rocmfpx')
    await expect(sel.locator('option', { hasText: 'not in this catalog' })).toHaveCount(1)
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
    // Label reads "Context (ceiling)": the bound model's own default
    // context_size is authoritative; this slot value only clamps it down for
    // lighter hardware, it never overrides it upward.
    const modelGroup = page.locator('.drawer .field-group').filter({
      has: page.locator('.field-group-label', { hasText: /^Model$/ }),
    })
    const contextRow = modelGroup.locator('.form-row').filter({
      has: page.locator('.form-lbl > span', { hasText: /^Context \(ceiling\)$/ }),
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
    await expect(modelGroup.getByLabel('Model for primary')).toBeVisible()
    // The Profile row moved INTO the Model group (under the model select) —
    // no standalone Profile group on non-NPU slots.
    await expect(modelGroup.getByTestId('slot-profile')).toBeVisible()
    await expect(page.locator('.field-group-label', { hasText: /^Profile$/i })).toHaveCount(0)
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
