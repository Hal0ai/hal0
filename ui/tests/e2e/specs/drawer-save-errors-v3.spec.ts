/**
 * drawer-save-errors-v3 — rejected-write behaviour for the slot + model drawers.
 *
 * Every slot/model drawer spec in the suite asserts the HAPPY path: the write
 * goes out, the drawer closes. Nothing covered what happens when the server
 * says no — and that is exactly the path where an operator can silently lose
 * work. The contract these tests pin:
 *
 *   1. a rejected write NEVER closes the drawer,
 *   2. the operator's edits are still in the fields afterwards,
 *   3. the backend's error-envelope `message` is surfaced verbatim (the client
 *      lifts `{error:{code,message,details}}` in src/api/client.ts),
 *   4. a failed FIRST write short-circuits the second one (the drawer's Save
 *      is a two-step PATCH /defaults → PUT /config; a defaults failure must
 *      not leave a half-applied config behind),
 *   5. an instant-apply toggle that the server rejects REVERTS to server truth
 *      instead of lying about the new state.
 *
 * Realistic envelopes are used throughout — `validation.unknown_keys` from
 * `_reject_unknown_config_keys`, and the NPU-exclusivity 409 the slot manager
 * raises when a second device=npu llm anchor is enabled.
 */
import { test, expect, type Page } from '../fixtures/apiMock'

const PRIMARY = {
  name: 'primary', type: 'llm', device: 'gpu-rocm', profile: 'rocm',
  model: 'qwen3.6-27b-mtp', model_id: 'qwen3.6-27b-mtp',
  modelLong: 'Qwen3.6-27B-MTP',
  group: 'chat', state: 'serving', port: 8092, isDefault: true,
  enabled: true, n_gpu_layers: -1, ctx_max: 8192,
  metrics: { ctx: 8192 },
}

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

/** The backend's structured error envelope (hal0.api.middleware.error_codes). */
function envelope(code: string, message: string, details: Record<string, unknown> = {}) {
  return JSON.stringify({ error: { code, message, details } })
}

const drawer = (page: Page) => page.locator('.drawer')

test.describe('Slot drawer — rejected writes', () => {
  test('E1 — a 400 from PUT /config keeps the drawer open with the edit intact', async ({ page }) => {
    await page.route('**/api/slots/primary/config', (route) =>
      route.fulfill({
        status: 400,
        contentType: 'application/json',
        body: envelope(
          'validation.unknown_keys',
          'unknown slot config key(s): n_gpu_layerss',
          { unknown_keys: ['n_gpu_layerss'] },
        ),
      }),
    )
    await page.route('**/api/slots/primary/defaults', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: '{}' }),
    )
    await seedSlots(page, [PRIMARY])

    await page.goto('/#slots/primary')
    await page.getByTestId('slot-hw-ngl').fill('24')
    await drawer(page).locator('button:has-text("Save")').click()

    // The envelope message is surfaced verbatim next to the Save button…
    await expect(drawer(page)).toContainText('unknown slot config key(s): n_gpu_layerss')
    // …the drawer stays open…
    await expect(drawer(page)).toBeVisible()
    // …and the operator's edit is still there to correct or retry.
    await expect(page.getByTestId('slot-hw-ngl')).toHaveValue('24')
  })

  test('E2 — a failed PATCH /defaults short-circuits the /config PUT', async ({ page }) => {
    // Save is a two-step write: defaults FIRST, then config. If the first leg
    // fails the second must not fire, or the slot ends up half-applied.
    const puts: any[] = []
    await page.route('**/api/slots/primary/config', async (route) => {
      if (route.request().method() === 'PUT') puts.push(route.request().postDataJSON())
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
    })
    await page.route('**/api/slots/primary/defaults', (route) =>
      route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: envelope('slot.config_write_failed', 'failed to rewrite primary.toml'),
      }),
    )
    await seedSlots(page, [PRIMARY])

    await page.goto('/#slots/primary')
    // Touch BOTH legs: ctx_size rides /defaults, NGL rides /config.
    const modelGroup = page.locator('.drawer .field-group').filter({
      has: page.locator('.field-group-label', { hasText: /^Model$/ }),
    })
    await modelGroup
      .locator('.form-row')
      .filter({ has: page.locator('.form-lbl > span', { hasText: /^Context \(ceiling\)$/ }) })
      .locator('input')
      .fill('16384')
    await page.getByTestId('slot-hw-ngl').fill('24')
    await drawer(page).locator('button:has-text("Save")').click()

    await expect(drawer(page)).toContainText('failed to rewrite primary.toml')
    await expect(drawer(page)).toBeVisible()
    // The second leg never ran.
    expect(puts).toEqual([])
  })

  test('E3 — a rejected model swap surfaces inline and leaves the drawer open', async ({ page }) => {
    await page.route('**/api/slots/primary/swap', (route) =>
      route.fulfill({
        status: 409,
        contentType: 'application/json',
        body: envelope('slot.busy', 'slot primary is warming — retry once it is ready'),
      }),
    )
    await page.route('**/api/slots/primary/config', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: '{}' }),
    )
    await page.route('**/api/slots/primary/defaults', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: '{}' }),
    )
    await seedSlots(page, [
      { ...PRIMARY, state: 'offline', runtime: 'container', container_status: 'stopped', container_health: false },
    ])

    await page.goto('/#slots/primary')
    await drawer(page).getByLabel('Model for primary').selectOption('qwen3-coder-30b')

    await expect(drawer(page)).toContainText('slot primary is warming — retry once it is ready')
    await expect(drawer(page)).toBeVisible()
  })

  test('E4 — a rejected NPU toggle reverts the switch instead of lying', async ({ page }) => {
    // The npu-exclusivity 409 the slot manager raises when a second
    // device=npu llm anchor is enabled.
    await page.route('**/api/slots/npu/config', (route) =>
      route.fulfill({
        status: 409,
        contentType: 'application/json',
        body: envelope(
          'slot.npu_exclusive',
          'another device=npu llm slot is already enabled',
          { conflicting: 'npu-other' },
        ),
      }),
    )
    await page.route('**/api/slots/flm/models', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ models: [{ model: 'flm-chat', installed: true }] }),
      }),
    )
    await seedSlots(page, [
      {
        ...PRIMARY,
        name: 'npu',
        device: 'npu',
        profile: 'flm',
        model: 'flm-chat',
        model_id: 'flm-chat',
        npu: { chat: true, asr: false, embed: false },
      },
    ])

    await page.goto('/#slots/npu')
    const embedSwitch = page.locator('.drawer .form-row', { hasText: 'NPU · Embed' }).getByRole('switch')
    await expect(embedSwitch).toHaveAttribute('aria-checked', 'false')
    await embedSwitch.click()

    // Error surfaced…
    await expect(drawer(page)).toContainText('another device=npu llm slot is already enabled')
    // …and the optimistic flip is rolled back to server truth.
    await expect(embedSwitch).toHaveAttribute('aria-checked', 'false')
  })
})

test.describe('Model drawer — rejected writes', () => {
  const MODEL_ID = 'qwen3.6-27b-mtp'

  async function stubLookups(page: Page) {
    await page.route('**/api/profiles', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) }),
    )
    await page.route('**/api/chat-templates', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([{ id: 'auto', label: 'Auto (GGUF embedded)' }]),
      }),
    )
  }

  async function openDrawer(page: Page) {
    await page.goto('/#models')
    await page.locator('button:has-text("Edit options")').first().click()
    await expect(page.getByTestId('model-flags-input')).toBeVisible()
  }

  test('E5 — a rejected PUT keeps the drawer open with edits intact', async ({ page }) => {
    await stubLookups(page)
    let puts = 0
    await page.route(`**/api/models/${MODEL_ID}`, async (route) => {
      if (route.request().method() === 'PUT') {
        puts += 1
        return route.fulfill({
          status: 409,
          contentType: 'application/json',
          body: envelope(
            'model.in_use',
            'model qwen3.6-27b-mtp is bound to a running slot — stop it first',
            { slots: ['primary'] },
          ),
        })
      }
      return route.fallback()
    })

    await openDrawer(page)
    await page.getByTestId('model-name-input').fill('Renamed while failing')
    await page.getByTestId('model-ctx-input').fill('16384')
    await page.getByTestId('model-save').click()

    await expect.poll(() => puts).toBe(1)
    // Envelope message surfaced verbatim inside the drawer…
    await expect(page.locator('.drawer.open')).toContainText(
      'model qwen3.6-27b-mtp is bound to a running slot — stop it first',
    )
    // …drawer stays open with BOTH edits still in the fields.
    await expect(page.locator('.drawer.open')).toHaveCount(1)
    await expect(page.getByTestId('model-name-input')).toHaveValue('Renamed while failing')
    await expect(page.getByTestId('model-ctx-input')).toHaveValue('16384')
  })

  test('E6 — a hal0-managed flag blocks Save client-side before any PUT', async ({ page }) => {
    // `--port` is owned by the launch path; the drawer refuses it locally so the
    // operator gets the message without a round-trip (the server screens it too,
    // via screen_model_write).
    await stubLookups(page)
    let puts = 0
    await page.route(`**/api/models/${MODEL_ID}`, async (route) => {
      if (route.request().method() === 'PUT') {
        puts += 1
        return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
      }
      return route.fallback()
    })

    await openDrawer(page)
    await page.getByTestId('model-flags-input').fill('--port 9999')

    await expect(page.getByTestId('model-save')).toBeDisabled()
    await expect(page.locator('.drawer.open')).toContainText('--port')
    expect(puts).toBe(0)

    // Clearing the offending flag re-enables the write path.
    await page.getByTestId('model-flags-input').fill('--cache-type-k q8_0')
    await expect(page.getByTestId('model-save')).toBeEnabled()
  })

  test('E6 — an unbalanced quote in flags blocks Save too', async ({ page }) => {
    await stubLookups(page)
    let puts = 0
    await page.route(`**/api/models/${MODEL_ID}`, async (route) => {
      if (route.request().method() === 'PUT') {
        puts += 1
        return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
      }
      return route.fallback()
    })

    await openDrawer(page)
    await page.getByTestId('model-flags-input').fill('--chat-template "broken')

    await expect(page.getByTestId('model-save')).toBeDisabled()
    expect(puts).toBe(0)
  })
})
