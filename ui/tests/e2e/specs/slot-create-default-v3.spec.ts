/**
 * slot-create-default-v3 — the create-slot "Set as default" checkbox now works.
 *
 * Checking "Set as default" sends `default: true` in the POST /api/slots body;
 * the backend promotes the slot's MODEL as its type's default (verified in
 * tests/api/test_models_default.py). Here we assert the wire contract: the
 * checkbox drives the payload flag, and leaving it unchecked omits it.
 */
import { test, expect, type Page } from '../fixtures/apiMock'

async function openCreateModal(page: import('@playwright/test').Page) {
  await page.goto('/#slots')
  await page.locator('.view .vh button:has-text("New slot")').click()
  await expect(page.getByTestId('create-slot-model')).toBeVisible()
}

// GET /api/models is served entirely from `window.HAL0_DATA.models` in the
// forced-mock dev server this suite runs against (src/api/mock.ts's
// MOCK_ALLOWLIST short-circuits it before any fetch — page.route can't see
// it, unlike POST /api/slots which opts out via `{raw: true}`). Patching the
// catalog therefore has to happen at the HAL0_DATA seed layer, same pattern
// as slot-drawer-profile-v3.spec.ts's `seedSlots`: intercept the `HAL0_DATA`
// assignment dash/data.jsx makes on load and substitute `models`.
async function seedModels(page: Page, models: any[]) {
  await page.addInitScript((models) => {
    let real: any
    Object.defineProperty(window, 'HAL0_DATA', {
      configurable: true,
      get() { return real },
      set(v) {
        real = v
        if (v && typeof v === 'object') v.models = models
      },
    })
  }, models)
}

// The empty-slot-card "Configure" flow (dash/slots.jsx openCreatePrefilled)
// opens the modal via the same `hal0:create-slot` window event the N-hotkey
// and command palette use, with `{ name, type, device }` in `detail` — no
// `model`. Firing it directly here exercises CreateSlotModal's preselect
// path without depending on the skip-path seeded-card layout.
async function openCreateModalWithDefaults(page: import('@playwright/test').Page, detail: Record<string, unknown>) {
  await page.goto('/#slots')
  // The `hal0:create-slot` listener is wired by a Slots-page useEffect, so
  // dispatching before that mount commits is a no-op. Wait for a
  // page-is-interactive signal (the "New slot" button, present as soon as
  // the view header renders) before firing the event.
  await expect(page.locator('.view .vh button:has-text("New slot")')).toBeVisible()
  await page.evaluate((d) => {
    window.dispatchEvent(new CustomEvent('hal0:create-slot', { detail: d }))
  }, detail)
  await expect(page.getByTestId('create-slot-model')).toBeVisible()
}

test.describe('Create slot — set-as-default checkbox', () => {
  test('checking the box sends default:true in the create payload', async ({ page }) => {
    let posted: any = null
    await page.route('**/api/slots', (route) => {
      if (route.request().method() === 'POST') {
        posted = route.request().postDataJSON?.() ?? {}
        return route.fulfill({
          status: 201,
          contentType: 'application/json',
          body: JSON.stringify({ name: posted.name, state: 'offline', default_promotion: { promoted: true } }),
        })
      }
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ slots: [] }) })
    })

    await openCreateModal(page)
    await page.getByTestId('create-slot-model').selectOption({ index: 1 })
    await page.getByTestId('create-slot-name').fill('coder')
    await page.getByTestId('create-slot-default').check()
    await page.getByTestId('create-slot-submit').click()

    await expect.poll(() => posted?.default).toBe(true)
    expect(posted.name).toBe('coder')
  })

  test('leaving the box unchecked omits default from the payload', async ({ page }) => {
    let posted: any = null
    await page.route('**/api/slots', (route) => {
      if (route.request().method() === 'POST') {
        posted = route.request().postDataJSON?.() ?? {}
        return route.fulfill({
          status: 201,
          contentType: 'application/json',
          body: JSON.stringify({ name: posted.name, state: 'offline' }),
        })
      }
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ slots: [] }) })
    })

    await openCreateModal(page)
    await page.getByTestId('create-slot-model').selectOption({ index: 1 })
    await page.getByTestId('create-slot-name').fill('coder')
    await page.getByTestId('create-slot-submit').click()

    await expect.poll(() => posted?.name).toBe('coder')
    expect(posted.default).toBeUndefined()
  })
})

// Two chat (dispatcher type "llm") models + one embedding model, so a test
// can assert the default lookup is scoped to the right type. `capabilities`
// (not the legacy `labels`) is required for normalizeApiModel to derive
// `type` — see slot-drawer-profile-v3.spec.ts's C7i note on this exact gotcha.
const CHAT_DEFAULT = { id: 'chat-default-model', name: 'chat-default-model', capabilities: ['chat'], installed: true }
const CHAT_OTHER = { id: 'chat-other-model', name: 'chat-other-model', capabilities: ['chat'], installed: true }
const EMBED_MODEL = { id: 'embed-model', name: 'embed-model', capabilities: ['embed'], installed: true }

test.describe('Create slot — model preselection from the type default', () => {
  test('opening with a derived type but no explicit model preselects that type\'s default model', async ({ page }) => {
    await seedModels(page, [
      { ...CHAT_DEFAULT, default: true },
      { ...CHAT_OTHER, default: false },
      { ...EMBED_MODEL, default: false },
    ])
    await openCreateModalWithDefaults(page, { name: 'chat-util', type: 'llm', device: 'gpu-vulkan' })

    await expect(page.getByTestId('create-slot-model')).toHaveValue('chat-default-model')
  })

  test('an explicit defaults.model is never overridden by the type default', async ({ page }) => {
    // The type default is a *different* chat model than the one pinned via
    // defaults.model — the explicit pin must win.
    await seedModels(page, [
      { ...CHAT_DEFAULT, default: true },
      { ...CHAT_OTHER, default: false },
    ])
    await openCreateModalWithDefaults(page, {
      name: 'chat-util',
      type: 'llm',
      model: 'chat-other-model',
    })

    await expect(page.getByTestId('create-slot-model')).toHaveValue('chat-other-model')
  })

  test('a default marked on a different type is not honoured — picker stays blank', async ({ page }) => {
    // embed-model is the default, but the modal opened for the "llm" type —
    // the embedding-type default must not leak into the chat picker.
    await seedModels(page, [
      { ...CHAT_OTHER, default: false },
      { ...EMBED_MODEL, default: true },
    ])
    await openCreateModalWithDefaults(page, { name: 'chat-util', type: 'llm' })

    await expect(page.getByTestId('create-slot-model')).toHaveValue('')
  })

  test('no default set for the type leaves the picker blank, as before', async ({ page }) => {
    await seedModels(page, [
      { ...CHAT_DEFAULT, default: false },
      { ...CHAT_OTHER, default: false },
    ])
    await openCreateModalWithDefaults(page, { name: 'chat-util', type: 'llm' })

    await expect(page.getByTestId('create-slot-model')).toHaveValue('')
  })
})
