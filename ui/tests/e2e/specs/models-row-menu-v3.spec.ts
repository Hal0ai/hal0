/**
 * models-row-menu-v3 — Stream E: the far-right kebab (Icons.more) on every
 * model row opens the `Menu` primitive (primitives.jsx:762-791 — this is its
 * first real call site) whose "Edit model settings" item opens the
 * ModelDrawer directly for THAT row's model. No prior row click/select is
 * required, and opening/using the kebab must never disturb catalog
 * selection (`.mdl-row.sel`) — the drawer's target model is tracked
 * separately from `selId` for exactly this reason (models.jsx `recipeModel`).
 *
 * Default fixture (data.jsx MODELS): the dashboard auto-selects the first
 * INSTALLED model in registry order — "Qwen3.6-27B-MTP" — regardless of the
 * catalog's on-screen (alphabetical) sort order. "Qwen3-Coder-30B-A3B"
 * sorts ahead of it alphabetically and is never auto-selected, so it's a
 * reliable non-selected row to exercise the kebab against.
 */
import { test, expect } from '../fixtures/apiMock'

const SELECTED_NAME = 'Qwen3.6-27B-MTP'
const OTHER_NAME = 'Qwen3-Coder-30B-A3B'

test.describe('Models row menu (kebab)', () => {
  test('every visible row renders a kebab trigger', async ({ page }) => {
    await page.goto('/#models')
    await expect(page.locator('.mdl-row').first()).toBeVisible()
    const rowCount = await page.locator('.mdl-row').count()
    await expect(page.getByTestId('mdl-row-menu-btn')).toHaveCount(rowCount)
  })

  test('opening the kebab menu does not select the row or disturb existing selection', async ({ page }) => {
    await page.goto('/#models')
    const selectedRow = page.locator('.mdl-row', { hasText: SELECTED_NAME })
    const otherRow = page.locator('.mdl-row', { hasText: OTHER_NAME })
    await expect(selectedRow).toHaveClass(/\bsel\b/)
    await expect(otherRow).not.toHaveClass(/\bsel\b/)

    await otherRow.getByTestId('mdl-row-menu-btn').click()
    await expect(page.locator('.hal0-menu')).toBeVisible()
    await expect(page.locator('.hal0-menu-item', { hasText: 'Edit model settings' })).toBeVisible()

    // Merely opening the menu changed neither row's selection state.
    await expect(otherRow).not.toHaveClass(/\bsel\b/)
    await expect(selectedRow).toHaveClass(/\bsel\b/)
  })

  test('clicking outside the open menu dismisses it without opening the drawer', async ({ page }) => {
    await page.goto('/#models')
    const otherRow = page.locator('.mdl-row', { hasText: OTHER_NAME })
    await otherRow.getByTestId('mdl-row-menu-btn').click()
    await expect(page.locator('.hal0-menu')).toBeVisible()

    // The row's own click-outside backdrop (fixed, full-viewport) — click it
    // directly rather than guessing at uncovered page coordinates.
    await page.locator('.mdl-row-menu-backdrop').click({ position: { x: 5, y: 5 } })
    await expect(page.locator('.hal0-menu')).toHaveCount(0)
    await expect(page.getByTestId('model-flags-input')).toHaveCount(0)
  })

  test('"Edit model settings" opens the ModelDrawer directly for a non-selected row — no prior row-select needed', async ({ page }) => {
    await page.goto('/#models')
    const selectedRow = page.locator('.mdl-row', { hasText: SELECTED_NAME })
    const otherRow = page.locator('.mdl-row', { hasText: OTHER_NAME })

    await otherRow.getByTestId('mdl-row-menu-btn').click()
    await page.locator('.hal0-menu-item', { hasText: 'Edit model settings' }).click()

    // Drawer opened, targeted at the kebab'd row (not the auto-selected one).
    await expect(page.getByTestId('model-flags-input')).toBeVisible()
    await expect(page.locator('.drawer-h h2')).toHaveText(OTHER_NAME)
    // Menu closes itself on item click.
    await expect(page.locator('.hal0-menu')).toHaveCount(0)

    // Catalog selection is exactly as it was before — the kebab flow never
    // touched `selId`.
    await expect(otherRow).not.toHaveClass(/\bsel\b/)
    await expect(selectedRow).toHaveClass(/\bsel\b/)
  })
})
