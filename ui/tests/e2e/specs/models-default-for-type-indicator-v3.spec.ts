/**
 * models-default-for-type-indicator-v3 (GH #1440) — the catalog LIST must
 * show which model is the default for its type, not just the drawer.
 *
 * `model.default` (boolean) already drives the drawer's "Default for
 * {type}" badge (model-drawer.jsx `isTypeDefault`), but the list row
 * (`ModelRow` in models.jsx) never read it — an operator had to open every
 * row's drawer one at a time to find the current default.
 *
 * Forced-mock note: mirrors models-catalog-controls-v3.spec.ts — /api/models
 * is served by the client-side HAL0_DATA mock layer (VITE_MOCK_HAL0), so
 * fixture rows are injected via addInitScript, not page.route.
 */
import { test, expect } from '../fixtures/apiMock'

const ROWS = [
  {
    id: 'local.default-chat',
    name: 'default-chat',
    longName: 'Default Chat Model',
    installed: true,
    ns: 'pulled',
    capabilities: ['chat'],
    backends: ['rocm'],
    size_bytes: 4_500_000_000,
    type: 'chat',
    default: true,
    created: 1_700_000_000,
  },
  {
    id: 'local.not-default-chat',
    name: 'not-default-chat',
    longName: 'Fallback Chat Model',
    installed: true,
    ns: 'pulled',
    capabilities: ['chat'],
    backends: ['rocm'],
    size_bytes: 4_500_000_000,
    type: 'chat',
    default: false,
    created: 1_650_000_000,
  },
]

test.beforeEach(async ({ page }) => {
  await page.addInitScript((rows) => {
    let stored: any
    Object.defineProperty(window, 'HAL0_DATA', {
      configurable: true,
      get: () => stored,
      set: (v) => {
        if (v && Array.isArray(v.models)) v.models = [...rows, ...v.models]
        stored = v
      },
    })
  }, ROWS)
})

test.describe('Models catalog — default-for-type indicator (#1440)', () => {
  test('the default model for a type carries a visible badge in the list row', async ({ page }) => {
    await page.goto('/#models')
    const defaultRow = page.locator('.mdl-row', { hasText: 'Default Chat Model' })
    await expect(defaultRow).toBeVisible()
    await expect(defaultRow.getByTestId('mdl-row-default')).toBeVisible()
  })

  test('a non-default model in the same type carries no badge', async ({ page }) => {
    await page.goto('/#models')
    const nonDefaultRow = page.locator('.mdl-row', { hasText: 'Fallback Chat Model' })
    await expect(nonDefaultRow).toBeVisible()
    await expect(nonDefaultRow.getByTestId('mdl-row-default')).toHaveCount(0)
  })
})
