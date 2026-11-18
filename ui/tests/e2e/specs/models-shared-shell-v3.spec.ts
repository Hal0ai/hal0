/**
 * models-shared-shell-v3 — what the shared catalog shell (dash/models/catalog-shell.jsx)
 * adds to the Inference tab: the "In use" section, the State filter, one
 * status chip per row, the NPU fit of a GGUF that ggml-xdna can run, and the header's
 * Check updates / Add actions.
 *
 * Forced-mock note: /api/models, /api/slots and /api/npu/occupancy are served by the
 * in-bundle HAL0_DATA mock layer, so rows and the slot that uses one are injected via
 * addInitScript (as in models-default-for-type-indicator-v3). /api/runners/xdna/fit is
 * not in that layer and is routed here.
 */
import { test, expect } from '../fixtures/apiMock'

const USED = 'local.shell-used'
const FREE = 'local.shell-free'
const MISSING = 'local.shell-missing'
const row = (id: string, longName: string, extra: Record<string, unknown> = {}) => ({
  id,
  name: id,
  longName,
  installed: true,
  ns: 'pulled',
  capabilities: ['chat'],
  backends: ['vulkan'],
  size_bytes: 2_000_000_000,
  type: 'chat',
  created: 1_700_000_000,
  ...extra,
})
const ROWS = [
  row(USED, 'Shell Used Model'),
  row(FREE, 'Shell Free Model'),
  row(MISSING, 'Shell Missing Model', { installed: false, ns: 'blessed' }),
]

test.beforeEach(async ({ page }) => {
  await page.addInitScript(
    ({ rows, used }) => {
      let stored: any
      Object.defineProperty(window, 'HAL0_DATA', {
        configurable: true,
        get: () => stored,
        set: (v) => {
          if (v && Array.isArray(v.models)) v.models = [...rows, ...v.models]
          // Point the mock's `primary` slot at the "used" row.
          const slot = v && Array.isArray(v.slots) ? v.slots.find((s: any) => s.name === 'primary') : null
          if (slot) {
            slot.model = used
            slot.model_id = used
          }
          stored = v
        },
      })
    },
    { rows: ROWS, used: USED },
  )
  await page.route('**/api/runners/xdna/fit', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        models: {
          [USED]: { xdna_path: 'per-op', reason: 'No fused kernel for this geometry.' },
          [FREE]: { xdna_path: 'fused', reason: 'Fused decode path.' },
        },
      }),
    }),
  )
})

test.describe('Models — shared catalog shell', () => {
  test('a model a slot references leads the list under "In use" and says which slot', async ({ page }) => {
    await page.goto('/#models')
    const inUse = page.getByTestId('mdl-section-inuse')
    await expect(inUse.locator('.mdl-row', { hasText: 'Shell Used Model' })).toBeVisible()
    await expect(inUse.locator('.mdl-row', { hasText: 'Shell Used Model' }).locator('.mdl-status')).toHaveText('in use · primary')
    // A free installed model sits under Installed, a blessed one that is not on disk under Available.
    await expect(page.getByTestId('mdl-section-installed').locator('.mdl-row', { hasText: 'Shell Free Model' })).toBeVisible()
    const missing = page.getByTestId('mdl-section-blessed').locator('.mdl-row', { hasText: 'Shell Missing Model' })
    await expect(missing.locator('.mdl-status')).toHaveText('not installed')
  })

  test('the State filter narrows the list and Clear restores it', async ({ page }) => {
    await page.goto('/#models')
    await page.locator('.mdl-search').fill('Shell')
    await expect(page.locator('.mdl-row')).toHaveCount(3)
    await page.getByTestId('mdl-filter-inuse').click()
    await expect(page.locator('.mdl-row')).toHaveCount(1)
    await expect(page.locator('.mdl-row')).toContainText('Shell Used Model')
    // The groups are OR within themselves: adding Not installed brings that row back.
    await page.getByTestId('mdl-filter-available').click()
    await expect(page.locator('.mdl-row')).toHaveCount(2)
    await page.locator('.mdl-clear').click()
    await expect(page.locator('.mdl-search')).toHaveValue('')
    await expect(page.getByTestId('mdl-filter-inuse')).toHaveAttribute('aria-pressed', 'false')
  })

  test('a GGUF the NPU can run shows its ggml-xdna fit on the row and in the detail pane', async ({ page }) => {
    await page.goto('/#models')
    const used = page.locator('.mdl-row', { hasText: 'Shell Used Model' })
    await expect(used.getByTestId('mdl-row-npu')).toHaveText('npu · per-op')
    await expect(page.locator('.mdl-row', { hasText: 'Shell Free Model' }).getByTestId('mdl-row-npu')).toHaveText('npu · fused')
    await expect(page.locator('.mdl-row', { hasText: 'Shell Missing Model' }).getByTestId('mdl-row-npu')).toHaveCount(0)

    await used.click()
    const fit = page.getByTestId('mdl-npu-fit')
    await expect(fit.getByTestId('mdl-npu-fit-path')).toHaveText('per-op')
    await expect(fit).toContainText('No fused kernel for this geometry.')
    await expect(fit).toContainText('Cannot hold the NPU together with FLM')

    // "Runs on: NPU" keeps only the rows with a fit.
    await page.locator('.mdl-search').fill('Shell')
    await page.getByTestId('mdl-filter-npu').click()
    await expect(page.locator('.mdl-row')).toHaveCount(2)
  })

  test('the Providers tab carries Refresh model lists and Add provider', async ({ page }) => {
    await page.goto('/#models/providers')
    await expect(page.getByTestId('providers-tab')).toBeVisible()
    await expect(page.getByTestId('mdl-check-providers')).toHaveText('Refresh model lists')
    await page.getByTestId('mdl-add').click()
    await expect(page.getByTestId('add-provider-modal')).toBeVisible()
  })

  test('a row is reachable and selectable from the keyboard', async ({ page }) => {
    await page.goto('/#models')
    const free = page.locator('.mdl-row', { hasText: 'Shell Free Model' })
    await free.focus()
    await page.keyboard.press('Enter')
    await expect(free).toHaveClass(/sel/)
    await expect(page.locator('.mdl-detail-h .nm')).toHaveText('Shell Free Model')
  })
})
