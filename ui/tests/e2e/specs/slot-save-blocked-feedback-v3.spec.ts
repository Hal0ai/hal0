/**
 * slot-save-blocked-feedback-v3 — a blocked Save must say so (#1389).
 *
 * `onSaveClick` collects per-field validation errors and bails when any are
 * set. Two of those errors could be invisible:
 *
 *   1. `extraArgsErr` is derived from the PERSISTED seed, not from user input,
 *      so a slot whose `[server].extra_args` is already malformed on disk
 *      starts in the blocked state without the operator typing anything.
 *   2. `errs.extraArgs` was never rendered anywhere. The visible extra-args
 *      error lives inside the `device !== "npu"` branch, so on an NPU slot it
 *      is in an unmounted subtree.
 *
 * Net effect on an NPU slot with a stray quote on disk: Save is an enabled
 * button that does nothing, forever, with no request, no toast and no error.
 * The operator cannot discover why, and cannot fix it from the dashboard —
 * the offending field isn't even rendered for that device.
 *
 * The contract: a Save that refuses to run must always be explainable, and a
 * persisted value the drawer does not surface must not silently veto edits to
 * unrelated fields.
 */
import { test, expect, type Page } from '../fixtures/apiMock'

const NPU_SLOT = {
  name: 'npu',
  type: 'llm',
  device: 'npu',
  profile: 'flm',
  group: 'chat',
  state: 'serving',
  port: 8092,
  enabled: true,
  n_gpu_layers: -1,
  threads: 0,
  model: 'flm-chat',
  model_id: 'flm-chat',
  model_default: 'flm-chat',
  npu: { chat: true, asr: false, embed: false },
  metrics: { ctx: 8192 },
}

const GPU_SLOT = {
  ...NPU_SLOT,
  name: 'primary',
  device: 'gpu-rocm',
  profile: 'rocm',
  model: 'qwen3.6-27b-mtp',
  model_id: 'qwen3.6-27b-mtp',
  model_default: 'qwen3.6-27b-mtp',
  npu: undefined,
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

async function captureWrites(page: Page, name: string) {
  const puts: any[] = []
  const patches: any[] = []
  await page.route(`**/api/slots/${name}/config`, async (route) => {
    if (route.request().method() === 'PUT') puts.push(route.request().postDataJSON())
    await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
  })
  await page.route(`**/api/slots/${name}/defaults`, async (route) => {
    patches.push(route.request().postDataJSON())
    await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
  })
  await page.route(`**/api/slots/${name}/restart`, (route) =>
    route.fulfill({ status: 202, contentType: 'application/json', body: '{}' }),
  )
  await page.route('**/api/slots/flm/models', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ models: [{ model: 'flm-chat', installed: true }] }),
    }),
  )
  return { puts, patches }
}

test.describe('Slot drawer — a blocked Save is always explainable (#1389)', () => {
  test('an NPU slot with malformed persisted extra_args can still save unrelated edits', async ({ page }) => {
    // The extra-args field is not even rendered for device=npu, so a stray
    // quote left on disk must not veto a THREADS edit.
    const { puts } = await captureWrites(page, 'npu')
    await seedSlots(page, [{ ...NPU_SLOT, llamacpp_args: '--foo "bar' }])

    await page.goto('/#slots/npu')
    await page.getByTestId('slot-hw-threads').fill('8')
    await page.locator('.drawer button:has-text("Save")').click()

    await expect.poll(() => puts.length).toBe(1)
    expect(puts[0]).toHaveProperty('threads', 8)
    // The untouched, unsurfaced extra_args never rides along.
    expect(puts[0]).not.toHaveProperty('server')
  })

  test('a malformed extra_args the operator CAN see still blocks, and says why', async ({ page }) => {
    // On a device where the field is rendered, the guard must keep working —
    // and the reason must be on screen.
    const { puts } = await captureWrites(page, 'primary')
    await seedSlots(page, [{ ...GPU_SLOT, llamacpp_args: '--threads 6' }])

    await page.goto('/#slots/primary')
    await page.getByTestId('extra-args-input').fill('--foo "bar')
    await page.locator('.drawer button:has-text("Save")').click()

    await expect(page.locator('.drawer')).toContainText('Unbalanced quote')
    await page.waitForTimeout(250)
    expect(puts).toEqual([])
    // Drawer stays open so the operator can correct it in place.
    await expect(page.locator('.drawer')).toHaveCount(1)
  })

  test('a Save blocked by an off-screen field surfaces the reason in the footer', async ({ page }) => {
    // Set an invalid value while the field is visible, then switch Device to
    // npu — which unmounts the row AND its inline error. Save is still
    // blocked; the operator must be told, not left with a dead button.
    const { puts } = await captureWrites(page, 'primary')
    await seedSlots(page, [{ ...GPU_SLOT, llamacpp_args: '--threads 6' }])

    await page.goto('/#slots/primary')
    await page.getByTestId('extra-args-input').fill('--foo "bar')
    await page.getByTestId('slot-hw-device').selectOption('npu')
    await expect(page.getByTestId('extra-args-input')).toHaveCount(0)

    await page.locator('.drawer button:has-text("Save")').click()

    await expect(page.getByTestId('slot-save-blocked')).toBeVisible()
    await expect(page.getByTestId('slot-save-blocked')).toContainText('Unbalanced quote')
    await page.waitForTimeout(250)
    expect(puts).toEqual([])
  })

  test('the blocked-save notice clears once the value is valid again', async ({ page }) => {
    const { puts } = await captureWrites(page, 'primary')
    await seedSlots(page, [{ ...GPU_SLOT, llamacpp_args: '--threads 6' }])

    await page.goto('/#slots/primary')
    await page.getByTestId('extra-args-input').fill('--foo "bar')
    await page.locator('.drawer button:has-text("Save")').click()
    await expect(page.getByTestId('slot-save-blocked')).toBeVisible()

    await page.getByTestId('extra-args-input').fill('--foo bar')
    await expect(page.getByTestId('slot-save-blocked')).toHaveCount(0)

    await page.locator('.drawer button:has-text("Save")').click()
    await expect.poll(() => puts.length).toBe(1)
    expect(puts[0].server).toEqual({ extra_args: '--foo bar' })
  })
})
