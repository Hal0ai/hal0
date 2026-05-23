/**
 * models-v3 — `#models` route renders the 3-pane layout (filters / list
 * / detail) and exposes the "Add by HF coords" trigger.
 */
import { test, expect } from '../fixtures/apiMock'

test.describe('Models v3 (/models)', () => {
  test('renders 3-pane catalog layout', async ({ page }) => {
    await page.goto('/#models')
    await expect(page.locator('.view .vh h1')).toHaveText('Models')
    await expect(page.locator('.models-layout')).toBeVisible()
    await expect(page.locator('.mdl-filters')).toBeVisible()
    await expect(page.locator('.mdl-list')).toBeVisible()
  })

  test('exposes Add-by-HF + Search-HF CTAs', async ({ page }) => {
    await page.goto('/#models')
    await expect(page.locator('.view .vh button:has-text("Add by HF coords")')).toBeVisible()
    await expect(page.locator('.view .vh button:has-text("Search HF")')).toBeVisible()
  })

  test('filter chips for type/device/namespace are clickable', async ({ page }) => {
    await page.goto('/#models')
    const llmChip = page.locator('.mdl-filter-chips button.mdl-chip', { hasText: 'llm' }).first()
    await expect(llmChip).toBeVisible()
    await llmChip.click()
    await expect(llmChip).toHaveClass(/on/)
  })
})
