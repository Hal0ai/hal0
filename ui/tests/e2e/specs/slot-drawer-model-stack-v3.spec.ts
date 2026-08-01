/**
 * slot-drawer-model-stack-v3 — the slot edit drawer stacks the model drawer
 * SIDE BY SIDE, not on top of it.
 *
 * Nothing covered `data-testid="slot-model-edit-open"` or the stacked
 * ModelDrawer before this file. The geometry is the whole point: there are no
 * portals in this app, `.drawer` is `position: fixed; right: 0; z-index: 95`,
 * and both drawers used to dock flush-right at equal z-index — so the
 * later-rendered ModelDrawer fully COVERED the slot drawer and the "in place"
 * edit was a lie.
 *
 *   S1. the model field's pencil opens the model drawer for the BOUND model.
 *   S2. both drawers are simultaneously visible AND interactive — a slot
 *       hardware control and a model-drawer control are usable at once.
 *   S3. the docked drawer's right edge lands exactly on the slot drawer's left
 *       edge (dock offset === the slot drawer's 560px width).
 *   S4. exactly ONE dim scrim — the docked drawer's own backdrop paints
 *       nothing (no double-darkening) but is still a click target.
 *   S5. one Esc closes only the TOP drawer; the slot drawer and its unsaved
 *       edits survive.
 *   S6. below the 1200px breakpoint the dock degrades to the overlay stack
 *       rather than pushing 1160px of panels off-canvas to the left.
 *
 * List seeding mirrors slot-drawer-field-wiring-v3.spec.ts (in-bundle
 * HAL0_DATA; VITE_MOCK_HAL0=1 short-circuits GET /api/slots before page.route
 * sees it).
 */
import { test, expect, type Page } from '../fixtures/apiMock'

/** The slot drawer's width — and therefore the expected dock offset. */
const SLOT_DRAWER_WIDTH = 560

// `model_id` must be a real HAL0_DATA.models id: the pencil resolves the slot
// to a registry ROW (ModelDrawer renders nothing for a null model) and is
// disabled when that lookup fails.
const PRIMARY = {
  name: 'primary', type: 'llm', device: 'gpu-rocm', profile: 'rocm',
  model: 'qwen3.6-27b-mtp', model_id: 'qwen3.6-27b-mtp',
  modelLong: 'Qwen3.6-27B-MTP',
  group: 'chat', state: 'serving', port: 8092, isDefault: true,
  n_gpu_layers: -1,
  metrics: { ctx: 8192, toks: 42, ttft: 180, kv: 35 },
}
// Bound to an id that is NOT in the registry — the pencil must be disabled
// rather than opening an empty editor.
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

/** Stub the sibling writes so nothing leaks to the vite proxy. */
async function stubWrites(page: Page, name: string) {
  await page.route(`**/api/slots/${name}/config`, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: '{}' }),
  )
  await page.route(`**/api/slots/${name}/defaults`, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: '{}' }),
  )
}

const slotDrawer = (page: Page) => page.locator('.drawer:not(.drawer--docked)')
const modelDrawer = (page: Page) => page.locator('.drawer.drawer--docked')

/** Open the slot drawer, then the stacked model drawer. */
async function openStack(page: Page, name = 'primary') {
  await page.goto(`/#slots/${name}`)
  await expect(slotDrawer(page)).toBeVisible()
  await page.getByTestId('slot-model-edit-open').click()
  await expect(modelDrawer(page)).toBeVisible()
}

test.describe('Slot drawer — stacked model editor', () => {
  test('S1 — the model field pencil opens the model drawer for the BOUND model', async ({ page }) => {
    await stubWrites(page, 'primary')
    await seedSlots(page, [PRIMARY])

    await page.goto('/#slots/primary')
    // Icon button, not the old "Edit model…" text button. Scoped to `button`
    // rather than the whole `.drawer` — the slot drawer's own launch-tune
    // hint prose ("edit them with "Edit model…" above", #1508) legitimately
    // contains this phrase now, so a drawer-wide text filter false-positives.
    const pencil = page.getByTestId('slot-model-edit-open')
    await expect(pencil).toBeVisible()
    await expect(pencil).toBeEnabled()
    await expect(pencil.locator('svg')).toHaveCount(1)
    await expect(page.locator('.drawer button', { hasText: 'Edit model…' })).toHaveCount(0)
    // Nothing is stacked yet.
    await expect(modelDrawer(page)).toHaveCount(0)

    await pencil.click()
    await expect(modelDrawer(page)).toBeVisible()
    // It is the model editor, opened on the slot's BOUND row — the drawer title
    // is `longName || name || id`, and the Display-name placeholder is the id.
    await expect(modelDrawer(page).locator('.modal-h-eye')).toContainText('Edit model')
    await expect(modelDrawer(page).locator('.drawer-h h2')).toHaveText('Qwen3.6-27B-MTP')
    await expect(modelDrawer(page).getByTestId('model-name-input')).toHaveAttribute(
      'placeholder',
      'qwen3.6-27b-mtp',
    )
  })

  test('S1 — the pencil is disabled when the bound model has no registry row', async ({ page }) => {
    await stubWrites(page, 'orphan')
    await seedSlots(page, [ORPHAN])

    await page.goto('/#slots/orphan')
    await expect(slotDrawer(page)).toBeVisible()
    // ModelDrawer needs the ROW, not an id, so with no row there is nothing to
    // open — the control says so instead of flashing an empty editor.
    await expect(page.getByTestId('slot-model-edit-open')).toBeDisabled()
  })

  test('S2 — both drawers are visible and interactive at the same time', async ({ page }) => {
    await stubWrites(page, 'primary')
    await seedSlots(page, [PRIMARY])
    await openStack(page)

    // Two live drawers, not one covering the other.
    await expect(page.locator('.drawer')).toHaveCount(2)
    await expect(slotDrawer(page)).toBeVisible()
    await expect(modelDrawer(page)).toBeVisible()

    // A slot HARDWARE control and a MODEL control are both visible together —
    // the old behaviour hid the slot drawer entirely behind the model drawer.
    const ngl = page.getByTestId('slot-hw-ngl')
    const flags = modelDrawer(page).getByTestId('model-flags-input')
    await expect(ngl).toBeVisible()
    await expect(flags).toBeVisible()

    // …and both accept input while the other stays put (visible ⇒ hit-testable,
    // which is what the covering stack broke).
    await flags.fill('-fa on -b 2048')
    await expect(flags).toHaveValue('-fa on -b 2048')
    await ngl.fill('24')
    await expect(ngl).toHaveValue('24')
    await expect(flags).toHaveValue('-fa on -b 2048')
  })

  test('S3 — the docked drawer sits flush against the slot drawer, offset by its width', async ({ page }) => {
    await stubWrites(page, 'primary')
    await seedSlots(page, [PRIMARY])
    await openStack(page)

    // The dock offset is declared on the element, so a drift between the slot
    // drawer's width and the offset is caught here rather than by eyeball.
    await expect(modelDrawer(page)).toHaveAttribute(
      'data-drawer-dock',
      String(SLOT_DRAWER_WIDTH),
    )
    await expect(slotDrawer(page)).not.toHaveAttribute('data-drawer-dock', /.*/)

    const slotBox = (await slotDrawer(page).boundingBox())!
    const modelBox = (await modelDrawer(page).boundingBox())!
    expect(slotBox).toBeTruthy()
    expect(modelBox).toBeTruthy()

    // Slot drawer still flush right; docked drawer's right edge == the slot
    // drawer's left edge (flush seam, no gap, no overlap).
    const viewport = page.viewportSize()!
    expect(Math.round(slotBox.x + slotBox.width)).toBe(viewport.width)
    expect(Math.round(slotBox.width)).toBe(SLOT_DRAWER_WIDTH)
    expect(Math.abs(modelBox.x + modelBox.width - slotBox.x)).toBeLessThanOrEqual(1)
    // …and neither is pushed off-canvas to the left.
    expect(modelBox.x).toBeGreaterThanOrEqual(0)
  })

  test('S4 — the stacked drawer paints no second scrim (no double-darkening)', async ({ page }) => {
    await stubWrites(page, 'primary')
    await seedSlots(page, [PRIMARY])
    await openStack(page)

    // Two backdrops exist (each Drawer renders its own) but exactly ONE paints.
    const backdrops = page.locator('.drawer-backdrop')
    await expect(backdrops).toHaveCount(2)
    await expect(page.locator('.drawer-backdrop--clear')).toHaveCount(1)

    const painted = await backdrops.evaluateAll((els) =>
      els.map((el) => getComputedStyle(el).backgroundColor),
    )
    const opaqueish = painted.filter((c) => c !== 'rgba(0, 0, 0, 0)' && c !== 'transparent')
    expect(opaqueish).toHaveLength(1)

    // The clear one is still a click target — click-outside dismisses the TOP
    // drawer and leaves the slot drawer alone.
    await page.mouse.click(40, 400)
    await expect(modelDrawer(page)).toHaveCount(0)
    await expect(slotDrawer(page)).toBeVisible()
  })

  test('S5 — one Esc closes only the model drawer; the slot drawer keeps its edits', async ({ page }) => {
    await stubWrites(page, 'primary')
    await seedSlots(page, [PRIMARY])
    await openStack(page)

    // Dirty the slot drawer first — a single Esc used to fire BOTH drawers'
    // document-level keydown handlers.
    await page.getByTestId('slot-hw-threads').fill('12')

    await page.keyboard.press('Escape')
    await expect(modelDrawer(page)).toHaveCount(0)
    await expect(slotDrawer(page)).toBeVisible()
    // No discard dialog popped underneath, and the edit is intact.
    await expect(page.locator('.modal-shell', { hasText: 'Discard' })).toHaveCount(0)
    await expect(page.getByTestId('slot-hw-threads')).toHaveValue('12')
  })

  test('S6 — below 1200px the dock degrades to the overlay stack, never off-canvas', async ({ page }) => {
    await page.setViewportSize({ width: 1024, height: 800 })
    await stubWrites(page, 'primary')
    await seedSlots(page, [PRIMARY])
    await openStack(page)

    const modelBox = (await modelDrawer(page).boundingBox())!
    // 560 + 600 does not fit in 1024: the media query resets `right` to 0 so
    // the panel overlays instead of hanging 136px off the left edge.
    expect(modelBox.x).toBeGreaterThanOrEqual(0)
    expect(Math.round(modelBox.x + modelBox.width)).toBe(1024)
    // Still fully usable at the narrow size.
    await expect(modelDrawer(page).getByTestId('model-flags-input')).toBeVisible()
  })
})
