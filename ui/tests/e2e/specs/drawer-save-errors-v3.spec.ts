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
 *   4. a failed config write never restarts the slot (ADR-0029: Save is a
 *      single PUT /config — there is no PATCH /defaults leg any more — and the
 *      restart is only fired after that write lands),
 *   5. an instant-apply toggle that the server rejects REVERTS to server truth
 *      instead of lying about the new state.
 *
 * Realistic envelopes are used throughout — `validation.unknown_keys` from
 * `_reject_unknown_config_keys`, and the NPU-exclusivity 409 the slot manager
 * raises when a second device=npu llm anchor is enabled.
 */
import { test, expect, type Page } from '../fixtures/apiMock'
import { pickRichOption } from '../fixtures/richSelect'

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
    await seedSlots(page, [PRIMARY])

    await page.goto('/#slots/primary')
    // NGL lives behind the Advanced disclosure now (Task 11a).
    await page.getByTestId('slot-hw-advanced').click()
    await page.getByTestId('slot-hw-ngl').fill('24')
    await drawer(page).locator('button:has-text("Save")').click()

    // The envelope message is surfaced verbatim next to the Save button…
    await expect(drawer(page)).toContainText('unknown slot config key(s): n_gpu_layerss')
    // …the drawer stays open…
    await expect(drawer(page)).toBeVisible()
    // …and the operator's edit is still there to correct or retry.
    await expect(page.getByTestId('slot-hw-ngl')).toHaveValue('24')
  })

  test('E2 — a failed /config PUT never restarts the slot', async ({ page }) => {
    // Save writes the placement keys in one PUT /config and only THEN fires the
    // background restart. If the write fails the restart must not run, or the
    // slot cold-restarts onto a config that never landed.
    const restarts: string[] = []
    await page.route('**/api/slots/primary/config', (route) =>
      route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: envelope('slot.config_write_failed', 'failed to rewrite primary.toml'),
      }),
    )
    await page.route('**/api/slots/primary/restart', async (route) => {
      restarts.push(route.request().url())
      await route.fulfill({ status: 202, contentType: 'application/json', body: '{}' })
    })
    await seedSlots(page, [PRIMARY])

    await page.goto('/#slots/primary')
    // NGL is behind the Advanced disclosure; it is a hardware key, so a
    // successful write WOULD restart.
    await page.getByTestId('slot-hw-advanced').click()
    await page.getByTestId('slot-hw-ngl').fill('24')
    await drawer(page).locator('button:has-text("Save")').click()

    await expect(drawer(page)).toContainText('failed to rewrite primary.toml')
    await expect(drawer(page)).toBeVisible()
    await page.waitForTimeout(250)
    expect(restarts).toEqual([])
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
    await seedSlots(page, [
      { ...PRIMARY, state: 'offline', runtime: 'container', container_status: 'stopped', container_health: false },
    ])

    await page.goto('/#slots/primary')
    await pickRichOption(drawer(page).getByLabel('Model for primary'), 'qwen3-coder-30b')

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
    await expect(page.getByTestId('model-chat-template')).toBeVisible()
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
    // model-drawer-2 Task 3: name edits ride the inline title editor now
    // (✎ → model-title-input, Enter commits), not a "Display name" form row.
    await page.getByTestId('model-title-edit').click()
    await page.getByTestId('model-title-input').fill('Renamed while failing')
    await page.getByTestId('model-title-input').press('Enter')
    // A second, unrelated edit (a file fact): jinja marked unsupported.
    await page.getByTestId('model-cap-override-add').click()
    await page.getByTestId('model-cap-override-add-jinja-off').click()
    await page.getByTestId('model-save').click()

    await expect.poll(() => puts).toBe(1)
    // Envelope message surfaced verbatim inside the drawer…
    await expect(page.locator('.drawer.open')).toContainText(
      'model qwen3.6-27b-mtp is bound to a running slot — stop it first',
    )
    // …drawer stays open with BOTH edits still in the fields.
    await expect(page.locator('.drawer.open')).toHaveCount(1)
    await expect(
      page.locator('.drawer.open h2 > span > span').first(),
    ).toHaveText('Renamed while failing')
    await expect(page.getByTestId('model-cap-override-jinja')).toContainText('off')
  })

  // E6 (a hal0-managed flag / an unbalanced quote blocks Save client-side) lived
  // here while the model drawer carried a flag editor. ADR-0029 moved the flags
  // onto the profile; the same inline screens (managed, hardware, model-fact
  // flags, unbalanced quote, context range) are pinned in profiles-crud-v3.spec.ts
  // and the profiles vitest suite.
})
