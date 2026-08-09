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

const A = { name: 'primary', type: 'llm', device: 'gpu-rocm', state: 'serving', port: 8092, isDefault: true }
const B = { name: 'backup',  type: 'llm', device: 'gpu-rocm', state: 'ready',   port: 8093, isDefault: false }

test('Default slots pane sets the chosen slot default and clears the prior one', async ({ page }) => {
  const puts: Record<string, any[]> = { primary: [], backup: [] }
  for (const n of ['primary', 'backup']) {
    await page.route(`**/api/slots/${n}/config`, async (route) => {
      if (route.request().method() === 'PUT') puts[n].push(JSON.parse(route.request().postData() || '{}'))
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
    })
  }
  await seedSlots(page, [A, B])
  await page.goto('/#settings/slots')
  const row = page.locator('.default-slot-row', { hasText: 'llm' })
  await expect(row).toBeVisible()
  await row.locator('select').selectOption('backup')
  await expect.poll(() => puts.backup.length).toBeGreaterThan(0)
  expect(puts.backup[0].default).toBe(true)
  await expect.poll(() => puts.primary.length).toBeGreaterThan(0)
  expect(puts.primary[0].default).toBe(false)
})

// The modality rows carried `form-row` alongside `s-row`; `.form-row` is
// declared later in dashboard.css with `padding: 12px 0`, so it stripped the
// panel's 18px horizontal padding and left "llm" hanging outside the panel
// while every sibling row stayed indented.
test('modality rows share the panel indent with the other settings rows', async ({ page }) => {
  await seedSlots(page, [A, B])
  await page.goto('/#settings/slots')
  const modality = page.locator('.default-slot-row .k').first()
  await expect(modality).toBeVisible()

  const header = await page.locator('.s-panel .s-row .k', { hasText: 'Default slots' }).first().boundingBox()
  const row = await modality.boundingBox()
  expect(Math.round(row!.x)).toBe(Math.round(header!.x))
})

// AdvRow used to slice descriptions at 150 chars, which silently dropped the
// tail of deliberately long copy — slots.publish_host loses its "only widen
// this on a trusted network" warning. The popup wraps and caps its own width,
// so there is nothing for the truncation to protect.
test('settings info popups carry the whole description, untruncated', async ({ page }) => {
  await seedSlots(page, [A, B])
  await page.goto('/#settings/slots')
  const row = page.locator('.s-row', { has: page.locator('.k > span', { hasText: 'publish_host' }) })
  await row.locator('.field-info-btn').hover()

  const pop = page.locator('.field-info-pop[data-open="1"]')
  await expect(pop).toBeVisible()
  await expect(pop).toContainText('only widen this on a trusted network')
  await expect(pop).not.toContainText('…')
})
