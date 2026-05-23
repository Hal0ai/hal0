/**
 * logs-v3 — `#logs` route renders the filter bar (source / level / slot
 * / search), the follow-tail indicator, and the Pause/Resume control.
 */
import { test, expect } from '../fixtures/apiMock'

test.describe('Logs v3 (/logs)', () => {
  test('renders Logs view + filter bar', async ({ page }) => {
    await page.goto('/#logs')
    await expect(page.locator('.view .vh h1')).toHaveText('Logs')
    // source toggle (merged/hal0/lemond)
    await expect(page.locator('.view button', { hasText: 'merged' })).toBeVisible()
    await expect(page.locator('.view button', { hasText: 'hal0' })).toBeVisible()
    await expect(page.locator('.view button', { hasText: 'lemond' })).toBeVisible()
  })

  test('search input + slot select + pause button render', async ({ page }) => {
    await page.goto('/#logs')
    await expect(page.locator('.view input[placeholder="search…"]')).toBeVisible()
    await expect(page.locator('.view select').first()).toBeVisible()
    await expect(page.locator('.view button', { hasText: 'Pause' })).toBeVisible()
  })

  test('eyebrow shows Runtime + lines hint visible', async ({ page }) => {
    await page.goto('/#logs')
    await expect(page.locator('.view .vh .vh-eye')).toHaveText('Runtime')
    await expect(page.locator('.view .vh .hint')).toContainText('lines')
  })
})
