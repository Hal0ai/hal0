/**
 * slot-drawer-lifecycle — the "When it unloads" section (Task 11b) that
 * pairs Auto-Load with Eviction priority.
 *
 * Companion to slot-pin-toggle-v3.spec.ts (which owns the header Pin/
 * Auto-Load toggle wiring itself — PUT /config { pinned } / { autoload }).
 * This file owns the CROSS-CONTROL contract the lifecycle regroup
 * introduced: pinning a slot disables the priority input (with an
 * explainer) but leaves Auto-Load fully live, because Auto-Load answers "did
 * this slot start at boot" while priority answers "who unloads first under
 * pressure" — orthogonal questions the operator ruling (Task 11b) says a
 * pinned slot only silences the second one.
 *
 *   L1. pin ON  ⇒ priority input disabled + the "Pinned — never evicted…"
 *       explainer replaces the unpinned hint; Auto-Load stays enabled and
 *       toggling it still fires PUT /config { autoload }.
 *   L2. pin OFF ⇒ priority input enabled, "lower priority unloads first…"
 *       hint shown.
 *   L3. a pinned anchor SEEDS disabled (no click needed) — the disabled
 *       state is a property of `slot.pinned`, not of having just clicked Pin.
 *   L4. Escape layering: Escape inside an OPEN RichSelect closes only the
 *       dropdown (rich-select.jsx consumes it via stopPropagation); the
 *       drawer's document-level Escape handler only fires when no dropdown
 *       is open. One press = one layer.
 *
 * List seeding mirrors slot-pin-toggle-v3.spec.ts (in-bundle HAL0_DATA;
 * mutations fall through to page.route).
 */
import { test, expect, type Page } from '../fixtures/apiMock'
import { openRichSelect, closeRichSelect } from '../fixtures/richSelect'

const PRIMARY = {
  name: 'primary', type: 'llm', device: 'gpu-rocm', profile: 'rocm',
  model: 'qwen3.6-27b', model_id: 'qwen3.6-27b', modelLong: 'qwen3.6-27b',
  group: 'chat', state: 'serving', port: 8092, isDefault: true,
  pinned: false, n_gpu_layers: -1,
  metrics: { ctx: 8192, toks: 42, ttft: 180, kv: 35 },
}
const PINNED_ANCHOR = { ...PRIMARY, name: 'utility', pinned: true, isDefault: false, port: 8090 }

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

async function capturePuts(page: Page, name: string) {
  const puts: any[] = []
  await page.route(`**/api/slots/${name}/config`, async (route) => {
    if (route.request().method() === 'PUT') {
      puts.push(JSON.parse(route.request().postData() || '{}'))
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
  })
  return puts
}

const priorityInput = (page: Page) => page.getByTestId('slot-priority-input')
const pinToggle = (page: Page) => page.locator('label[data-testid="slot-pin-toggle"]')
const autoloadToggle = (page: Page) => page.locator('label[data-testid="slot-autoload-toggle"]')

test.describe('Slot drawer — lifecycle row ("When it unloads", Task 11b)', () => {
  test('L1 — pinning ON disables priority + shows the pinned explainer, Auto-Load stays live', async ({ page }) => {
    const puts = await capturePuts(page, 'primary')
    await seedSlots(page, [PRIMARY])
    await page.goto('/#slots/primary')
    await expect(page.locator('.drawer')).toBeVisible()

    // Unpinned baseline: enabled, "lower priority unloads first" hint.
    await expect(priorityInput(page)).toBeEnabled()
    const lifecycleRow = page.locator('.form-row').filter({
      has: page.getByTestId('slot-priority-input'),
    })
    await expect(lifecycleRow).toContainText(/lower priority unloads first/i)
    await expect(lifecycleRow).not.toContainText(/pinned — never evicted/i)

    await pinToggle(page).click()
    await expect.poll(() => puts.length).toBeGreaterThan(0)
    expect(puts[0]).toEqual({ pinned: true })

    // The drawer prop is poll-driven; flip HAL0_DATA to reflect the write so
    // the next poll carries `pinned: true` back down, same as a real PUT
    // round-trip would.
    await page.evaluate(() => {
      ;(window as any).HAL0_DATA.slots = [{ ...(window as any).HAL0_DATA.slots[0], pinned: true }]
    })

    await expect(priorityInput(page)).toBeDisabled({ timeout: 20_000 })
    await expect(lifecycleRow).toContainText(/pinned — never evicted; priority has no effect while pinned/i)
    await expect(lifecycleRow).not.toContainText(/lower priority unloads first/i)

    // Auto-Load is untouched by pinning — still enabled and still wired.
    const autoload = autoloadToggle(page)
    await expect(autoload.locator('input[type="checkbox"]')).toBeEnabled()
    await autoload.click()
    await expect.poll(() => puts.length).toBeGreaterThan(1)
    expect(puts[puts.length - 1]).toEqual({ autoload: true })
  })

  test('L2 — pin OFF leaves priority enabled with the unpinned hint', async ({ page }) => {
    await seedSlots(page, [PRIMARY])
    await page.goto('/#slots/primary')
    await expect(page.locator('.drawer')).toBeVisible()

    await expect(priorityInput(page)).toBeEnabled()
    const lifecycleRow = page.locator('.form-row').filter({
      has: page.getByTestId('slot-priority-input'),
    })
    await expect(lifecycleRow).toContainText(/lower priority unloads first · ties go to least recently used/i)
  })

  test('L3 — a pinned anchor seeds priority disabled with no click needed', async ({ page }) => {
    await seedSlots(page, [PINNED_ANCHOR])
    await page.goto('/#slots/utility')
    await expect(page.locator('.drawer')).toBeVisible()

    await expect(
      page.locator('[data-testid="slot-pin-toggle"] input[type="checkbox"]'),
    ).toBeChecked()
    await expect(priorityInput(page)).toBeDisabled()
    const lifecycleRow = page.locator('.form-row').filter({
      has: page.getByTestId('slot-priority-input'),
    })
    await expect(lifecycleRow).toContainText(/pinned — never evicted/i)
    // Auto-Load is still a live, enabled control on a pinned slot.
    await expect(
      autoloadToggle(page).locator('input[type="checkbox"]'),
    ).toBeEnabled()
  })

  test('L4 — Escape in an open RichSelect closes only the dropdown; the drawer needs its own press', async ({ page }) => {
    await seedSlots(page, [PRIMARY])
    await page.goto('/#slots/primary')
    await expect(page.locator('.drawer')).toBeVisible()

    const trigger = page.getByTestId('slot-hw-runtime')
    const listbox = await openRichSelect(trigger)
    await expect(listbox).toBeVisible()

    // First Escape (closeRichSelect): consumed by the RichSelect — the
    // dropdown unmounts, the drawer stays.
    await closeRichSelect(trigger)
    await expect(trigger).toHaveAttribute('aria-expanded', 'false')
    await expect(listbox).toHaveCount(0)
    await expect(page.locator('.drawer')).toBeVisible()

    // Second Escape, with no dropdown open, DOES reach the drawer's
    // document-level handler and closes it — proving the first press was
    // consumed by the dropdown, not merely lagging behind an animation.
    await page.keyboard.press('Escape')
    await expect(page.locator('.drawer')).toBeHidden()
  })
})
