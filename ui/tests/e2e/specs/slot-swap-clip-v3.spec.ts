/**
 * slot-swap-clip-v3 — the swap dropdown must not be clipped by the card.
 *
 * The .slot card has overflow:hidden (rounded corners), which painted-clips
 * the absolutely-positioned .swap-pop to the card's bottom edge — hiding the
 * lower models (e.g. qwen3.6-35b) and their internal scroll. When the
 * dropdown is open the card must stop clipping.
 */
import { test, expect, type Page } from '../fixtures/apiMock'

const cardByName = (page: Page, name: string) =>
  page
    .locator('.slot', { has: page.locator('.slot-name .nm', { hasText: new RegExp(`^${name}$`) }) })
    .first()

test.describe('Slot swap dropdown — not clipped by card', () => {
  test('card stops clipping (overflow-y visible) while the swap dropdown is open', async ({ page }) => {
    await page.goto('/#slots')
    const card = cardByName(page, 'primary')

    // Closed: card clips its content (rounded-corner aesthetic).
    expect(await card.evaluate((el) => getComputedStyle(el).overflowY)).toBe('hidden')

    await card.locator('.slot-model').click()
    await expect(page.locator('.swap-pop')).toBeVisible()

    // Open: card must not clip the dropdown.
    expect(await card.evaluate((el) => getComputedStyle(el).overflowY)).toBe('visible')
  })
})
