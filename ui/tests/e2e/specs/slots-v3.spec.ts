/**
 * slots-v3 — `#slots` route renders grouped sections (Chat / Embed /
 * Voice / Image / NPU rollup) + slot cards + the "New slot" CTA.
 */
import { test, expect } from '../fixtures/apiMock'

test.describe('Slots v3 (/slots)', () => {
  test('renders grouped sections + slot cards', async ({ page }) => {
    await page.goto('/#slots')
    await expect(page.locator('.view .vh h1')).toHaveText('Slots')
    // at least one group section h2 (chat/embed/voice) renders
    await expect(page.locator('.view .sec h2').first()).toBeVisible()
    // slot cards or list rows present
    const cards = page.locator('.slots-grid > *, .slots-list > *')
    expect(await cards.count()).toBeGreaterThan(0)
  })

  test('exposes New-slot button (create modal trigger)', async ({ page }) => {
    await page.goto('/#slots')
    const newBtn = page.locator('.view .vh button:has-text("New slot")')
    await expect(newBtn).toBeVisible()
  })

  test('NPU rollup section renders when an NPU slot is present', async ({ page }) => {
    await page.goto('/#slots')
    // HAL0_DATA seeds at least one device=npu slot, so the NPU section h2 should appear
    await expect(page.locator('.view .sec h2', { hasText: 'NPU' })).toBeVisible()
  })
})
