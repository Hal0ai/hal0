import { test, expect, type Page } from '../fixtures/apiMock'

async function seedSlots(page: Page, slots: any[]) {
  await page.addInitScript((slots) => {
    let real: any
    Object.defineProperty(window, 'HAL0_DATA', {
      configurable: true, get() { return real },
      set(v) { real = v; if (v && typeof v === 'object') v.slots = slots },
    })
  }, slots)
}

const A = { name: 'primary', type: 'llm', device: 'gpu-rocm', state: 'serving', port: 8092, isDefault: true,  enabled: true }
const B = { name: 'backup',  type: 'llm', device: 'gpu-rocm', state: 'ready',   port: 8093, isDefault: false, enabled: true }

test('Default slots pane sets the chosen slot default and clears the prior one', async ({ page }) => {
  const puts: Record<string, any[]> = { primary: [], backup: [] }
  for (const n of ['primary', 'backup']) {
    await page.route(`**/api/slots/${n}/config`, async (route) => {
      if (route.request().method() === 'PUT') puts[n].push(JSON.parse(route.request().postData() || '{}'))
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
    })
  }
  await seedSlots(page, [A, B])
  await page.goto('/#settings/defaults')
  const row = page.locator('.default-slot-row', { hasText: 'llm' })
  await expect(row).toBeVisible()
  await row.locator('select').selectOption('backup')
  await expect.poll(() => puts.backup.length).toBeGreaterThan(0)
  expect(puts.backup[0].default).toBe(true)
  await expect.poll(() => puts.primary.length).toBeGreaterThan(0)
  expect(puts.primary[0].default).toBe(false)
})
