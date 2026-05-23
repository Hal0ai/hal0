/**
 * models-slots-refactor.spec.ts — slice #171 v2-adapted smoke + a11y.
 *
 * Original (v1) covered: Local-file scan/register tab, Edit-slot
 * advanced disclosure, inline swap popover, cascade-delete copy. None
 * of those flows exist in v2:
 *   - Local-file tab → removed; v2 install pipeline owns user-pull
 *     under user.* namespace via AddByHF modal.
 *   - Edit-slot advanced disclosure → moved to Slots.vue + Slot drawer.
 *   - Inline swap popover → SlotCard owns swap UX.
 *   - Cascade-delete copy → carried over into v2 DeleteModelDialog with
 *     a richer warn block (covered by models-v2.spec).
 *
 * The retained intent that fits v2 is the per-modal a11y contract +
 * filter-chip behaviour. We rewrite to cover those on the new layout.
 */
import { test, expect, json, MOCK_DATA } from '../fixtures/apiMock'

async function assertDialogA11y(page: import('@playwright/test').Page) {
  const dialog = page.locator('.modal-shell')
  await expect(dialog).toBeVisible()
  await expect(dialog).toHaveAttribute('role', 'dialog')
  await expect(dialog).toHaveAttribute('aria-modal', 'true')
}

test.beforeEach(async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 })
})

test('AddByHF modal satisfies dialog a11y contract', async ({ page, cleanState: _ }) => {
  await page.route('**/api/models', (route) => json(route, { models: [] }))

  await page.goto('/models')
  await page.locator('[data-test="add-by-hf"]').click()
  await assertDialogA11y(page)

  // Esc closes
  await page.keyboard.press('Escape')
  await expect(page.locator('.modal-shell')).toBeHidden()
})

test('DeleteModelDialog satisfies dialog a11y contract', async ({ page, mockState, cleanState: _ }) => {
  const m = MOCK_DATA.models.find((mod) => mod.installed)!
  await page.route('**/api/models', (route) => json(route, { models: [m] }))

  await page.goto('/models')
  await page.locator(`[data-model-id="${m.id}"]`).click()
  await page.locator('[data-test="delete-btn"]').click()
  await assertDialogA11y(page)

  // Cancel closes
  await page.getByRole('button', { name: /^Cancel$/ }).click()
  await expect(page.locator('.modal-shell')).toBeHidden()
})

test('filter chips narrow + Clear all resets', async ({ page, cleanState: _ }) => {
  await page.route('**/api/models', (route) => json(route, { models: MOCK_DATA.models }))

  await page.goto('/models')
  // Wait for list to populate (loader replaces mock fallback async).
  await expect(page.locator('.mdl-row').first()).toBeVisible()
  await page.waitForFunction(() => document.querySelectorAll('.mdl-row').length >= 4)

  // Pre-chip: at least one non-llm row should exist (embedding/tts/etc).
  const allRowsBefore = await page.locator('.mdl-row').count()
  expect(allRowsBefore).toBeGreaterThan(2)

  // Toggle type=llm → row count drops to llm-only models.
  await page.locator('[data-filter-type="llm"]').click()
  const llmCount = await page.locator('.mdl-row').count()
  expect(llmCount).toBeLessThan(allRowsBefore)

  // Active summary shows + Clear all resets.
  const summary = page.locator('.active-summary')
  await expect(summary).toBeVisible()
  await page.locator('.clear-link').click()
  const restored = await page.locator('.mdl-row').count()
  expect(restored).toBe(allRowsBefore)
})

test('search input filters by id + repo substring', async ({ page, cleanState: _ }) => {
  await page.route('**/api/models', (route) => json(route, { models: MOCK_DATA.models }))

  await page.goto('/models')
  await page.locator('[data-test="mdl-search"]').fill('qwen')
  // At least one Qwen row remains, no non-Qwen visible.
  await expect(page.locator('.mdl-row').first()).toBeVisible()
  const visibleIds = await page.locator('.mdl-row').evaluateAll((rows) =>
    rows.map((r) => (r as HTMLElement).dataset.modelId || ''),
  )
  for (const id of visibleIds) {
    expect(id.toLowerCase()).toMatch(/qwen/)
  }
})
