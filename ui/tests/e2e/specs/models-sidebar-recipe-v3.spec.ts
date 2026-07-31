/**
 * models-sidebar-recipe-v3 — Stream E model-sidebar restyle (RULINGS.md #7):
 * a visual pass, not a content rebuild. The "Recipe options" section adopts
 * the model drawer's form-row/form-lbl/form-ctl + FieldInfoIcon rhythm
 * (model-drawer.jsx:850-864) in place of the old ad-hoc `.ro-row` list, and
 * drops the two dead recipe keys (spec-hw-slot-ownership §2): `n_gpu_layers`
 * (NGL moved off the model onto the slot's HW grid) and `rope_freq_base`
 * (deprecated, never emitted). A legacy row that still carries either on
 * disk must never render it.
 *
 * Forced-mock note: same HAL0_DATA injection idiom as
 * models-catalog-controls-v3.spec.ts — VITE_MOCK_HAL0 short-circuits
 * page.route for /api/models.
 */
import { test, expect } from '../fixtures/apiMock'

const ROW = {
  id: 'local.legacy-recipe',
  name: 'legacy-recipe',
  longName: 'Legacy Recipe Model',
  installed: true,
  ns: 'pulled',
  capabilities: ['chat'],
  backends: ['rocm'],
  size_bytes: 4_000_000_000,
  defaults: {
    profile: 'rocm-moe',
    context_size: 8192,
    extra_args: '-fa on -b 2048',
    // Dead keys a legacy row might still carry on disk — must never render.
    n_gpu_layers: 999,
    rope_freq_base: 10000,
  },
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript((row) => {
    let stored: any
    Object.defineProperty(window, 'HAL0_DATA', {
      configurable: true,
      get: () => stored,
      set: (v) => {
        if (v && Array.isArray(v.models)) v.models = [row, ...v.models]
        stored = v
      },
    })
  }, ROW)
})

test.describe('Models sidebar — restyled recipe options', () => {
  test('renders profile / context size / launch flags with the drawer form-row rhythm', async ({ page }) => {
    await page.goto('/#models')
    await page.locator('.mdl-row', { hasText: 'Legacy Recipe Model' }).click()

    // Three panels share `.mdl-detail-recipe` (Recipe options / Used by / On
    // disk); it's rendered first in ModelDetail's JSX.
    const recipe = page.locator('.mdl-detail-recipe').first()
    await expect(recipe.getByTestId('mdl-recipe-row-profile')).toContainText('rocm-moe')
    await expect(recipe.getByTestId('mdl-recipe-row-context_size')).toContainText('8192')
    await expect(recipe.getByTestId('mdl-recipe-row-extra_args')).toContainText('-fa on -b 2048')

    // form-row/form-lbl/form-ctl + FieldInfoIcon — the drawer's rhythm, not
    // the old bespoke `.ro-row` grid.
    await expect(recipe.locator('.form-row')).toHaveCount(3)
    await expect(recipe.locator('.field-info-btn')).toHaveCount(3)
  })

  test('never renders the dead n_gpu_layers / rope_freq_base keys', async ({ page }) => {
    await page.goto('/#models')
    await page.locator('.mdl-row', { hasText: 'Legacy Recipe Model' }).click()

    const recipe = page.locator('.mdl-detail-recipe').first()
    await expect(recipe).not.toContainText('n_gpu_layers')
    await expect(recipe).not.toContainText('rope_freq_base')
    await expect(page.getByTestId('mdl-recipe-row-n_gpu_layers')).toHaveCount(0)
    await expect(page.getByTestId('mdl-recipe-row-rope_freq_base')).toHaveCount(0)
  })

  test('a model with no defaults still shows the empty-state message', async ({ page }) => {
    await page.goto('/#models')
    // The injected fixture row is installed + prepended, so it — not
    // Qwen3.6-27B-MTP — wins auto-selection here; select explicitly.
    // Qwen3.6-27B-MTP carries no `defaults` in the base fixture.
    await page.locator('.mdl-row', { hasText: 'Qwen3.6-27B-MTP' }).click()
    const recipe = page.locator('.mdl-detail-recipe').first()
    await expect(recipe).toContainText('No defaults set')
    await expect(recipe.locator('.form-row')).toHaveCount(0)
  })
})
