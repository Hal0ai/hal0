/**
 * slot-pin-toggle-v3 — the drawer header lifecycle toggles (#1367 + spec
 * 2026-08-02 consolidation).
 *
 * §21.10 operator pin replaced the old Enabled/Disabled header toggle; the
 * lifecycle consolidation then moved Auto-Load up next to it and re-homed
 * Eviction priority under the Advanced disclosure:
 *   P1. header renders BOTH toggles — "Auto-Load" and "Pinned"/"Unpinned"
 *       (from `slot.pinned`); the old Enabled/Disabled copy is gone.
 *   P2. flipping the pin toggle fires PUT /config { pinned } — never
 *       { enabled }.
 *   P3. a pinned slot (fresh-install utility anchor) seeds the toggle on.
 *   P4. flipping the header Auto-Load toggle fires PUT /config { autoload }.
 *   P5. Eviction priority lives under the Advanced disclosure (not in the
 *       Model section) and commits PUT /config { priority } on blur.
 *
 * List seeding mirrors slot-edit-controls-v3.spec.ts (in-bundle HAL0_DATA,
 * mutations fall through to page.route).
 */
import { test, expect, type Page } from '../fixtures/apiMock'

const PRIMARY = {
  name: 'primary', type: 'llm', device: 'gpu-rocm', profile: 'rocm',
  model: 'qwen3.6-27b', model_id: 'qwen3.6-27b', modelLong: 'qwen3.6-27b',
  group: 'chat', state: 'serving', port: 8092, isDefault: true,
  pinned: false, n_gpu_layers: -1,
  metrics: { ctx: 8192, toks: 42, ttft: 180, kv: 35 },
}
const UTILITY = {
  name: 'utility', type: 'llm', device: 'gpu-rocm', profile: 'rocm',
  model: 'qwen3-4b', model_id: 'qwen3-4b', modelLong: 'qwen3-4b',
  group: 'chat', state: 'ready', port: 8090, isDefault: false,
  pinned: true, n_gpu_layers: -1,
  metrics: {},
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

test.describe('Slot pin toggle (/slots drawer header)', () => {
  test('P1 — header shows Auto-Load + Pinned/Unpinned, Enabled/Disabled copy is gone', async ({ page }) => {
    await seedSlots(page, [PRIMARY, UTILITY])
    await page.goto('/#slots/primary')
    await expect(page.locator('.drawer')).toBeVisible()
    await expect(
      page.locator('[data-testid="slot-pin-toggle"] .drawer-enable-label'),
    ).toHaveText('Unpinned')
    await expect(
      page.locator('[data-testid="slot-autoload-toggle"] .drawer-enable-label'),
    ).toHaveText('Auto-Load')
    await expect(page.locator('.drawer', { hasText: /Enabled|Disabled/ })).toHaveCount(0)
  })

  test('P2 — flipping the pin toggle PUTs { pinned }, never { enabled }', async ({ page }) => {
    const puts: any[] = []
    await page.route('**/api/slots/primary/config', async (route) => {
      if (route.request().method() === 'PUT')
        puts.push(JSON.parse(route.request().postData() || '{}'))
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
    })
    await seedSlots(page, [PRIMARY, UTILITY])
    await page.goto('/#slots/primary')
    await expect(page.locator('.drawer')).toBeVisible()
    // The input is visually hidden behind the styled track — click the label.
    await page.locator('label[data-testid="slot-pin-toggle"]').click()
    await expect.poll(() => puts.length).toBeGreaterThan(0)
    expect(puts[0]).toEqual({ pinned: true })
    expect(puts[0]).not.toHaveProperty('enabled')
  })

  test('P3 — a pinned anchor seeds the toggle on', async ({ page }) => {
    await seedSlots(page, [PRIMARY, UTILITY])
    await page.goto('/#slots/utility')
    await expect(page.locator('.drawer')).toBeVisible()
    await expect(
      page.locator('[data-testid="slot-pin-toggle"] .drawer-enable-label'),
    ).toHaveText('Pinned')
    await expect(
      page.locator('[data-testid="slot-pin-toggle"] input[type="checkbox"]'),
    ).toBeChecked()
  })

  test('P4 — flipping the header Auto-Load toggle PUTs { autoload }', async ({ page }) => {
    const puts: any[] = []
    await page.route('**/api/slots/primary/config', async (route) => {
      if (route.request().method() === 'PUT')
        puts.push(JSON.parse(route.request().postData() || '{}'))
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
    })
    await seedSlots(page, [PRIMARY, UTILITY])
    await page.goto('/#slots/primary')
    await expect(page.locator('.drawer')).toBeVisible()
    await page.locator('label[data-testid="slot-autoload-toggle"]').click()
    await expect.poll(() => puts.length).toBeGreaterThan(0)
    expect(puts[0]).toEqual({ autoload: true })
  })

  test('P5 — Eviction priority lives under Advanced and PUTs { priority } on blur', async ({ page }) => {
    const puts: any[] = []
    await page.route('**/api/slots/primary/config', async (route) => {
      if (route.request().method() === 'PUT')
        puts.push(JSON.parse(route.request().postData() || '{}'))
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
    })
    await seedSlots(page, [PRIMARY, UTILITY])
    await page.goto('/#slots/primary')
    await expect(page.locator('.drawer')).toBeVisible()
    const prio = page.locator('[data-testid="slot-priority-input"]')
    // Collapsed by default — the row sits inside the Advanced disclosure.
    await expect(prio).toBeHidden()
    await page.locator('details.adv-disclosure > summary').click()
    await expect(prio).toBeVisible()
    await prio.fill('10')
    await prio.blur()
    await expect.poll(() => puts.length).toBeGreaterThan(0)
    expect(puts[0]).toEqual({ priority: 10 })
  })
})
