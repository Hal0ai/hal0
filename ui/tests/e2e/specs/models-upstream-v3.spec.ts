/**
 * models-upstream-v3 — upstream-advertised rows (aggregated by
 * GET /api/models from a provider's /v1/models: installed=false +
 * upstream=<name>) stay out of the local catalog:
 *
 *   - not mixed into the Inference tab (they live under Models → Providers),
 *   - excluded from the create-slot model picker (a slot binds a
 *     local file path these rows don't have).
 *
 * Forced-mock note: VITE_MOCK_HAL0 short-circuits page.route for
 * /api/models, so the upstream fixture row is injected by intercepting
 * the `window.HAL0_DATA = …` assignment (data.jsx) via addInitScript —
 * the mock layer reads HAL0_DATA.models lazily on every fetch.
 */
import { test, expect } from '../fixtures/apiMock'

const UPSTREAM_ROW = {
  id: 'meta-llama/llama-3.3-70b-instruct',
  name: 'meta-llama/llama-3.3-70b-instruct',
  object: 'model',
  created: 1751600000,
  installed: false,
  owned_by: 'openrouter',
  upstream: 'openrouter',
  ns: 'pulled',
  capabilities: ['chat'],
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript((row) => {
    let stored: any
    Object.defineProperty(window, 'HAL0_DATA', {
      configurable: true,
      get: () => stored,
      set: (v) => {
        if (v && Array.isArray(v.models)) v.models = [...v.models, row]
        stored = v
      },
    })
  }, UPSTREAM_ROW)
})

test.describe('Models v3 — upstream-advertised rows (/models)', () => {
  test('upstream rows do not appear on Inference', async ({ page }) => {
    await page.goto('/#models')
    const upRow = page.locator('.mdl-row', { hasText: 'llama-3.3-70b-instruct' })
    await expect(upRow).toHaveCount(0)
  })

  test('local not-installed rows keep the ns chip + Pull button (control)', async ({ page }) => {
    await page.goto('/#models')
    // On Inference tab (default), upstream rows excluded; local rows present
    const row = page.locator('.mdl-row', { hasText: 'Qwen3.5-9B' })
    await expect(row).toBeVisible()
    await expect(row.locator('.chip.info')).toHaveCount(0)
    await row.click()
    await expect(
      page.locator('.mdl-detail .mdl-detail-meta > div', { hasText: 'origin' }).locator('.v'),
    ).toHaveText('local')
    await expect(page.locator('.mdl-detail button', { hasText: 'Pull' })).toBeVisible()
  })

  test('create-slot model picker excludes upstream-advertised rows', async ({ page }) => {
    await page.goto('/#slots')
    await page.locator('.view .vh button:has-text("New slot")').click()
    const modal = page.locator('.modal, [role="dialog"]').last()
    await expect(modal).toBeVisible()
    await expect(modal.locator('option', { hasText: 'Qwen3.5-9B' })).toHaveCount(1)
    await expect(modal.locator('option', { hasText: 'llama-3.3-70b-instruct' })).toHaveCount(0)
  })
})
