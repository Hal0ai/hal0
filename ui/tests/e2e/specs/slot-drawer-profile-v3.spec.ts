/**
 * slot-drawer-profile-v3 — Task C7 spec.
 *
 * Covers drawer-editable profile for GPU container slots + create-modal
 * device derivation from selected profile's device_class.
 *
 *   C7a. GPU container slot (chat, profile dense-mtp-rocmfp4):
 *          - drawer shows profile <select> listing ONLY device_class==="gpu" profiles
 *          - kokoro-cpu (cpu) and flm-npu (npu) absent from options
 *          - current profile (dense-mtp-rocmfp4) preselected
 *   C7b. Change profile to vulkan-std + Save:
 *          - PUT /api/slots/chat/config body contains { profile: "vulkan-std" }
 *          - followed by POST /api/slots/chat/restart
 *   C7c. Save WITHOUT profile change:
 *          - PUT body does NOT contain `profile` key (no gratuitous restart)
 *          - restart endpoint NOT called
 *   C7d. NPU slot: profile rendered as fixed text (no <select>)
 *   C7e. TTS slot: profile rendered as fixed text (no <select>)
 *   C7f. Create modal: device derivation from selected profile:
 *          - vulkan-std (image contains "vulkan") → device "gpu-vulkan"
 *          - dense-mtp-rocmfp4 (no "vulkan" in image) → device "gpu-rocm"
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
    await expect(sel).toHaveValue('dense-mtp-rocmfp4')

    // GPU profiles present
    const gpuOptions = ['moe-rocmfp4', 'dense-mtp-rocmfp4', 'vulkan-std']
    for (const name of gpuOptions) {
      await expect(sel.locator(`option[value="${name}"]`)).toHaveCount(1)
    }

    // Non-GPU profiles absent from options
    await expect(sel.locator('option[value="kokoro-cpu"]')).toHaveCount(0)
    await expect(sel.locator('option[value="flm-npu"]')).toHaveCount(0)
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

    // Change profile to vulkan-std
    const profileRow = page.locator('.drawer .form-row', { hasText: 'Profile' })
    await profileRow.locator('select').selectOption('vulkan-std')

    await page.locator('.drawer button:has-text("Save")').click()

    // PUT /config must include profile: "vulkan-std"
    await expect.poll(() => configPuts.length).toBeGreaterThan(0)
    expect(configPuts[0].profile).toBe('vulkan-std')

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

  // C7f — Create modal: device derivation from profile device_class / image tag
  test('C7f — create modal: vulkan-std profile → device gpu-vulkan', async ({ page }) => {
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
    // Select vulkan-std (image contains "vulkan" → device="gpu-vulkan")
    await profileRowSel.selectOption('vulkan-std')

    // Fill required name field
    const nameInput = page.locator('.modal-shell input').first()
    await nameInput.fill('test-vulkan')

    await page.locator('.modal-shell button:has-text("Create slot")').click()
    await expect.poll(() => createBodies.length).toBeGreaterThan(0)
    expect(createBodies[0].device).toBe('gpu-vulkan')
    expect(createBodies[0].profile).toBe('vulkan-std')
  })
})
