/**
 * slot-edit-controls-v3 — Spec 1 (slot edit panel controls).
 *
 * Covers the operator controls added to the slots page:
 *   C3. enabled toggle on the slot CARD → PUT /config { enabled } + fade.
 *   C4. enable_thinking toggle in the edit DRAWER (llm slots only) →
 *       PUT /config { enable_thinking } instantly.
 *   C5. n_gpu_layers in the drawer Advanced section is READ-ONLY —
 *       profile-owned; Save ships ctx_size via PATCH /defaults instead.
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
  test('C4 — drawer reasoning pill PUTs /config { enable_thinking:true }', async ({ page }) => {
    const puts: any[] = []
    await page.route('**/api/slots/primary/config', async (route) => {
      if (route.request().method() === 'PUT') {
        puts.push(JSON.parse(route.request().postData() || '{}'))
      }
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
    })
    await seedSlots(page, [PRIMARY, EMBED])

    await page.goto('/#slots/primary')
    const row = page.locator('.drawer .form-row', { hasText: 'Reasoning' })
    await expect(row).toBeVisible()
    await row.locator('button[role="switch"]').click()
    await expect.poll(() => puts.length).toBeGreaterThan(0)
    expect(puts[0].enable_thinking).toBe(true)
  })

  test('C4 — reasoning pill is hidden for non-llm slots', async ({ page }) => {
    await seedSlots(page, [PRIMARY, EMBED])
    await page.goto('/#slots/embed')
    await expect(page.locator('.drawer')).toBeVisible()
    await expect(page.locator('.drawer .form-row', { hasText: 'Reasoning' })).toHaveCount(0)
  })

  test('C5 — n_gpu_layers is read-only, owned by the profile', async ({ page }) => {
    await seedSlots(page, [PRIMARY, EMBED])

    await page.goto('/#slots/primary')
    // The Advanced section is collapsed by default — open the disclosure.
    await page.locator('.drawer details.adv-disclosure summary').click()
    const row = page.locator('.drawer .form-row', { hasText: 'n_gpu_layers' })
    await expect(row).toBeVisible()
    await expect(row.locator('.form-lbl .sub')).toContainText('defined by profile')
    await expect(row.locator('input')).toHaveAttribute('readonly', '')
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
    // ctx_size is now in the Model group (directly visible, not inside Advanced).
    const row = page.locator('.drawer .form-row', { hasText: 'ctx_size' })
    await expect(row).toBeVisible()
    await row.locator('input').fill('16384')
    await page.locator('.drawer button:has-text("Save")').click()
    await expect.poll(() => patches.length).toBeGreaterThan(0)
    expect(patches[0].ctx_size).toBe(16384)
    // Profile-owned knobs never ride the defaults PATCH.
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
    await expect.poll(() => puts.length).toBeGreaterThan(0)
    const body = puts[0]
    expect(body).not.toHaveProperty('idle_timeout_s')
    expect(body).not.toHaveProperty('workers')
    expect(body).not.toHaveProperty('llamacpp_args')
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

  test('C4 — reasoning pill toggles enable_thinking and keeps a fixed label', async ({ page }) => {
    const puts: any[] = []
    await page.route('**/api/slots/primary/config', async (route) => {
      if (route.request().method() === 'PUT') puts.push(JSON.parse(route.request().postData() || '{}'))
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
    })
    await seedSlots(page, [PRIMARY, EMBED])
    await page.goto('/#slots/primary')

    const row = page.locator('.drawer .form-row', { hasText: 'Reasoning' })
    await expect(row).toBeVisible()
    await expect(row.locator('.form-lbl span').first()).toHaveText('Reasoning')
    const pill = row.locator('button[role="switch"]')
    await expect(pill).toHaveAttribute('aria-checked', 'false')

    await pill.click()
    await expect.poll(() => puts.length).toBeGreaterThan(0)
    expect(puts[0].enable_thinking).toBe(true)
    await expect(pill).toHaveAttribute('aria-checked', 'true')
  })

  test('drawer fields are grouped under SLOT / MODEL / INFERENCE', async ({ page }) => {
    await seedSlots(page, [PRIMARY, EMBED])
    await page.goto('/#slots/primary')
    await expect(page.locator('.drawer')).toBeVisible()
    for (const label of ['Slot', 'Model', 'Inference']) {
      await expect(page.locator('.field-group-label', { hasText: new RegExp(`^${label}$`, 'i') })).toHaveCount(1)
    }
    const modelGroup = page.locator('.field-group', { has: page.locator('.field-group-label', { hasText: /^Model$/i }) })
    await expect(modelGroup.locator('.form-row', { hasText: 'Model' }).locator('select')).toBeVisible()
    const infGroup = page.locator('.field-group', { has: page.locator('.field-group-label', { hasText: /^Inference$/i }) })
    await expect(infGroup.locator('.form-row', { hasText: 'Reasoning' })).toBeVisible()
  })

  // Editable per-slot extra_args overlay (one-off flag tests without a new
  // profile). The field seeds from the on-disk [server].extra_args (wire key
  // `llamacpp_args`), is editable, and persists nested under `server` so the
  // backend one-level merge keeps sibling server keys.
  const PRIMARY_WITH_ARGS = {
    ...PRIMARY,
    llamacpp_args: '--threads 6',
    resolved_command: ['img', '--host', '0.0.0.0', '--port', '8092', '--threads', '6'],
  }

  test('extra_args is editable and labelled as a per-slot override', async ({ page }) => {
    await seedSlots(page, [PRIMARY_WITH_ARGS, EMBED])
    await page.goto('/#slots/primary')
    await page.locator('.drawer details.adv-disclosure summary').click()
    const row = page.locator('.drawer .form-row', { hasText: 'extra_args' })
    await expect(row).toBeVisible()
    await expect(row.locator('.form-lbl .sub')).toContainText('per-slot override')
    const input = page.getByTestId('extra-args-input')
    await expect(input).not.toHaveAttribute('readonly', '')
    await expect(input).toHaveValue('--threads 6')
  })

  test('editing extra_args dims the resolved command and Regenerate PUTs { server: { extra_args } }', async ({ page }) => {
    const puts: any[] = []
    await page.route('**/api/slots/primary/config', async (route) => {
      if (route.request().method() === 'PUT') puts.push(JSON.parse(route.request().postData() || '{}'))
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
    })
    await seedSlots(page, [PRIMARY_WITH_ARGS, EMBED])
    await page.goto('/#slots/primary')
    await page.locator('.drawer details.adv-disclosure summary').click()

    // No overlay until the field is dirty.
    await expect(page.getByTestId('resolved-stale-overlay')).toHaveCount(0)
    await page.getByTestId('extra-args-input').fill('--threads 6 -fa off')
    await expect(page.getByTestId('resolved-stale-overlay')).toBeVisible()

    await page.getByTestId('regenerate-resolved').click()
    await expect.poll(() => puts.length).toBeGreaterThan(0)
    expect(puts[0]).toEqual({ server: { extra_args: '--threads 6 -fa off' } })
  })

  test('malformed extra_args (unbalanced quote) blocks Regenerate', async ({ page }) => {
    await seedSlots(page, [PRIMARY_WITH_ARGS, EMBED])
    await page.goto('/#slots/primary')
    await page.locator('.drawer details.adv-disclosure summary').click()
    await page.getByTestId('extra-args-input').fill('--chat-template "oops')
    await expect(page.getByTestId('regenerate-resolved')).toBeDisabled()
  })

  test('Save ships changed extra_args nested under server', async ({ page }) => {
    const puts: any[] = []
    await page.route('**/api/slots/primary/config', async (route) => {
      if (route.request().method() === 'PUT') puts.push(JSON.parse(route.request().postData() || '{}'))
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
    })
    await page.route('**/api/slots/primary/defaults', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: '{}' }))
    await seedSlots(page, [PRIMARY_WITH_ARGS, EMBED])
    await page.goto('/#slots/primary')
    await page.locator('.drawer details.adv-disclosure summary').click()
    await page.getByTestId('extra-args-input').fill('--threads 12')
    await page.locator('.drawer button:has-text("Save")').click()
    await expect.poll(() => puts.length).toBeGreaterThan(0)
    expect(puts[0].server).toEqual({ extra_args: '--threads 12' })
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
    await page.locator('.drawer button:has-text("Save")').click()
    await expect.poll(() => puts.length).toBeGreaterThan(0)
    expect(puts[0]).not.toHaveProperty('default')
  })
})
