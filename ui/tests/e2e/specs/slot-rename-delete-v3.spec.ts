/**
 * slot-rename-delete-v3 — D2 decomposed rename + delete dialogs.
 *
 * Rename: the slot name is a mutable display label; the stable slot_id never
 * changes. Rename requires the unit OFFLINE — while the slot runs, the field
 * is disabled. Offline, a rename commits through the SAME wire contract:
 * POST /api/slots/{name}/rename { new_name }.
 *
 * Task 11a replaced the standalone RenameSlotDialog's "Rename…" button +
 * disabled Name field with an inline-editable title field
 * (`slot-name-inline`) that commits on blur/Enter through the identical
 * `useSlotRename` mutation, gated offline-only exactly like the retired
 * dialog gated it (the drawer no longer mounts RenameSlotDialog at all —
 * `slot-rename-open` no longer exists). Escape cancels the edit: it reverts
 * the draft WITHOUT committing (the blur that follows must not fire the
 * stale draft — nameCancelRef in slot-modals.jsx) and never bubbles to the
 * drawer's own Escape→close handler. The disabled state carries its
 * stop-first reason both as the `title` attribute and as an
 * `aria-describedby` sr-only description for non-hover access.
 *
 * Delete (DeleteSlotDialog): unaffected by the drawer restructure — the
 * confirm states the true blast radius (unit, port → PortAuthority, state)
 * and that the model + weights are untouched; type-to-confirm the name, then
 * DELETE /api/slots/{name}.
 *
 * Slot GETs are served from the in-bundle mock (VITE_MOCK_HAL0) off
 * window.HAL0_DATA, so we seed slot STATE via addInitScript; the mutations
 * (POST /rename, DELETE) reach the network and are captured via page.route.
 */
import { test, expect, type Page } from '../fixtures/apiMock'

// Seed the slot list the in-bundle mock serves for /api/slots + /api/slots/:name.
async function seedSlots(page: Page, slots: any[]) {
  await page.addInitScript((slots) => {
    let real: any
    Object.defineProperty(window, 'HAL0_DATA', {
      configurable: true,
      get() { return real },
      set(v) { real = v; if (v && typeof v === 'object') v.slots = slots },
    })
  }, slots)
}

const RUNNING = {
  name: 'primary', type: 'llm', device: 'gpu-rocm', profile: 'rocm',
  model: 'qwen3.6-27b-mtp-q4_k_m', model_id: 'qwen3.6-27b-mtp', id: 1,
  state: 'serving', port: 8092, isDefault: true, runtime: 'container',
  container_status: 'running', container_health: true,
  metrics: { ctx: 8192, toks: 42, ttft: 180 },
}
// Same slot, fully offline (stopped + cold) so rename is enabled.
const OFFLINE = {
  ...RUNNING, state: 'stopped', container_status: 'stopped', container_health: false,
  metrics: {}, isDefault: false,
}

test.describe('Slot rename', () => {
  test('running slot: the inline name field is disabled with a stop-first title', async ({ page }) => {
    await seedSlots(page, [RUNNING])
    await page.goto('/#slots/primary')
    await expect(page.locator('.drawer')).toBeVisible()

    const nameField = page.getByTestId('slot-name-inline')
    await expect(nameField).toBeDisabled()
    await expect(nameField).toHaveAttribute('title', /Stop the slot to rename/i)
  })

  test('offline slot: committing the inline name field POSTs /api/slots/{name}/rename', async ({ page }) => {
    let body: any = null
    await page.route('**/api/slots/primary/rename', async (route) => {
      body = JSON.parse(route.request().postData() || '{}')
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
    })
    await seedSlots(page, [OFFLINE])
    await page.goto('/#slots/primary')
    await expect(page.locator('.drawer')).toBeVisible()

    const nameField = page.getByTestId('slot-name-inline')
    await expect(nameField).toBeEnabled()
    await expect(nameField).toHaveAttribute('title', /click to rename/i)
    await nameField.fill('primary-2')
    await nameField.press('Enter')
    await expect.poll(() => body?.new_name).toBe('primary-2')
  })

  test('offline slot: Escape cancels the edit — draft reverts, no rename POST, drawer stays open', async ({ page }) => {
    const posts: any[] = []
    await page.route('**/api/slots/primary/rename', async (route) => {
      posts.push(JSON.parse(route.request().postData() || '{}'))
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
    })
    await seedSlots(page, [OFFLINE])
    await page.goto('/#slots/primary')
    await expect(page.locator('.drawer')).toBeVisible()

    const nameField = page.getByTestId('slot-name-inline')
    await nameField.fill('primary-2')
    await nameField.press('Escape')

    // The draft reverts to the persisted name…
    await expect(nameField).toHaveValue('primary')
    // …the drawer survives (Escape in the field is consumed, it must not
    // reach the drawer's document-level Escape→close handler)…
    await expect(page.locator('.drawer')).toBeVisible()
    // …and the blur Escape triggers must NOT commit the stale edited draft
    // (the cancel-then-blur ordering bug this test pins).
    expect(posts).toHaveLength(0)

    // The field still works after a cancel: a fresh edit + Enter commits.
    await nameField.fill('primary-3')
    await nameField.press('Enter')
    await expect.poll(() => posts.length).toBeGreaterThan(0)
    expect(posts[0]).toEqual({ new_name: 'primary-3' })
  })
})

test.describe('Slot delete', () => {
  test('confirm states the blast radius + model untouched, then DELETEs', async ({ page }) => {
    const deletes: string[] = []
    await page.route('**/api/slots/primary', async (route) => {
      if (route.request().method() === 'DELETE') deletes.push(route.request().url())
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
    })
    await seedSlots(page, [OFFLINE])
    await page.goto('/#slots/primary')
    await page.getByTestId('slot-delete-open').click()

    const blast = page.getByTestId('delete-slot-blast')
    await expect(blast).toBeVisible()
    await expect(blast).toContainText('hal0-slot@primary.service')
    await expect(blast).toContainText(':8092')
    await expect(blast).toContainText(/weights are untouched/i)

    // The confirm renders in a Modal (.modal-shell); the drawer's trigger has
    // the same label, so scope to the modal to disambiguate.
    await page.locator('.modal-shell input.input.mono').last().fill('primary')
    await page.locator('.modal-shell button:has-text("Delete slot")').click()
    await expect.poll(() => deletes.length).toBeGreaterThan(0)
  })
})
