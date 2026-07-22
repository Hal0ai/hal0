/**
 * slot-drawer-profile-v3 — slot drawer profile/HW ownership spec.
 *
 * The 1.0 hw-slot-ownership pivot removed drawer-editable profile/device
 * derivation. Profiles are logical tune templates; device/NGL/threads/binary
 * live on the slot HW grid.
 */
import { test, expect, MOCK_DATA, type Page } from '../fixtures/apiMock'

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

    await expect(page.getByTestId('slot-hw-device')).toBeVisible()
    await expect(page.getByTestId('slot-hw-ngl')).toBeVisible()
    await expect(page.getByTestId('slot-hw-threads')).toBeVisible()
    await expect(page.getByTestId('slot-hw-binary')).toBeVisible()
    await expect(page.locator('.drawer .form-row', { hasText: 'Profile' }).locator('select')).toHaveCount(0)
  })

  test('C7a2 — slot card still surfaces the bound profile chip', async ({ page }) => {
    await seedSlots(page, [CHAT_CONTAINER])
    await page.goto('/#slots/chat')
    await expect(page.locator('button', { hasText: CHAT_CONTAINER.profile || '' })).toBeVisible()
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

  // C7c — no-op profile Save: PUT body has no `profile` key, no restart
  test('C7c — no-op profile Save: PUT has no profile, restart not called', async ({ page }) => {
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

    // Click Save immediately — no profile change
    await page.locator('.drawer button:has-text("Save")').click()

    await expect.poll(() => configPuts.length).toBeGreaterThan(0)
    // Profile must NOT be in the body
    expect(configPuts[0]).not.toHaveProperty('profile')
    // Restart must NOT have fired
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

    await expect(page.getByTestId('slot-hw-device')).toBeVisible()
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

  // ─── MTP pill (Task 2) ───────────────────────────────────────────────────

  const MTP_SLOT = { name: 'chat', type: 'llm', device: 'gpu-rocm', profile: 'rocm-mtp', backend: 'rocm',
    model_id: 'qwen-mtp', model: 'qwen-mtp', state: 'serving', port: 8092, runtime: 'container', enabled: true, mtp: false }

  async function seedSlotsAndModels(page: Page, slots: any[], models: any[]) {
    await page.addInitScript(({ slots, models }: { slots: any[]; models: any[] }) => {
      let real: any
      Object.defineProperty(window, 'HAL0_DATA', {
        configurable: true, get() { return real },
        set(v) { real = v; if (v && typeof v === 'object') { v.slots = slots; v.models = models } },
      })
    }, { slots, models })
  }

  test('C7i — MTP control shows for MTP-capable model; On writes mtp:true + restart, Auto writes mtp:null', async ({ page }) => {
    const puts: any[] = []
    let restarted = false
    await page.route('**/api/slots/chat/config', async (route) => {
      if (route.request().method() === 'PUT') puts.push(JSON.parse(route.request().postData() || '{}'))
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
    })
    await page.route('**/api/slots/chat/restart', async (route) => { restarted = true; await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' }) })
    await seedSlotsAndModels(page, [MTP_SLOT], [{ id: 'qwen-mtp', name: 'qwen-mtp', capabilities: ['chat'], tags: ['rocmfp4', 'mtp'] }])
    await page.goto('/#slots/chat')
    // Use the exact label span text to avoid false-matches on "qwen-mtp" / "rocm-mtp" substrings
    const row = page.locator('.drawer .form-row').filter({ has: page.locator('.form-lbl span', { hasText: /^MTP$/ }) })
    await expect(row).toBeVisible()
    // Tri-state: forcing On writes mtp:true and restarts.
    await row.getByTestId('mtp-seg-on').click()
    await expect.poll(() => puts.length).toBeGreaterThan(0)
    expect(puts[0].mtp).toBe(true)
    await expect.poll(() => restarted).toBe(true)
    // Returning to Auto writes mtp:null (defer to model × profile).
    await row.getByTestId('mtp-seg-auto').click()
    await expect.poll(() => puts.length).toBeGreaterThan(1)
    expect(puts[1].mtp).toBeNull()
  })

  test('C7j — MTP control visible for a non-MTP model, with reason + force-on warning', async ({ page }) => {
    // Operator feedback: hiding the row for ineligible models made the state
    // undiscoverable (can't see WHY it's off; the force-on escape hatch had no
    // UI). The row is now ALWAYS shown on llm slots — for an ineligible model
    // it explains Auto is off and warns before a force-on that would fail at
    // launch. Fixture model carries neither the tag nor a name marker, and the
    // slot has NO override (mtp: null = Auto) — MTP_SLOT's mtp:false would
    // legitimately select "Off" instead of Auto.
    const slot = { ...MTP_SLOT, model_id: 'qwen-plain', model: 'qwen-plain', mtp: null }
    await seedSlotsAndModels(page, [slot], [{ id: 'qwen-plain', name: 'qwen-plain', capabilities: ['chat'], tags: ['rocmfp4'] }])
    await page.route('**/api/slots/chat/config', async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
    })
    await page.route('**/api/slots/chat/restart', async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
    })
    await page.goto('/#slots/chat')
    const row = page.locator('.drawer .form-row').filter({ has: page.locator('.form-lbl span', { hasText: /^MTP$/ }) })
    await expect(row).toBeVisible()
    // Auto is selected and the reason line names the model.
    await expect(row.getByTestId('mtp-seg-auto')).toHaveAttribute('aria-checked', 'true')
    await expect(row.locator('.mtp-eff').first()).toContainText('model has no MTP heads')
    // Forcing On surfaces the crash warning (escape hatch stays usable).
    await row.getByTestId('mtp-seg-on').click()
    await expect(row.getByTestId('mtp-force-warn')).toBeVisible()
  })

  // ─── Chat-template override (Task 5) ────────────────────────────────────────

  // C7k — Template row appears in the Model group; clicking [Override] reveals a
  // select; choosing chatml + Save writes chat_template:'chatml' in the config PUT
  // and fires a non-blocking restart (mirrors MTP toggle pattern).
  test('C7k — chat-template override: [Override] reveals select, Save writes chat_template + restart', async ({ page }) => {
    const CT_SLOT = {
      name: 'chat', type: 'llm', device: 'gpu-rocm', profile: 'rocm-mtp', backend: 'rocm',
      model_id: 'qwen-ct', model: 'qwen-ct', state: 'serving', port: 8092,
      runtime: 'container', enabled: true,
      // No chat_template override on disk — starts in read-only mode.
    }
    const CT_MODEL = { id: 'qwen-ct', name: 'qwen-ct', capabilities: ['chat'], tags: [], defaults: { chat_template: 'chatml' } }

    const puts: any[] = []
    let restarted = false

    await page.route('**/api/slots/chat/config', async (route) => {
      if (route.request().method() === 'PUT') puts.push(JSON.parse(route.request().postData() || '{}'))
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
    })
    await page.route('**/api/slots/chat/defaults', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: '{}' }),
    )
    await page.route('**/api/slots/chat/restart', async (route) => {
      restarted = true
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
    })
    await page.route('**/api/chat-templates', (route) =>
      route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify([
          { id: 'chatml', label: 'ChatML' },
          { id: 'llama3', label: 'Llama 3' },
          { id: 'qwen3.6-27b-mtp', label: 'Qwen3.6 27B MTP' },
        ]),
      }),
    )

    await seedSlotsAndModels(page, [CT_SLOT], [CT_MODEL])
    await page.goto('/#slots/chat')
    await expect(page.locator('.drawer')).toBeVisible()

    // Template row is visible in the Model group (read-only display)
    const tmplRow = page.locator('.drawer .form-row').filter({ has: page.locator('.form-lbl span', { hasText: /^Template$/ }) })
    await expect(tmplRow).toBeVisible()

    // [Override] button is present initially (no override active)
    const overrideBtn = tmplRow.locator('button', { hasText: 'Override' })
    await expect(overrideBtn).toBeVisible()

    // Click [Override] to reveal the select
    await overrideBtn.click()

    // The override select should now be visible
    const tmplSelect = tmplRow.locator('select')
    await expect(tmplSelect).toBeVisible()
    await expect(tmplSelect.locator('option[value="qwen3.6-27b-mtp"]')).toHaveCount(1)

    // Choose chatml
    await tmplSelect.selectOption('chatml')

    // Save
    await page.locator('.drawer button:has-text("Save")').click()

    // PUT body must include chat_template: 'chatml'
    await expect.poll(() => puts.length).toBeGreaterThan(0)
    expect(puts[0].chat_template).toBe('chatml')

    // Non-blocking restart must have fired
    await expect.poll(() => restarted).toBe(true)
  })

  test.skip('C7h — obsolete: model filtering no longer follows selected profile', async ({ page }) => {
    // The dashboard uses mockFetch (VITE_MOCK_HAL0=1) which short-circuits
    // page.route for allowlisted endpoints like /api/models — it reads
    // HAL0_DATA.models directly. Seed both slots AND models in a single
    // addInitScript so the setter patch covers both fields in one pass.
    // NOTE: capabilities: ['chat'] is required for the lib normalizeApiModel
    // to derive type='llm'; a bare type field is overwritten by deriveType().
    const testModels = [
      { id: 'qwen-fp4', name: 'qwen-fp4', capabilities: ['chat'], tags: ['rocmfp4'] },
      { id: 'qwen-plain', name: 'qwen-plain', capabilities: ['chat'], tags: [] },
    ]
    await page.addInitScript(({ slots, models }: { slots: any[], models: any[] }) => {
      let real: any
      Object.defineProperty(window, 'HAL0_DATA', {
        configurable: true,
        get() { return real },
        set(v) {
          real = v
          if (v && typeof v === 'object') {
            v.slots = slots
            v.models = models
          }
        },
      })
    }, { slots: [CHAT_CONTAINER], models: testModels })
    await page.goto('/#slots/chat')
    const modelSel = page.locator('.drawer .form-row', { hasText: 'Model' }).locator('select')
    await expect(modelSel.locator('option[value="qwen-fp4"]')).toHaveCount(1)   // rocm profile → fp4 present
    await page.locator('.drawer .form-row', { hasText: 'Profile' }).locator('select').selectOption('vulkan')
    await expect(modelSel.locator('option[value="qwen-fp4"]')).toHaveCount(0)   // vulkan → fp4 filtered out
    await expect(modelSel.locator('option[value="qwen-plain"]')).toHaveCount(1)
  })
})
