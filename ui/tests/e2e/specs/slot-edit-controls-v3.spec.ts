/**
 * slot-edit-controls-v3 — Spec 1 (slot edit panel controls).
 *
 * Covers the operator controls added to the slots page:
 *   C3. enabled toggle on the slot CARD → PUT /config { enabled } + fade.
 *   C4. enable_thinking toggle in the edit DRAWER (llm slots only) →
 *       PUT /config { enable_thinking } instantly.
 *   C5. n_gpu_layers input in the drawer Advanced section → Save →
 *       PATCH /defaults { n_gpu_layers }.
 *   C6. enabled slots sort before disabled ones in the grid.
 *
 * The dashboard renders the slot LIST from in-bundle HAL0_DATA
 * (VITE_MOCK_LEMONADE=1 short-circuits GET /api/slots before page.route
 * sees it — see src/api/mock.ts). So we control the list by intercepting
 * the `window.HAL0_DATA` assignment via addInitScript (`seedSlots`).
 * Mutations to /config + /defaults are NOT allowlisted, so they fall
 * through to real fetch and page.route captures their bodies.
 */
import { test, expect, type Page } from '../fixtures/apiMock'

const PRIMARY = {
  name: 'primary', type: 'llm', device: 'gpu-rocm',
  model: 'qwen3.6-27b', model_id: 'qwen3.6-27b', modelLong: 'qwen3.6-27b',
  group: 'chat', state: 'serving', port: 8092, isDefault: true,
  enabled: true, enable_thinking: false, n_gpu_layers: -1,
  metrics: { ctx: 8192, toks: 42, ttft: 180, kv: 35 },
}
const CODER = {
  name: 'coder', type: 'llm', device: 'gpu-rocm',
  model: 'qwen3-coder-30b', model_id: 'qwen3-coder-30b', modelLong: 'qwen3-coder-30b',
  group: 'chat', state: 'idle', port: 8094,
  enabled: false, enable_thinking: null, n_gpu_layers: 20,
  metrics: { ctx: 4096 },
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

const card = (page: Page, name: string) =>
  page
    .locator('.slot', { has: page.locator('.slot-name .nm', { hasText: new RegExp(`^${name}$`) }) })
    .first()

test.describe('Slot edit controls (/slots)', () => {
  test('C3 — card enabled toggle PUTs /config { enabled:false }', async ({ page }) => {
    const puts: any[] = []
    await page.route('**/api/slots/primary/config', async (route) => {
      if (route.request().method() === 'PUT') {
        puts.push(JSON.parse(route.request().postData() || '{}'))
      }
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
    })
    await seedSlots(page, [PRIMARY, EMBED])

    await page.goto('/#slots')
    const toggle = card(page, 'primary').locator('.slot-enable-toggle')
    await expect(toggle).toBeVisible()
    await toggle.click()
    await expect.poll(() => puts.length).toBeGreaterThan(0)
    expect(puts[0].enabled).toBe(false)
  })

  test('C3 — disabled slot card carries the fade modifier class', async ({ page }) => {
    await seedSlots(page, [PRIMARY, CODER])
    await page.goto('/#slots')
    await expect(card(page, 'coder')).toHaveClass(/slot--disabled/)
    await expect(card(page, 'primary')).not.toHaveClass(/slot--disabled/)
  })

  test('C4 — drawer thinking toggle PUTs /config { enable_thinking:true }', async ({ page }) => {
    const puts: any[] = []
    await page.route('**/api/slots/primary/config', async (route) => {
      if (route.request().method() === 'PUT') {
        puts.push(JSON.parse(route.request().postData() || '{}'))
      }
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
    })
    await seedSlots(page, [PRIMARY, EMBED])

    await page.goto('/#slots/primary')
    const row = page.locator('.drawer .form-row', { hasText: 'Thinking' })
    await expect(row).toBeVisible()
    await row.locator('input[type="checkbox"]').click()
    await expect.poll(() => puts.length).toBeGreaterThan(0)
    expect(puts[0].enable_thinking).toBe(true)
  })

  test('C4 — thinking toggle is hidden for non-llm slots', async ({ page }) => {
    await seedSlots(page, [PRIMARY, EMBED])
    await page.goto('/#slots/embed')
    await expect(page.locator('.drawer')).toBeVisible()
    await expect(page.locator('.drawer .form-row', { hasText: 'Thinking' })).toHaveCount(0)
  })

  test('C5 — n_gpu_layers Save PATCHes /defaults', async ({ page }) => {
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
    const row = page.locator('.drawer .form-row', { hasText: 'n_gpu_layers' })
    await expect(row).toBeVisible()
    await row.locator('input').fill('33')
    await page.locator('.drawer button:has-text("Save")').click()
    await expect.poll(() => patches.length).toBeGreaterThan(0)
    expect(patches[0].n_gpu_layers).toBe(33)
  })

  test('C6 — enabled slots sort before disabled ones', async ({ page }) => {
    // Disabled-first input order: only a real enabled-first sort makes
    // primary (enabled) render ahead of coder (disabled).
    await seedSlots(page, [CODER, PRIMARY, EMBED])
    await page.goto('/#slots')
    const chatCards = page.locator('section', { hasText: 'Chat' }).locator('.slot')
    await expect(chatCards.first()).toContainText('primary')
    await expect(chatCards.nth(1)).toContainText('coder')
  })
})
