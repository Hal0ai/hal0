/**
 * slot-drawer-profile-v3 — Task C7 spec.
 *
 * Covers drawer-editable profile for GPU container slots + create-modal
 * device derivation from selected profile's device_class.
 *
 *   C7a. GPU container slot (chat, profile rocm-mtp):
 *          - drawer shows profile <select> listing ONLY device_class==="gpu" profiles
 *          - tts (cpu) and flm (npu) absent from options
 *          - current profile (rocm-mtp) preselected
 *   C7b. Change profile to vulkan + Save:
 *          - PUT /api/slots/chat/config body contains { profile: "vulkan" }
 *          - followed by POST /api/slots/chat/restart
 *   C7c. Save WITHOUT profile change:
 *          - PUT body does NOT contain `profile` key (no gratuitous restart)
 *          - restart endpoint NOT called
 *   C7d. NPU slot: profile rendered as fixed text (no <select>)
 *   C7e. TTS slot: profile rendered as fixed text (no <select>)
 *   C7f. Create modal: device derivation from selected profile's backend:
 *          - vulkan (backend "vulkan") → device "gpu-vulkan"
 *          - rocm-mtp (backend "rocm")  → device "gpu-rocm"
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

test.describe('C7 — drawer-editable profile + create-modal device derivation', () => {

  // C7a — GPU container slot: profile select shows only gpu profiles
  test('C7a — GPU slot: profile select shows only gpu-class profiles', async ({ page }) => {
    await seedSlots(page, [CHAT_CONTAINER, NPU_SLOT, TTS_SLOT])
    await page.goto('/#slots/chat')
    await expect(page.locator('.drawer')).toBeVisible()

    const profileRow = page.locator('.drawer .form-row', { hasText: 'Profile' }).first()
    await expect(profileRow).toBeVisible()

    // Must be a select (not readOnly input) for GPU slot
    const sel = profileRow.locator('select')
    await expect(sel).toBeVisible()

    // Current profile is preselected
    await expect(sel).toHaveValue('rocm-mtp')

    // GPU profiles present
    const gpuOptions = ['rocm', 'rocm-mtp', 'vulkan']
    for (const name of gpuOptions) {
      await expect(sel.locator(`option[value="${name}"]`)).toHaveCount(1)
    }

    // Non-GPU profiles absent from options
    await expect(sel.locator('option[value="tts"]')).toHaveCount(0)
    await expect(sel.locator('option[value="flm"]')).toHaveCount(0)
  })

  // C7a2 — profile options surface the intent label so custom profiles
  // (auto-named e.g. "rocm-custom") are recognizable, not just the bare name.
  test('C7a2 — GPU profile options show name · intent', async ({ page }) => {
    await seedSlots(page, [CHAT_CONTAINER])
    await page.goto('/#slots/chat')
    await expect(page.locator('.drawer')).toBeVisible()

    const sel = page.locator('.drawer .form-row', { hasText: 'Profile' }).first().locator('select')
    // The rocm-mtp option carries its intent ("Dense chat + MTP") in the label.
    await expect(sel.locator('option[value="rocm-mtp"]')).toContainText('Dense chat + MTP')
    await expect(sel.locator('option[value="vulkan"]')).toContainText('Vulkan std · fallback')
  })

  // C7b — profile change Save: PUT with profile + restart fires
  test('C7b — profile change: PUT includes profile + restart fires', async ({ page }) => {
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
  test('C7g — profile-change Save closes the drawer without awaiting restart', async ({ page }) => {
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

  // C7d — NPU slot: profile is fixed text (no select)
  test('C7d — NPU slot: profile rendered as fixed text, no select', async ({ page }) => {
    await seedSlots(page, [NPU_SLOT])
    await page.goto('/#slots/npu')
    await expect(page.locator('.drawer')).toBeVisible()

    const profileRow = page.locator('.drawer .form-row', { hasText: 'Profile' }).first()
    await expect(profileRow).toBeVisible()

    // Must be readOnly input, not select
    await expect(profileRow.locator('input[readonly]')).toBeVisible()
    await expect(profileRow.locator('select')).toHaveCount(0)
  })

  // C7e — TTS slot: profile is fixed text (no select)
  test('C7e — TTS slot: profile rendered as fixed text, no select', async ({ page }) => {
    await seedSlots(page, [TTS_SLOT])
    await page.goto('/#slots/tts')
    await expect(page.locator('.drawer')).toBeVisible()

    const profileRow = page.locator('.drawer .form-row', { hasText: 'Profile' }).first()
    await expect(profileRow).toBeVisible()

    // Must be readOnly input, not select
    await expect(profileRow.locator('input[readonly]')).toBeVisible()
    await expect(profileRow.locator('select')).toHaveCount(0)
  })

  // C7f — Create modal: device derivation from profile backend
  test('C7f — create modal: vulkan profile → device gpu-vulkan', async ({ page }) => {
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

    // Open the create modal via the "New slot" button
    await page.locator('button:has-text("New slot")').first().click()
    await expect(page.locator('.modal-shell')).toBeVisible()

    // Switch to container runtime: find the select with a "container" option
    const allSelects = page.locator('.modal-shell select')
    const selCount = await allSelects.count()
    for (let i = 0; i < selCount; i++) {
      const opts = await allSelects.nth(i).locator('option').allTextContents()
      if (opts.some(o => o.toLowerCase().includes('container'))) {
        await allSelects.nth(i).selectOption('container')
        break
      }
    }

    // Profile row appears after switching to container runtime
    const profileRowSel = page.locator('.modal-shell .form-row', { hasText: 'Profile' }).locator('select')
    await expect(profileRowSel).toBeVisible()
    // Select vulkan (backend "vulkan" → device="gpu-vulkan")
    await profileRowSel.selectOption('vulkan')

    // Fill required name field
    const nameInput = page.locator('.modal-shell input').first()
    await nameInput.fill('test-vulkan')

    await page.locator('.modal-shell button:has-text("Create slot")').click()
    await expect.poll(() => createBodies.length).toBeGreaterThan(0)
    expect(createBodies[0].device).toBe('gpu-vulkan')
    expect(createBodies[0].profile).toBe('vulkan')
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

  test('C7i — MTP pill shows for rocm slot + MTP-capable model and writes mtp:true + restart', async ({ page }) => {
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
    await row.locator('button[role="switch"]').click()
    await expect.poll(() => puts.length).toBeGreaterThan(0)
    expect(puts[0].mtp).toBe(true)
    await expect.poll(() => restarted).toBe(true)
  })

  test('C7j — MTP pill hidden when the model is not MTP-capable', async ({ page }) => {
    await seedSlotsAndModels(page, [MTP_SLOT], [{ id: 'qwen-mtp', name: 'qwen-mtp', capabilities: ['chat'], tags: ['rocmfp4'] }])
    await page.goto('/#slots/chat')
    await expect(page.locator('.drawer')).toBeVisible()
    // Expect no .form-row whose label span reads exactly "MTP"
    await expect(page.locator('.drawer .form-row').filter({ has: page.locator('.form-lbl span', { hasText: /^MTP$/ }) })).toHaveCount(0)
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
        body: JSON.stringify([{ id: 'chatml', label: 'ChatML' }, { id: 'llama3', label: 'Llama 3' }]),
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

  // C7h — model options re-filter from the SELECTED profile, not the persisted one
  test('C7h — model options re-filter from the SELECTED profile, not the persisted one', async ({ page }) => {
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
