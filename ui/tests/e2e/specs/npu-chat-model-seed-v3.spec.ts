/**
 * npu-chat-model-seed-v3 — the NPU chat tag comes from the CONFIGURED default,
 * not the live `model_id` (#1388).
 *
 * Every NPU modality toggle routes through `applyNpu`, which attaches
 * `model.default` alongside the `npu` table. It sourced that tag from
 * `slot.model_id` — which `useSlots.ts` documents as *stale for exactly this
 * slot class*:
 *
 *   "the intended FLM tag even when the live `model_id` is stale on the
 *    pre-trio GGUF (trio slots never load as their own process, so `model_id`
 *    never reconciles)"
 *
 * …and for which it already exposes the correct value as `modelDefault`
 * (normalised from `model_default`, which `config_enrichment` lifts from the
 * slot TOML). The drawer never read it.
 *
 * Consequence: flipping ASR or Embed — controls with no business touching the
 * chat model at all — rewrote `[model].default` to an unrelated GGUF id and
 * cold-restarted the slot. Silent config corruption on NPU boxes.
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

async function captureConfig(page: Page) {
  const puts: any[] = []
  await page.route('**/api/slots/npu/config', async (route) => {
    if (route.request().method() === 'PUT') puts.push(route.request().postDataJSON())
    await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
  })
  await page.route('**/api/slots/npu/defaults', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: '{}' }),
  )
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

const lane = (page: Page, label: string) =>
  page.locator('.drawer .form-row', { hasText: label })

test.describe('NPU chat-model seeding (#1388)', () => {
  test('an ASR toggle never rewrites the configured chat tag', async ({ page }) => {
    const puts = await captureConfig(page)
    await stubFlm(page, [{ model: CONFIGURED, installed: true }])
    await seedSlots(page, [NPU_SLOT])

    await page.goto('/#slots/npu')
    await lane(page, 'NPU · ASR').getByRole('switch').click()

    await expect.poll(() => puts.length).toBe(1)
    expect(puts[0].npu).toEqual({ chat: true, asr: true, embed: false })
    // The whole point: the stale live id must never reach the wire.
    expect(JSON.stringify(puts[0])).not.toContain(STALE_LIVE)
    if (puts[0].model !== undefined) {
      expect(puts[0].model).toEqual({ default: CONFIGURED })
    }
  })

  test('an Embed toggle never rewrites the configured chat tag', async ({ page }) => {
    const puts = await captureConfig(page)
    await stubFlm(page, [{ model: CONFIGURED, installed: true }])
    await seedSlots(page, [NPU_SLOT])

    await page.goto('/#slots/npu')
    await lane(page, 'NPU · Embed').getByRole('switch').click()

    await expect.poll(() => puts.length).toBe(1)
    expect(puts[0].npu).toEqual({ chat: true, asr: false, embed: true })
    expect(JSON.stringify(puts[0])).not.toContain(STALE_LIVE)
  })

  test('the chat lane displays the configured tag, not the live id', async ({ page }) => {
    await captureConfig(page)
    await stubFlm(page, [
      { model: CONFIGURED, installed: true },
      { model: 'flm-other', installed: true },
    ])
    await seedSlots(page, [NPU_SLOT])

    await page.goto('/#slots/npu')
    // An operator must be able to see what a toggle is about to send.
    await expect(lane(page, 'NPU · Chat').locator('select')).toHaveValue(CONFIGURED)
  })

  test('an explicit chat-model pick still wins over the seed', async ({ page }) => {
    const puts = await captureConfig(page)
    await stubFlm(page, [
      { model: CONFIGURED, installed: true },
      { model: 'flm-other', installed: true },
    ])
    await seedSlots(page, [NPU_SLOT])

    await page.goto('/#slots/npu')
    await lane(page, 'NPU · Chat').locator('select').selectOption('flm-other')

    await expect.poll(() => puts.length).toBe(1)
    expect(puts[0].model).toEqual({ default: 'flm-other' })
  })

  test('falls back to the live id when no configured default exists', async ({ page }) => {
    // A slot with no [model].default on disk has nothing better to offer;
    // the fallback chain must not regress to the "qwen3:4b" constant while a
    // real live id is available.
    const puts = await captureConfig(page)
    await stubFlm(page, [{ model: STALE_LIVE, installed: true }])
    await seedSlots(page, [{ ...NPU_SLOT, model_default: undefined }])

    await page.goto('/#slots/npu')
    await lane(page, 'NPU · ASR').getByRole('switch').click()

    await expect.poll(() => puts.length).toBe(1)
    if (puts[0].model !== undefined) {
      expect(puts[0].model).toEqual({ default: STALE_LIVE })
    }
  })
})
