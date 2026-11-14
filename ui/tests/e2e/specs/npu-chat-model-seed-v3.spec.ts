/**
 * npu-chat-model-seed-v3 — the FLM lane's chat tag comes from the CONFIGURED default, not the
 * live `model_id` (#1388).
 *
 * `useSlots.ts` documents `model_id` as stale for exactly this slot class (trio slots never load
 * as their own process, so it never reconciles off the pre-trio GGUF); the configured tag is
 * `[model].default`. Flipping ASR or Embed — controls with no business touching the chat model —
 * must never rewrite `[model].default` to an unrelated id. These cases drove the Slots drawer's
 * capability matrix until the matrix moved to the NPU tab's FLM lane (final-review item 4); the
 * lane stages toggles and applies once, so each case stages, applies and reads the one PUT.
 */
import { test, expect, type Page } from '../fixtures/apiMock'

/** The tag the operator configured, in the slot TOML. */
const CONFIGURED = 'qwen3:8b'
/** What the live runtime reports — stale for a trio slot, never reconciled. */
const STALE_LIVE = 'qwen2.5-7b-instruct-Q4'

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
  metrics: { ctx: 8192 },
  // The drift this bug is about: live id ≠ configured default.
  model: STALE_LIVE,
  model_id: STALE_LIVE,
  model_default: CONFIGURED,
  npu: { chat: true, asr: false, embed: false },
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

/** The anchor's saved config (GET) and every PUT body (captured). */
async function captureConfig(page: Page, saved: any) {
  const puts: any[] = []
  await page.route('**/api/slots/npu/config', async (route) => {
    if (route.request().method() === 'PUT') {
      puts.push(route.request().postDataJSON())
      return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
    }
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(saved) })
  })
  await page.route('**/api/slots/npu/restart', (route) =>
    route.fulfill({ status: 202, contentType: 'application/json', body: '{}' }),
  )
  return puts
}

async function stubFlm(page: Page, models: any[]) {
  await page.route('**/api/slots/flm/models', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ models }),
    }),
  )
}

const SAVED = { name: 'npu', npu: { chat: true, asr: false, embed: false }, model: { default: CONFIGURED } }

test.describe('FLM lane chat-model seeding (#1388)', () => {
  test('an ASR toggle never rewrites the configured chat tag', async ({ page }) => {
    const puts = await captureConfig(page, SAVED)
    await stubFlm(page, [{ model: CONFIGURED, installed: true, capabilities: ['chat'] }])
    await seedSlots(page, [NPU_SLOT])

    await page.goto('/#slots/npu')
    await page.getByTestId('flm-cap-asr').click()
    await page.getByTestId('flm-apply').click()

    await expect.poll(() => puts.length).toBe(1)
    expect(puts[0].npu).toEqual({ chat: true, asr: true, embed: false })
    // The whole point: the stale live id must never reach the wire.
    expect(JSON.stringify(puts[0])).not.toContain(STALE_LIVE)
    expect(puts[0].model).toEqual({ default: CONFIGURED })
  })

  test('an Embed toggle never rewrites the configured chat tag', async ({ page }) => {
    const puts = await captureConfig(page, SAVED)
    await stubFlm(page, [{ model: CONFIGURED, installed: true, capabilities: ['chat'] }])
    await seedSlots(page, [NPU_SLOT])

    await page.goto('/#slots/npu')
    await page.getByTestId('flm-cap-embed').click()
    await page.getByTestId('flm-apply').click()

    await expect.poll(() => puts.length).toBe(1)
    expect(puts[0].npu).toEqual({ chat: true, asr: false, embed: true })
    expect(JSON.stringify(puts[0])).not.toContain(STALE_LIVE)
    expect(puts[0].model).toEqual({ default: CONFIGURED })
  })

  test('the chat lane displays the configured tag, not the live id', async ({ page }) => {
    await captureConfig(page, SAVED)
    await stubFlm(page, [
      { model: CONFIGURED, installed: true, capabilities: ['chat'] },
      { model: 'flm-other', installed: true, capabilities: ['chat'] },
    ])
    await seedSlots(page, [NPU_SLOT])

    await page.goto('/#slots/npu')
    // An operator must be able to see what an apply is about to send.
    await expect(page.getByTestId('flm-chat-model')).toHaveValue(CONFIGURED)
  })

  test('an explicit chat-model pick still wins over the seed', async ({ page }) => {
    const puts = await captureConfig(page, SAVED)
    await stubFlm(page, [
      { model: CONFIGURED, installed: true, capabilities: ['chat'] },
      { model: 'flm-other', installed: true, capabilities: ['chat'] },
    ])
    await seedSlots(page, [NPU_SLOT])

    await page.goto('/#slots/npu')
    await page.getByTestId('flm-chat-model').selectOption('flm-other')
    await page.getByTestId('flm-apply').click()

    await expect.poll(() => puts.length).toBe(1)
    expect(puts[0].model).toEqual({ default: 'flm-other' })
  })

  test('falls back to the live id when no configured default exists', async ({ page }) => {
    // A slot with no [model].default on disk has nothing better to offer; the fallback chain must
    // not regress to a constant while a real live id is available.
    const puts = await captureConfig(page, { name: 'npu', npu: { chat: true, asr: false, embed: false } })
    await stubFlm(page, [{ model: STALE_LIVE, installed: true, capabilities: ['chat'] }])
    await seedSlots(page, [{ ...NPU_SLOT, model_default: undefined }])

    await page.goto('/#slots/npu')
    await page.getByTestId('flm-cap-asr').click()
    await page.getByTestId('flm-apply').click()

    await expect.poll(() => puts.length).toBe(1)
    expect(puts[0].model).toEqual({ default: STALE_LIVE })
  })
})
