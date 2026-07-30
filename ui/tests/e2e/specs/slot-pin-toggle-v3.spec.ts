/**
 * slot-pin-toggle-v3 — the drawer header toggle is Pinned/Unpinned (#1367).
 *
 * §21.10 operator pin replaces the old Enabled/Disabled header toggle:
 *   P1. header toggle renders "Pinned"/"Unpinned" from `slot.pinned`;
 *       the old Enabled/Disabled copy is gone from the drawer.
 *   P2. flipping the toggle fires PUT /config { pinned } — never { enabled }.
 *   P3. a pinned slot (fresh-install utility anchor) seeds the toggle on.
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
  test('P1 — header toggle reads Pinned/Unpinned, Enabled/Disabled copy is gone', async ({ page }) => {
    await seedSlots(page, [PRIMARY, UTILITY])
    await page.goto('/#slots/primary')
    await expect(page.locator('.drawer')).toBeVisible()
    await expect(page.locator('.drawer-enable-label')).toHaveText('Unpinned')
    await expect(page.locator('.drawer', { hasText: /Enabled|Disabled/ })).toHaveCount(0)
  })

  test('P2 — flipping the toggle PUTs { pinned }, never { enabled }', async ({ page }) => {
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
    await page.locator('label.drawer-enable').click()
    await expect.poll(() => puts.length).toBeGreaterThan(0)
    expect(puts[0]).toEqual({ pinned: true })
    expect(puts[0]).not.toHaveProperty('enabled')
  })

  test('P3 — a pinned anchor seeds the toggle on', async ({ page }) => {
    await seedSlots(page, [PRIMARY, UTILITY])
    await page.goto('/#slots/utility')
    await expect(page.locator('.drawer')).toBeVisible()
    await expect(page.locator('.drawer-enable-label')).toHaveText('Pinned')
    await expect(page.locator('.drawer-enable input[type="checkbox"]')).toBeChecked()
  })
})
