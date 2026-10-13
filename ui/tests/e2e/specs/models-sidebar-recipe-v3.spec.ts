/**
 * models-sidebar-recipe-v3 — the model detail pane's "File facts" section
 * (ADR-0029). The model carries FILE FACTS only — chat template, jinja, MTP
 * heads — plus its native context window (read-only, from metadata). The tune
 * (profile, context size, launch flags, n_gpu_layers, rope_freq_base) lives on
 * a profile, so a row that still carries any of those on disk must never render
 * them. The section keeps the model drawer's form-row/form-lbl/form-ctl +
 * FieldInfoIcon rhythm.
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
  metadata: { context_length: 131072 },
  // File facts, top-level on the model (ADR-0029 §6).
  chat_template: 'chatml',
  jinja: false,
  mtp: true,
  // Stray tuning keys on the row — they belong on a profile and must never
  // render here.
  profile: 'rocm-moe',
  context_size: 8192,
  extra_args: '-fa on -b 2048',
  n_gpu_layers: 999,
  rope_freq_base: 10000,
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

test.describe('Models sidebar — file facts', () => {
  test('renders chat template / jinja / MTP heads / native context with the drawer form-row rhythm', async ({ page }) => {
    await page.goto('/#models')
    await page.locator('.mdl-row', { hasText: 'Legacy Recipe Model' }).click()

    // Three panels share `.mdl-detail-recipe` (File facts / Used by / On
    // disk); it's rendered first in ModelDetail's JSX.
    const recipe = page.locator('.mdl-detail-recipe').first()
    await expect(recipe.getByTestId('mdl-recipe-row-chat_template')).toContainText('chatml')
    await expect(recipe.getByTestId('mdl-recipe-row-jinja')).toContainText('false')
    await expect(recipe.getByTestId('mdl-recipe-row-mtp')).toContainText('true')
    await expect(recipe.getByTestId('mdl-recipe-row-native_context')).toContainText('131,072')

    // form-row/form-lbl/form-ctl + FieldInfoIcon — the drawer's rhythm, not
    // the old bespoke `.ro-row` grid.
    await expect(recipe.locator('.form-row')).toHaveCount(4)
    await expect(recipe.locator('.field-info-btn')).toHaveCount(4)
  })

  test('never renders a tuning key the model no longer owns', async ({ page }) => {
    await page.goto('/#models')
    await page.locator('.mdl-row', { hasText: 'Legacy Recipe Model' }).click()

    const recipe = page.locator('.mdl-detail-recipe').first()
    for (const key of ['profile', 'context_size', 'extra_args', 'n_gpu_layers', 'rope_freq_base']) {
      await expect(recipe).not.toContainText(key)
      await expect(page.getByTestId(`mdl-recipe-row-${key}`)).toHaveCount(0)
    }
  })

  test('a model with no facts still shows the empty-state message', async ({ page }) => {
    await page.goto('/#models')
    // The injected fixture row is installed + prepended, so it — not
    // Qwen3.6-27B-MTP — wins auto-selection here; select explicitly.
    // Qwen3.6-27B-MTP carries no file facts in the base fixture.
    await page.locator('.mdl-row', { hasText: 'Qwen3.6-27B-MTP' }).click()
    const recipe = page.locator('.mdl-detail-recipe').first()
    await expect(recipe).toContainText('No file facts recorded')
    await expect(recipe.locator('.form-row')).toHaveCount(0)
  })
})
