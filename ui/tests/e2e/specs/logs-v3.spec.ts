/**
 * logs-v3 — `#logs` route renders the filter bar (channel / level / slot
 * / search), the follow-tail indicator, and the Pause/Resume control.
 */
import { test, expect } from '../fixtures/apiMock'

test.describe('Logs v3 (/logs)', () => {
  test('renders Logs view + channel selector', async ({ page }) => {
    await page.goto('/#logs')
    await expect(page.locator('.view .vh h1')).toHaveText('Logs')
    // channel selector (events | slot | merged) — replaces the old dead
    // merged/hal0 toggle where both positions showed the same data.
    await expect(page.locator('.view button', { hasText: 'events' })).toBeVisible()
    await expect(page.locator('.view button', { hasText: 'slot' })).toBeVisible()
    await expect(page.locator('.view button', { hasText: 'merged' })).toBeVisible()
  })

  test('slot channel prompts for a slot selection', async ({ page }) => {
    await page.goto('/#logs')
    await page.locator('.view button', { hasText: 'slot' }).click()
    // With no slot picked the page guides the user instead of showing nothing.
    await expect(page.locator('.view')).toContainText('Select a slot')
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
