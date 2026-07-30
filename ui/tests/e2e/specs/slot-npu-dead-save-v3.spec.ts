/**
 * slot-npu-dead-save-v3 — #1389: Save must not be silently dead on an NPU
 * slot whose PERSISTED extra_args is malformed.
 *
 * The freeform extra_args field (and its error surface) lives in the Model
 * group, which is unmounted for `device === "npu"`. The Save validator still
 * seeded `extraArgsErr` from the persisted `llamacpp_args`, so a malformed
 * persisted value blocked every Save on the slot with zero feedback — the
 * error rendered into an unmounted subtree and no request ever fired.
 *
 * Contract: a validation error on a field the operator cannot see or edit
 * must not veto the batched Save of unrelated, visible fields.
 */
import { test, expect, type Page } from '../fixtures/apiMock'

const NPU = {
  name: 'agent', type: 'llm', device: 'npu', profile: '',
  model: 'qwen3:8b', model_id: 'qwen3:8b', modelLong: 'qwen3:8b',
  modelDefault: 'qwen3:8b', model_default: 'qwen3:8b',
  group: 'chat', state: 'ready', port: 8083, isDefault: false,
  n_gpu_layers: -1, threads: 0,
  // Persisted malformed override (unbalanced quote) — the repro's trigger.
  llamacpp_args: '--flash-attn "oops',
  npu: { chat: true, asr: false, embed: false },
  metrics: {},
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

test.describe('NPU slot Save with malformed persisted extra_args (#1389)', () => {
  test('N1 — Save of a visible field is not vetoed by the unmounted extra_args error', async ({ page }) => {
    const puts: any[] = []
    await page.route('**/api/slots/agent/config', async (route) => {
      if (route.request().method() === 'PUT')
        puts.push(JSON.parse(route.request().postData() || '{}'))
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
    })
    await page.route('**/api/slots/agent/restart', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: '{}' }),
    )
    await seedSlots(page, [NPU])
    await page.goto('/#slots/agent')
    await expect(page.locator('.drawer')).toBeVisible()

    await page.getByTestId('slot-hw-threads').fill('4')
    await page.locator('.drawer button:has-text("Save")').click()

    await expect.poll(() => puts.length, { timeout: 5000 }).toBeGreaterThan(0)
    expect(puts[0]).toMatchObject({ threads: 4 })
    // The phantom error must not ride the write either.
    expect(puts[0]).not.toHaveProperty('server')
  })
})
