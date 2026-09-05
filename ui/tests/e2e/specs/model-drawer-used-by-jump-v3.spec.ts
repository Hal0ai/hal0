/**
 * model-drawer-used-by-jump-v3 — the model drawer's facts-band "Used by"
 * cell (model-drawer.jsx `onOpenSlot`) is wired from the standalone Models
 * page (models.jsx): clicking a used-by slot name closes the drawer and
 * lands on `#slots/{name}` — the same gesture the sidebar's own "Used by"
 * panel already used (model-modals.jsx:354).
 *
 * `slotsUsingModel` (model-usage.js) matches a slot's `model`/`model_default`
 * field against the model's `id` — most seed slots carry a display TAG in
 * `model` (e.g. "qwen3.6-27b-mtp-q4_k_m") that differs from the registry
 * `id` ("qwen3.6-27b-mtp"), so they show 0 used-by here. The "tts" seed slot
 * (tests/e2e/fixtures/mock-data.ts) is the one pair where `model` IS the
 * registry id — "kokoro-v1" — matching the base HAL0_DATA models list's
 * `kokoro-v1` row (src/dash/data.jsx) exactly.
 */
import { test, expect } from '../fixtures/apiMock'

test.describe('Model drawer — used-by slot jump', () => {
  test('clicking a used-by slot name closes the drawer and opens the slot editor', async ({ page }) => {
    await page.goto('/#models')
    await page.locator('.mdl-row', { hasText: 'kokoro-v1' }).first().click()
    await page.locator('button:has-text("Edit options")').first().click()

    const usedBy = page.getByTestId('model-facts-usedby')
    await expect(usedBy).toContainText('tts')
    await usedBy.getByRole('button', { name: 'tts' }).click()

    // Drawer closes and the route jumps to the slot editor.
    await expect(page.getByTestId('model-facts-usedby')).toHaveCount(0)
    await expect.poll(() => page.url()).toContain('#slots/tts')
  })
})
