/**
 * slot-card-model-edit-v3 — the inline model-edit pencil on the SLOT CARD.
 *
 * The card is `SlotScard` in dash/inference-pane.jsx: the standalone Chat +
 * Capabilities `slots.jsx` SlotCard grids were retired (slots-v3.spec.ts
 * asserts `.slots-grid` has count 0), so the InferencePane card is the live
 * per-slot surface and the only one a spec can drive.
 *
 *   K1. the pencil renders beside the model picker and opens the model drawer
 *       on the slot's BOUND registry row.
 *   K2. it does NOT fight the model picker: clicking it fires no /swap and
 *       leaves the select's value untouched. (The pencil is a SIBLING of the
 *       picker, never nested in it — same rule as slots.jsx SlotCard, where
 *       the model div is itself the InlineSwapPopover click target.)
 *   K3. it does NOT open the slot edit drawer — that is the card's own Edit
 *       control, on a different route (#slots/:name).
 *   K4. disabled when the bound model has no registry row: ModelDrawer needs
 *       the ROW, not an id, and renders nothing for null.
 *
 * The stacked/docked variant of this drawer (opened from INSIDE the slot edit
 * drawer) is covered by slot-drawer-model-stack-v3.spec.ts.
 */
import { test, expect, type Page } from '../fixtures/apiMock'

// model_id must be a real HAL0_DATA.models id for the row lookup to resolve.
const PRIMARY = {
  name: 'primary', type: 'llm', device: 'gpu-rocm', profile: 'rocm',
  runtime: 'container', container_status: 'running', container_health: true,
  model: 'qwen3.6-27b-mtp', model_id: 'qwen3.6-27b-mtp',
  modelLong: 'Qwen3.6-27B-MTP',
  group: 'chat', state: 'serving', port: 8092, isDefault: true,
  n_gpu_layers: -1,
  metrics: { ctx: 8192, toks: 42, ttft: 180, kv: 35 },
}
// Bound to an id absent from the registry.
const ORPHAN = {
  ...PRIMARY,
  name: 'orphan',
  model: 'ghost-7b',
  model_id: 'ghost-7b',
  port: 8093,
}

async function seedSlots(page: Page, slots: any[]) {
  await page.addInitScript((slots) => {
    let real: any
    Object.defineProperty(window, 'HAL0_DATA', {
      configurable: true,
      get() {
        return real
      },
      set(v) {
        real = v
        if (v && typeof v === 'object') v.slots = slots
      },
    })
  }, slots)
}

const pane = (page: Page) => page.locator('.infer-pane:not(.infer-hero-top)').first()
const card = (page: Page, name: string) => pane(page).getByTestId(`infer-slot-${name}`)
const pencil = (page: Page, name: string) => page.getByTestId(`infer-model-edit-${name}`)
// The pane's own ModelDrawer is NOT docked (nothing is stacked beneath it here).
const drawer = (page: Page) => page.locator('.drawer')

test.describe('Slot card — inline model edit', () => {
  test('K1 — the pencil sits beside the model picker and opens the bound model', async ({ page }) => {
    await seedSlots(page, [PRIMARY])
    await page.goto('/#slots')
    await expect(card(page, 'primary')).toBeVisible()

    // Sibling of the picker inside the model row, not nested inside it.
    const row = card(page, 'primary').locator('.smodel-row')
    await expect(row.locator('> select.model-picker')).toHaveCount(1)
    await expect(row.locator('> button')).toHaveCount(1)
    await expect(pencil(page, 'primary')).toBeEnabled()
    await expect(pencil(page, 'primary').locator('svg')).toHaveCount(1)
    // Nothing stacked yet.
    await expect(drawer(page)).toHaveCount(0)

    await pencil(page, 'primary').click()
    await expect(drawer(page)).toBeVisible()
    await expect(drawer(page).locator('.modal-h-eye')).toContainText('Edit model')
    await expect(drawer(page).locator('.drawer-h h2')).toHaveText('Qwen3.6-27B-MTP')
  })

  test('K2 — the pencil never fires a model swap and leaves the picker alone', async ({ page }) => {
    const swaps: any[] = []
    await page.route('**/api/slots/primary/swap', async (route) => {
      swaps.push(route.request().postDataJSON())
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
    })
    await seedSlots(page, [PRIMARY])
    await page.goto('/#slots')

    const select = card(page, 'primary').locator('select.model-picker')
    await expect(select).toHaveValue('qwen3.6-27b-mtp')

    await pencil(page, 'primary').click()
    await expect(drawer(page)).toBeVisible()

    // No swap fired, and the bound model is unchanged. `primary` is a LIVE
    // container here, so an accidental swap would also cold-restart it.
    await page.waitForTimeout(250)
    expect(swaps).toEqual([])
    await expect(select).toHaveValue('qwen3.6-27b-mtp')
  })

  test('K3 — the pencil does not open the slot edit drawer', async ({ page }) => {
    await seedSlots(page, [PRIMARY])
    await page.goto('/#slots')
    await pencil(page, 'primary').click()
    await expect(drawer(page)).toBeVisible()

    // The MODEL editor opened, not the slot editor — the route never moved to
    // #slots/primary (which is what the card's own Edit control does).
    expect(page.url()).toContain('#slots')
    expect(page.url()).not.toContain('#slots/primary')
    await expect(drawer(page)).toHaveCount(1)
    await expect(page.locator('.drawer', { hasText: 'Edit primary' })).toHaveCount(0)
  })

  test('K4 — the pencil is disabled when the bound model has no registry row', async ({ page }) => {
    await seedSlots(page, [ORPHAN])
    await page.goto('/#slots')
    await expect(card(page, 'orphan')).toBeVisible()
    await expect(pencil(page, 'orphan')).toBeDisabled()
    await expect(pencil(page, 'orphan')).toHaveAttribute('title', /No model bound/)
  })
})
