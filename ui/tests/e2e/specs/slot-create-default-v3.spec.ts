/**
 * slot-create-default-v3 — the create-slot "Set as default" checkbox now works.
 *
 * Checking "Set as default" sends `default: true` in the POST /api/slots body;
 * the backend promotes the slot's MODEL as its type's default (verified in
 * tests/api/test_models_default.py). Here we assert the wire contract: the
 * checkbox drives the payload flag, and leaving it unchecked omits it.
 */
import { test, expect } from '../fixtures/apiMock'

async function openCreateModal(page: import('@playwright/test').Page) {
  await page.goto('/#slots')
  await page.locator('.view .vh button:has-text("New slot")').click()
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
