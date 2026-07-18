/**
 * slot-rename-delete-v3 — D2 decomposed rename + delete dialogs.
 *
 * Rename (RenameSlotDialog): the slot name is a mutable display label; the
 * stable slot_id never changes. Rename requires the unit OFFLINE — while the
 * slot runs, the field + Save are disabled with an inline reason (not a bare
 * tooltip). Offline, a rename POSTs /api/slots/{name}/rename { new_name }.
 *
 * Delete (DeleteSlotDialog): the confirm states the true blast radius (unit,
 * port → PortAuthority, state) and that the model + weights are untouched;
 * type-to-confirm the name, then DELETE /api/slots/{name}.
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
  state: 'serving', port: 8092, isDefault: true, enabled: true, runtime: 'container',
  container_status: 'running', container_health: true,
  metrics: { ctx: 8192, toks: 42, ttft: 180 },
}
// Same slot, fully offline (stopped + cold) so rename is enabled.
const OFFLINE = {
  ...RUNNING, state: 'stopped', container_status: 'stopped', container_health: false,
  metrics: {}, isDefault: false,
}

test.describe('Slot rename', () => {
  test('running slot: rename disabled with an inline stop-first reason', async ({ page }) => {
    await seedSlots(page, [RUNNING])
    await page.goto('/#slots/primary')
    await page.getByTestId('slot-rename-open').click()

    await expect(page.getByTestId('rename-slot-input')).toBeDisabled()
    await expect(page.getByTestId('rename-slot-save')).toBeDisabled()
    const reason = page.getByTestId('rename-slot-reason')
    await expect(reason).toBeVisible()
    await expect(reason).toContainText(/Stop the slot to rename/i)
    await expect(reason).toContainText(/slot_id/i)
  })

  test('offline slot: rename POSTs /api/slots/{name}/rename', async ({ page }) => {
    let body: any = null
    await page.route('**/api/slots/primary/rename', async (route) => {
      body = JSON.parse(route.request().postData() || '{}')
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
    })
    await seedSlots(page, [OFFLINE])
    await page.goto('/#slots/primary')
    await page.getByTestId('slot-rename-open').click()

    const input = page.getByTestId('rename-slot-input')
    await expect(input).toBeEnabled()
    await input.fill('primary-2')
    await page.getByTestId('rename-slot-save').click()
    await expect.poll(() => body?.new_name).toBe('primary-2')
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
