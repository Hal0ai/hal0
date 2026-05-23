/**
 * backends-v3 — `#backends` route renders the lemonade hero card +
 * backends table with one row per backend (name / version / state /
 * used by).
 */
import { test, expect } from '../fixtures/apiMock'

test.describe('Backends v3 (/backends)', () => {
  test('renders backends table + section header with count', async ({ page }) => {
    await page.goto('/#backends')
    await expect(page.locator('.view .vh h1')).toHaveText('Backends')
    // sec h2 with count chip
    await expect(page.locator('.view .sec h2', { hasText: 'Backends' })).toBeVisible()
    // lemonade hero card visible
    await expect(page.locator('.view .card').first()).toBeVisible()
  })

  test('column headers (backend / version / state / used by / actions) render', async ({ page }) => {
    await page.goto('/#backends')
    const card = page.locator('.view .card').nth(1) // 2nd card is the table
    await expect(card).toContainText('backend')
    await expect(card).toContainText('version')
    await expect(card).toContainText('state')
    await expect(card).toContainText('used by')
    await expect(card).toContainText('actions')
  })
})
