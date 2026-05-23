/**
 * agent-v3 — `#agent` route renders the tabbed view (overview / inbox /
 * skills / memory / personas) and the bundled-agent overview card by
 * default.
 */
import { test, expect } from '../fixtures/apiMock'

const TABS = ['overview', 'inbox', 'skills', 'memory', 'personas']

test.describe('Agent v3 (/agent)', () => {
  test.skip('renders Agent view + all 5 tabs', async ({ page }) => {
    await page.goto('/#agent')
    await expect(page.locator('.view .vh h1')).toHaveText('Agent')
    for (const tab of TABS) {
      await expect(page.locator('.view button', { hasText: new RegExp(`^${tab}$`, 'i') })).toBeVisible()
    }
  })

  test.skip('default tab is overview — bundled-agent card visible', async ({ page }) => {
    await page.goto('/#agent')
    await expect(page.locator('.view .sec h2', { hasText: 'Bundled agent' })).toBeVisible()
  })

  test.skip('clicking inbox tab swaps content', async ({ page }) => {
    await page.goto('/#agent')
    await page.locator('.view button', { hasText: /^inbox$/i }).click()
    // inbox state shows either pending approvals list or empty state
    const view = page.locator('.view')
    await expect(view).toContainText(/No pending approvals|approval/i)
  })
})
