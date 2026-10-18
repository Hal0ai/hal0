/**
 * slot-drawer-profile-v3 — slot drawer profile/HW ownership spec.
 *
 * ADR-0029: the PROFILE is the slot's whole tune (flags, context window,
 * thinking, vision, MTP, runtime). The slot carries placement — device, NGL,
 * threads, binary — plus which model and which profile. The drawer therefore
 * shows the effective context and its source read-only next to the Profile
 * row, offers "Edit profile" (or "Duplicate & edit" for a read-only seed) next
 * to the picker, and never layers model flags under the profile or announces a
 * device switch driven by a profile (profiles no longer declare a backend).
 * Its save/restart contract lives in slot-drawer-inline-model-v3.
 */
import { test, expect, MOCK_DATA, type Page } from '../fixtures/apiMock'
import { pickRichOption, richSelectOption } from '../fixtures/richSelect'

// ─── Slot fixtures ──────────────────────────────────────────────────────────

const CHAT_CONTAINER = MOCK_DATA.slots.find(s => s.name === 'chat')!
const NPU_SLOT = MOCK_DATA.slots.find(s => s.name === 'npu')!
const TTS_SLOT = MOCK_DATA.slots.find(s => s.name === 'tts')!

// ─── HAL0_DATA seed helper (mirrors pattern from slot-edit-controls-v3) ────

async function seedSlots(page: Page, slots: any[]) {
  await page.addInitScript((slots) => {
    let real: any
    Object.defineProperty(window, 'HAL0_DATA', {
      configurable: true,
      get() { return real },
      set(v) {
        real = v
        if (v && typeof v === 'object') v.slots = slots
      },
    })
  }, slots)
}

// ─── Tests ──────────────────────────────────────────────────────────────────

test.describe('C7 — slot-owned hardware grid; no drawer profile selector', () => {

  test.skip('C7a — GPU slot: drawer has HW grid and no profile select', async ({ page }) => {
    await seedSlots(page, [CHAT_CONTAINER, NPU_SLOT, TTS_SLOT])
    await page.goto('/#slots/chat')
    await expect(page.locator('.drawer')).toBeVisible()

    // Device has no control in the drawer any more — it rides the model at
    // creation and is fixed for the slot's lifetime.
    await expect(page.getByTestId('slot-hw-device')).toHaveCount(0)
    await expect(page.getByTestId('slot-hw-ngl')).toBeVisible()
    await expect(page.getByTestId('slot-hw-threads')).toBeVisible()
    await expect(page.getByTestId('slot-hw-runtime')).toBeVisible()
    await expect(page.locator('.drawer .form-row', { hasText: 'Profile' }).locator('select')).toHaveCount(0)
  })

  test('C7a2 — slot card still surfaces the bound profile chip', async ({ page }) => {
    await seedSlots(page, [CHAT_CONTAINER])
    await page.goto('/#slots/chat')
    // Scoped to the InferencePane card's own chip (`infer-profile-{slot}`) —
    // a plain `button` text match now also catches the drawer's `slot-profile`
    // RichSelect trigger (Task 11c turned it from a <select> into a
    // `<button role="combobox">` whose closed-state text includes the same
    // profile name), which strict-mode-violates on two matches.
    await expect(page.getByTestId('infer-profile-chat')).toBeVisible()
    await expect(page.getByTestId('infer-profile-chat')).toContainText(CHAT_CONTAINER.profile || '')
  })

  test.skip('C7b — obsolete: profile changes no longer drive slot config/restart', async ({ page }) => {
    const configPuts: any[] = []
    const restartCalls: string[] = []

    await page.route('**/api/slots/chat/config', async (route) => {
      if (route.request().method() === 'PUT') {
        configPuts.push(JSON.parse(route.request().postData() || '{}'))
      }
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
    })
    await page.route('**/api/slots/chat/defaults', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: '{}' }),
    )
    await page.route('**/api/slots/chat/restart', async (route) => {
      restartCalls.push(route.request().method())
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
    })

    await seedSlots(page, [CHAT_CONTAINER])
    await page.goto('/#slots/chat')
    await expect(page.locator('.drawer')).toBeVisible()

    // Change profile to vulkan
    const profileRow = page.locator('.drawer .form-row', { hasText: 'Profile' })
    await profileRow.locator('select').selectOption('vulkan')

    await page.locator('.drawer button:has-text("Save")').click()

    // PUT /config must include profile: "vulkan"
    await expect.poll(() => configPuts.length).toBeGreaterThan(0)
    expect(configPuts[0].profile).toBe('vulkan')

    // Restart must fire after the config PUT
    await expect.poll(() => restartCalls.length).toBeGreaterThan(0)
    expect(restartCalls[0]).toBe('POST')
  })

  // C7c — no-op Save: no empty config PUT and no restart.
  test('C7c — no-op Save skips config PUT and restart', async ({ page }) => {
    const configPuts: any[] = []
    let restartCalled = false

    await page.route('**/api/slots/chat/config', async (route) => {
      if (route.request().method() === 'PUT') {
        configPuts.push(JSON.parse(route.request().postData() || '{}'))
      }
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
    })
    await page.route('**/api/slots/chat/defaults', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: '{}' }),
    )
    await page.route('**/api/slots/chat/restart', async (route) => {
      restartCalled = true
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
    })

    await seedSlots(page, [CHAT_CONTAINER])
    await page.goto('/#slots/chat')
    await expect(page.locator('.drawer')).toBeVisible()

    // Click Save immediately — there is no changed config to persist.
    await page.locator('.drawer button:has-text("Save")').click()

    await expect(page.locator('.drawer')).toHaveCount(0)
    expect(configPuts).toEqual([])
    expect(restartCalled).toBe(false)
  })

  // C7g — non-blocking save: a profile change kicks off the cold restart in
  // the BACKGROUND. The drawer must close immediately after the (fast) config
  // writes land, WITHOUT waiting for the slow POST /restart to resolve. This
  // is the fix for "save/edit hangs the dash" — restart can take model-load
  // seconds-to-minutes and must never block the UI.
  test.skip('C7g — obsolete: profile-change restart moved out of slot drawer', async ({ page }) => {
    let restartStarted = false
    let releaseRestart: () => void = () => {}
    const restartGate = new Promise<void>((resolve) => { releaseRestart = resolve })

    await page.route('**/api/slots/chat/config', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: '{}' }),
    )
    await page.route('**/api/slots/chat/defaults', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: '{}' }),
    )
    // Hold the restart request open for the whole assertion window — it stays
    // "in flight" so we can prove the drawer does not wait on it.
    await page.route('**/api/slots/chat/restart', async (route) => {
      restartStarted = true
      await restartGate
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
    })

    await seedSlots(page, [CHAT_CONTAINER])
    await page.goto('/#slots/chat')
    await expect(page.locator('.drawer')).toBeVisible()

    await page.locator('.drawer .form-row', { hasText: 'Profile' }).locator('select').selectOption('vulkan')
    await page.locator('.drawer button:has-text("Save")').click()

    // The restart must have been kicked off…
    await expect.poll(() => restartStarted).toBe(true)
    // …but the drawer must close while it is STILL pending (non-blocking).
    await expect(page.locator('.drawer')).toBeHidden()

    releaseRestart() // let the held request settle for clean teardown
  })

  test.skip('C7d — NPU slot: no profile editor; HW grid owns placement', async ({ page }) => {
    await seedSlots(page, [NPU_SLOT])
    await page.goto('/#slots/npu')
    await expect(page.locator('.drawer')).toBeVisible()

    await expect(page.getByTestId('slot-hw-device')).toHaveCount(0)
    await expect(page.locator('.drawer .form-row', { hasText: 'Profile' }).locator('select')).toHaveCount(0)
  })

  test.skip('C7e — TTS slot: no profile editor', async ({ page }) => {
    await seedSlots(page, [TTS_SLOT])
    await page.goto('/#slots/tts')
    await expect(page.locator('.drawer')).toBeVisible()

    await expect(page.locator('.drawer .form-row', { hasText: 'Profile' }).locator('select')).toHaveCount(0)
  })

  // C7f — Create modal (D2): device rides the MODEL, not a slot profile.
  // The simplified create picks a model (which carries tune/device/runner) and
  // names it; the POST body derives `device` from the model and omits `profile`.
  test('C7f — create modal: device derives from the model, no profile', async ({ page }) => {
    const createBodies: any[] = []

    await page.route('**/api/slots', async (route) => {
      if (route.request().method() === 'POST') {
        createBodies.push(JSON.parse(route.request().postData() || '{}'))
        await route.fulfill({ status: 201, contentType: 'application/json', body: '{"name":"test"}' })
      } else {
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ slots: [] }) })
      }
    })

    await page.goto('/#slots')
    await page.locator('button:has-text("New slot")').first().click()
    await expect(page.locator('.modal-shell')).toBeVisible()

    // Pick a model (the model carries device); name it; create.
    await page.getByTestId('create-slot-model').selectOption({ index: 1 })
    await page.getByTestId('create-slot-name').fill('test-slot')
    await page.getByTestId('create-slot-submit').click()

    await expect.poll(() => createBodies.length).toBeGreaterThan(0)
    // Device is derived from the model; there is no slot-level profile.
    expect(createBodies[0].device).toBeTruthy()
    expect(createBodies[0].profile).toBeUndefined()
  })

  // ─── MTP pill — RETIRED (spec-hw-slot-ownership §1) ──────────────────────
  //
  // The former slot-drawer MTP tri-state control (Task 2, C7i/C7j) is gone —
  // mtp is a model-owned tri-state cap now (ModelDefaults.mtp), edited on the
  // model drawer instead. Only the "the row is gone" assertion survives here;
  // the tri-state behavior itself is covered by model-drawer-save-info-v3
  // (the overrides ledger's model-cap-override-add-mtp-on/-off, since the
  // Option A restyle — Task 8, PR-3 — retired the cap-mtp-* always-on rows)
  // and the server-side reject by
  // tests/slot_config/test_validation_and_lock.py::TestRejectModelOwnedSlotKeys.

  const MTP_SLOT = { name: 'chat', type: 'llm', device: 'gpu-rocm', profile: 'rocm-mtp', backend: 'rocm',
    model_id: 'qwen-mtp', model: 'qwen-mtp', state: 'serving', port: 8092, runtime: 'container' }

  async function seedSlotsAndModels(page: Page, slots: any[], models: any[]) {
    await page.addInitScript(({ slots, models }: { slots: any[]; models: any[] }) => {
      let real: any
      Object.defineProperty(window, 'HAL0_DATA', {
        configurable: true, get() { return real },
        set(v) { real = v; if (v && typeof v === 'object') { v.slots = slots; v.models = models } },
      })
    }, { slots, models })
  }

  test('MTP control is gone from the slot drawer, even for an MTP-tagged model', async ({ page }) => {
    await seedSlotsAndModels(page, [MTP_SLOT], [{ id: 'qwen-mtp', name: 'qwen-mtp', capabilities: ['chat'], tags: ['mtp'] }])
    await page.goto('/#slots/chat')
    await expect(page.locator('.drawer')).toBeVisible()
    const row = page.locator('.drawer .form-row').filter({ has: page.locator('.form-lbl span', { hasText: /^MTP$/ }) })
    await expect(row).toHaveCount(0)
  })

  // ─── Chat-template override (Task 5) ────────────────────────────────────────

  // C7k (chat-template override) was removed with the control in #1379: the
  // per-slot template is inert at launch (`resolve_chat_template`: the key
  // "is no longer consulted"), and saving one fired a cold restart that
  // changed no argv. Template lives on the model drawer now.
})

// ─── ADR-0029 — the profile row: effective context, edit, duplicate ───────
test.describe('slot drawer profile row (ADR-0029)', () => {
  const CHAT = {
    ...(MOCK_DATA.slots.find(s => s.name === 'chat')!),
    profile: 'my-tune',
    ctx_effective: 65536,
    ctx_source: 'profile',
  }

  const MY_TUNE = {
    name: 'my-tune', flags: '-fa on -b 2048', resolved_flags: '-fa on -b 2048',
    ctx_size: 65536, enable_thinking: false, vision: 'auto', speculation: 'auto',
    seed: false, cloned_from: null, intent: 'My tune', tps: null, rtf: null,
    used_by: ['chat'], supported_slot_types: ['llm'],
  }
  const SEED = {
    name: 'rocm', flags: '-fa on', resolved_flags: '-fa on',
    ctx_size: 32768, enable_thinking: true, vision: 'auto', speculation: 'auto',
    seed: true, cloned_from: null, intent: 'MoE agents', tps: null, rtf: null,
    used_by: [], supported_slot_types: ['llm'],
  }

  async function mockProfiles(page: Page, rows: any[]) {
    await page.route('**/api/profiles', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(rows) }),
    )
  }

  test('shows the effective context and its source, read-only — no ceiling input', async ({ page }) => {
    await mockProfiles(page, [MY_TUNE, SEED])
    await seedSlots(page, [CHAT])
    await page.goto('/#slots/chat')
    await expect(page.locator('.drawer')).toBeVisible()

    const hint = page.getByTestId('slot-ctx-effective')
    await expect(hint).toContainText('65,536')
    await expect(hint).toContainText('set by profile my-tune')
    await expect(page.locator('.drawer .form-lbl > span', { hasText: /^Context \(ceiling\)$/ })).toHaveCount(0)
    await expect(page.getByTestId('slot-launch-tune-note')).toContainText('profile')
  })

  test('"Edit profile" opens the profiles drawer on the slot\'s profile', async ({ page }) => {
    await mockProfiles(page, [MY_TUNE, SEED])
    await seedSlots(page, [CHAT])
    await page.goto('/#slots/chat')
    await expect(page.locator('.drawer')).toBeVisible()

    const edit = page.getByTestId('slot-profile-edit')
    await expect(edit).toContainText('Edit profile')
    await edit.click()

    await expect(page.locator('.pf-form-panel')).toBeVisible()
    await expect(page.locator('.pf-form-title')).toHaveText('Edit · my-tune')
    await expect(page.getByTestId('pf-input-ctx')).toHaveValue('65536')
    await expect(page.getByTestId('pf-select-thinking')).toHaveValue('off')
    await expect(page.getByTestId('pf-input-name')).toBeDisabled()
  })

  test('a seed offers "Duplicate & edit"; saving the fork points the slot at it', async ({ page }) => {
    const posts: any[] = []
    const puts: any[] = []
    let created = false
    await page.route('**/api/profiles', async (route) => {
      if (route.request().method() === 'POST') {
        posts.push(JSON.parse(route.request().postData() || '{}'))
        created = true
        return route.fulfill({ status: 201, contentType: 'application/json', body: '{}' })
      }
      const fork = { ...SEED, name: 'rocm-custom', seed: false, cloned_from: 'rocm', used_by: [] }
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(created ? [SEED, fork] : [SEED]),
      })
    })
    await page.route('**/api/slots/chat/config', async (route) => {
      if (route.request().method() === 'PUT') puts.push(JSON.parse(route.request().postData() || '{}'))
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
    })
    await page.route('**/api/slots/chat/restart', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: '{}' }),
    )
    await seedSlots(page, [{ ...CHAT, profile: 'rocm', ctx_effective: 32768 }])
    await page.goto('/#slots/chat')
    await expect(page.locator('.drawer')).toBeVisible()

    const edit = page.getByTestId('slot-profile-edit')
    await expect(edit).toContainText('Duplicate & edit')
    await edit.click()

    await expect(page.locator('.pf-form-title')).toHaveText('Edit a copy · rocm')
    await expect(page.getByTestId('pf-input-name')).toHaveValue('rocm-custom')
    await page.getByTestId('pf-input-ctx').fill('131072')
    await page.getByTestId('pf-btn-submit').click()
    await expect(page.locator('.pf-form-panel')).toHaveCount(0)

    expect(posts).toHaveLength(1)
    expect(posts[0]).toMatchObject({ name: 'rocm-custom', cloned_from: 'rocm', ctx_size: 131072 })

    // The slot drawer now stages the fork as the slot's profile; Save applies it.
    await expect(page.getByTestId('slot-profile')).toContainText('rocm-custom')
    await page.locator('.drawer button:has-text("Save")').click()
    await expect.poll(() => puts.length).toBeGreaterThan(0)
    expect(puts[0].profile).toBe('rocm-custom')
  })

  test('picking another profile previews the context / thinking / flag changes, with no divergence or device-switch UI', async ({ page }) => {
    await mockProfiles(page, [MY_TUNE, SEED])
    await seedSlots(page, [CHAT])
    await page.goto('/#slots/chat')
    await expect(page.locator('.drawer')).toBeVisible()

    await pickRichOption(page.getByTestId('slot-profile'), 'rocm')

    const preview = page.getByTestId('slot-profile-preview')
    await expect(preview).toBeVisible()
    await expect(page.getByTestId('slot-profile-preview-context')).toContainText('64K')
    await expect(page.getByTestId('slot-profile-preview-context')).toContainText('32K')
    await expect(page.getByTestId('slot-profile-preview-thinking')).toContainText('off')
    await expect(page.getByTestId('slot-profile-preview-thinking')).toContainText('on')
    await expect(preview).toContainText('differ from the current profile')
    await expect(preview).toContainText('slot restarts on Save')

    // The model-flag overlay and the profile-driven device switch are gone.
    await expect(page.getByTestId('slot-profile-diverges')).toHaveCount(0)
    await expect(page.getByTestId('slot-profile-device-switch')).toHaveCount(0)
  })

  test('the profile picker is flat — no "other backend" group', async ({ page }) => {
    await mockProfiles(page, [MY_TUNE, SEED])
    await seedSlots(page, [CHAT])
    await page.goto('/#slots/chat')
    await expect(page.locator('.drawer')).toBeVisible()
    await richSelectOption(page.getByTestId('slot-profile'), 'rocm')
    await expect(page.locator('[data-option-id="__crossfit_header__"]')).toHaveCount(0)
  })
})

// ─── Profile apply flow — runner-carrying profile (Task 10 D4) ────────────
//
// A profile can pin a `runner` (a RUNNER_IMAGES key, Task 3). Picking one in the drawer's
// `slot-profile` select mirrors it through the SAME hw-cascade.js
// runnerOptions/applyRunnerChoice the Hardware group's `slot-hw-runtime`
// select uses (slot-modals.jsx's `profileRow` IIFE), so `slot-profile-preview`
// previews exactly what the server's profile-wins reconcile (Task 5) does on
// Save: `binary` flips to the profile's runner and — for a single-lane
// runner — `device` follows it.
test.describe('profile apply flow — runner-carrying profile applies a new runtime (Task 10 D4)', () => {
  const VULKAN_SLOT = {
    name: 'vulkan-chat', type: 'llm', device: 'gpu-vulkan',
    device_class: 'gpu', backend: 'vulkan',
    model: 'qwen-vulkan-demo', model_id: 'qwen-vulkan-demo',
    group: 'chat', state: 'ready', port: 8101, isDefault: true,
    runtime: 'container', profile: '',
    container_status: 'running', container_health: true,
    n_gpu_layers: -1, threads: 0,
    mem_mb: 4_000,
    metrics: { toks: 30, ttft: 260, ctx: 8192, kv: null, mem: 4.0 },
  }

  // A profile no longer declares a device_class/backend (ADR-0029), so the
  // `runner` cascade under test is the only thing that can move the device.
  const RUNNER_PROFILE = {
    name: 'single-lane-apply',
    flags: '', ctx_size: null, enable_thinking: null, vision: 'auto', speculation: 'auto',
    resolved_flags: '', seed: false, used_by: [],
    intent: 'Qwen3 TTS — single-lane ROCm runtime',
    supported_slot_types: ['llm'],
    runner: 'qwen3tts',
  }

  const SYSTEM_INFO_BACKENDS = {
    // hw-cascade.js's runnerOptions() vetoes a runner only when hw is KNOWN
    // and none of its lanes are feasible (a bare `hardware: {}` — no
    // gpus[0] — reads as unknown, never a veto, since the Task 12 fix).
    // Report rocm capable anyway so `qwen3tts`'s single lane resolves as
    // a real pick, not just "not vetoed", in both this cascade and the
    // Hardware group's own.
    hardware: { gpus: [{ compute_capable: true, vulkan_capable: true }] },
    features: {}, podman_context: 'rootless',
    backends: {
      qwen3tts: {
        title: 'Qwen3 TTS',
        image: 'ghcr.io/hal0ai/hal0-runner-qwen3tts:v1',
        runtime_family: 'llamacpp',
        device_class: 'gpu',
        backend: 'rocm',
        supported_backends: ['rocm'],
        format_arch: 'gguf',
        state: 'installed',
      },
    },
  }

  async function seedSlotsAndModels(page: Page, slots: any[], models: any[]) {
    await page.addInitScript(({ slots, models }: { slots: any[]; models: any[] }) => {
      let real: any
      Object.defineProperty(window, 'HAL0_DATA', {
        configurable: true, get() { return real },
        set(v) { real = v; if (v && typeof v === 'object') { v.slots = slots; v.models = models } },
      })
    }, { slots, models })
  }

  test('runner-carrying profile on a vulkan slot: preview renders, Save PUTs the new runtime + gpu-rocm device', async ({ page }) => {
    const puts: any[] = []
    const restarts: string[] = []
    await page.route('**/api/slots/vulkan-chat/config', async (route) => {
      if (route.request().method() === 'PUT') puts.push(JSON.parse(route.request().postData() || '{}'))
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
    })
    await page.route('**/api/slots/vulkan-chat/defaults', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: '{}' }),
    )
    await page.route('**/api/slots/vulkan-chat/restart', async (route) => {
      restarts.push(route.request().method())
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
    })
    await page.route('**/api/system-info', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(SYSTEM_INFO_BACKENDS) }),
    )
    // /api/profiles is `networkFirst` in the forced-mock allowlist (see
    // src/api/mock.ts MOCK_ALLOWLIST) — a page.route registration is tried
    // BEFORE the baked fixture, so this override is authoritative without
    // needing the __hal0MockPassthrough hatch. That hatch (#1498/#1527,
    // reused by #2170's own e2e work) is only needed for an endpoint the
    // forced allowlist substitutes UNCONDITIONALLY — /api/system-info isn't
    // allowlisted at all (real fetch, routed above) and /api/models is
    // allowlisted non-networkFirst, so it's seeded via the HAL0_DATA setter
    // below instead (mirrors buildModels()'s own documented escape hatch in
    // src/api/mockFixtures.ts, which page.route can't reach either).
    await page.route('**/api/profiles', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([RUNNER_PROFILE]) }),
    )
    // No models seeded — the slot's bound model_id is intentionally absent
    // from the catalog.
    await seedSlotsAndModels(page, [VULKAN_SLOT], [])
    await page.goto('/#slots/vulkan-chat')
    await expect(page.locator('.drawer')).toBeVisible()

    // Runtime starts on the persisted (auto/vulkan-device-derived) pick —
    // no preview yet.
    await expect(page.getByTestId('slot-profile-preview')).toHaveCount(0)

    await pickRichOption(page.getByTestId('slot-profile'), 'single-lane-apply')

    // Applying this profile previews the runtime it carries and that the
    // slot restarts on Save.
    const preview = page.getByTestId('slot-profile-preview')
    await expect(preview).toBeVisible()
    await expect(preview).toContainText('Qwen3 TTS')
    await expect(preview).toContainText('slot restarts on Save')

    // The Hardware group's Runtime select mirrors the SAME pick immediately
    // (both read from the one hw-cascade.js cascade) — proving the preview
    // isn't a display-only fiction. A RichSelect trigger isn't a <select>, so
    // this reads the closed trigger's rendered title text instead of
    // `.toHaveValue()`.
    await expect(page.getByTestId('slot-hw-runtime')).toContainText('Qwen3 TTS')

    await page.locator('.drawer button:has-text("Save")').click()
    await expect.poll(() => puts.length).toBeGreaterThan(0)
    expect(puts[0]).toMatchObject({
      binary: 'qwen3tts',
      device: 'gpu-rocm',
      profile: 'single-lane-apply',
    })
    // A runtime/device change is a hardware change — the cold restart fires
    // in the background after the config write lands.
    await expect.poll(() => restarts.length).toBeGreaterThan(0)
  })
})
