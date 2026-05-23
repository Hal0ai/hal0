/**
 * first-run-bundle-picker.spec.ts — γ First-run bundle picker (PR-17).
 *
 * Covers the bundle picker UX (ADR-0010 / plan §8):
 *   - All five tiers render in order (4 hal0 + 1 LMX kit)
 *   - RAM-ineligible tiers are greyed + carry a `title` tooltip
 *   - Tier select → modal → confirm fires POST /api/bundles/{name}
 *   - NPU opt-in checkbox surfaces only on Pro/Max + rides POST body
 *   - "Skip — configure manually" fires GET /api/bundles/skip + leaves
 *
 * The router's bundle guard sees `picker_pending: true` on / and
 * redirects to /bundles. The install wizard is mocked as completed so
 * the firstrun guard doesn't fire.
 */
import { test, expect, json } from '../fixtures/apiMock'

// Bundle row shapes returned by GET /api/bundles. Matches the
// schema.py + tiers.py output verbatim so a future shape drift is
// surfaced as a spec failure.
const TIERS = [
  {
    name: 'hal0-Lite',
    min_ram_gb: 16,
    primary: { slot: 'chat.primary', model_name: 'qwen3.5-0.8b', size_gb: 1.0, lru: false },
    coder: null,
    aux: [],
    npu_trio_shown: false,
    npu_trio_optin: false,
    display_label: 'hal0-Lite',
    display_subtitle: 'Minimal chat — fits any 16 GB+ box',
    vendor: 'hal0',
    total_size_gb: 1.0,
  },
  {
    name: 'hal0-Default',
    min_ram_gb: 32,
    primary: { slot: 'chat.primary', model_name: 'qwen3.5-9b', size_gb: 6.9, lru: false },
    coder: null,
    aux: [
      { slot: 'embed', model_name: 'nomic-v1.5', size_gb: 0.3, lru: false },
      { slot: 'stt', model_name: 'whisper-tiny', size_gb: 0.075, lru: false },
      { slot: 'tts', model_name: 'kokoro:cpu', size_gb: 0.35, lru: false },
    ],
    npu_trio_shown: false,
    npu_trio_optin: false,
    display_label: 'hal0-Default',
    display_subtitle: 'Balanced chat + embed + voice for 32 GB+ boxes',
    vendor: 'hal0',
    total_size_gb: 7.625,
  },
  {
    name: 'hal0-Pro',
    min_ram_gb: 64,
    primary: { slot: 'chat.primary', model_name: 'Qwen3.6-27B-MTP', size_gb: 18.8, lru: false },
    coder: { slot: 'chat.coder', model_name: 'Qwen3-Coder-30B-A3B', size_gb: 18.6, lru: true },
    aux: [
      { slot: 'embed', model_name: 'nomic-v1.5', size_gb: 0.3, lru: false },
      { slot: 'rerank', model_name: 'bge-reranker-v2-m3', size_gb: 0.45, lru: false },
      { slot: 'stt', model_name: 'whisper-base', size_gb: 0.15, lru: false },
      { slot: 'tts', model_name: 'kokoro:cpu', size_gb: 0.35, lru: false },
      { slot: 'img', model_name: 'sd-turbo', size_gb: 1.4, lru: true },
    ],
    npu_trio_shown: true,
    npu_trio_optin: false,
    display_label: 'hal0-Pro',
    display_subtitle: 'Chat + coder + embed/rerank + STT + TTS + image, 64 GB+',
    vendor: 'hal0',
    total_size_gb: 40.05,
  },
  {
    name: 'hal0-Max',
    min_ram_gb: 100,
    primary: { slot: 'chat.primary', model_name: 'Qwen3.6-35B-A3B-MTP', size_gb: 23.8, lru: false },
    coder: { slot: 'chat.coder', model_name: 'Qwen3-Coder-Next-80B-A3B', size_gb: 48.0, lru: true },
    aux: [
      { slot: 'embed', model_name: 'nomic-v1.5', size_gb: 0.3, lru: false },
      { slot: 'rerank', model_name: 'bge-reranker-v2-m3', size_gb: 0.45, lru: false },
      { slot: 'stt', model_name: 'whisper-large-v3-turbo', size_gb: 1.6, lru: false },
      { slot: 'tts', model_name: 'kokoro:cpu', size_gb: 0.35, lru: false },
      { slot: 'img', model_name: 'flux-2-klein-9b', size_gb: 9.0, lru: true },
    ],
    npu_trio_shown: true,
    npu_trio_optin: false,
    display_label: 'hal0-Max',
    display_subtitle: 'Top-of-line chat + coder + voice + Flux image, 100 GB+ Strix Halo',
    vendor: 'hal0',
    total_size_gb: 83.5,
  },
  {
    name: 'LMX-Omni-52B-Halo',
    min_ram_gb: 100,
    primary: { slot: 'chat.primary', model_name: 'Qwen3.6-35B-A3B-MTP', size_gb: 23.8, lru: false },
    coder: null,
    aux: [
      { slot: 'stt', model_name: 'Whisper-Large-v3-Turbo', size_gb: 1.6, lru: false },
      { slot: 'tts', model_name: 'kokoro-v1', size_gb: 0.35, lru: false },
      { slot: 'img', model_name: 'Flux-2-Klein-9B', size_gb: 9.0, lru: false },
    ],
    npu_trio_shown: false,
    npu_trio_optin: false,
    display_label: 'LMX-Omni-52B-Halo',
    display_subtitle: 'AMD-curated kit — vendor-blessed Strix Halo bundle',
    vendor: 'amd',
    total_size_gb: 34.75,
  },
]

function bundlesPayload({ ramGb = 128, picker_pending = true, choice = null } = {}) {
  const eligible = TIERS.filter((t) => t.min_ram_gb <= ramGb).map((t) => t.name)
  return {
    host_ram_gb: ramGb,
    tiers: TIERS,
    eligible,
    choice,
    picker_pending,
  }
}

test.beforeEach(async ({ page, mockState }) => {
  // Install wizard already complete (so the firstrun guard doesn't
  // intercept). The bundle picker is what we're exercising.
  mockState.installState.first_run = false
})

test('shows all five bundle cards in order', async ({ page, cleanState: _ }) => {
  await page.route('**/api/bundles', (route) => json(route, bundlesPayload({ ramGb: 128 })))

  await page.goto('/')
  await expect(page).toHaveURL(/\/bundles$/)
  await expect(page.getByText('Welcome to hal0')).toBeVisible()

  // 4 hal0-curated cards.
  for (const name of ['hal0-Lite', 'hal0-Default', 'hal0-Pro', 'hal0-Max']) {
    await expect(page.locator(`.tier-card[data-tier-name="${name}"]`)).toBeVisible()
  }
  // 1 vendor kit card.
  await expect(page.locator('.tier-card[data-tier-name="LMX-Omni-52B-Halo"]')).toBeVisible()
  await expect(page.getByText('Pre-built kits')).toBeVisible()
})

test('greys out RAM-ineligible tiers and renders a tooltip', async ({ page, cleanState: _ }) => {
  await page.route('**/api/bundles', (route) => json(route, bundlesPayload({ ramGb: 32 })))

  await page.goto('/bundles')

  const lite = page.locator('.tier-card[data-tier-name="hal0-Lite"]')
  const max = page.locator('.tier-card[data-tier-name="hal0-Max"]')

  // Lite fits 32 GB and stays clickable.
  await expect(lite).toHaveAttribute('data-tier-eligible', 'true')
  await expect(lite).toBeEnabled()
  // Max needs 100 GB and is disabled with an explanatory tooltip.
  await expect(max).toHaveAttribute('data-tier-eligible', 'false')
  await expect(max).toBeDisabled()
  await expect(max).toHaveAttribute(
    'title',
    /Needs 100 GB; this host has ~32 GB\./,
  )
})

test('tier select → modal → confirm fires POST /api/bundles/{name}', async ({ page, cleanState: _ }) => {
  // Mutable so the picker-guard flips to "done" after the POST lands,
  // letting the dashboard redirect resolve cleanly.
  let pickerPending = true
  await page.route('**/api/bundles', (route) =>
    json(route, bundlesPayload({ ramGb: 128, picker_pending: pickerPending })),
  )

  let postedTo: string | null = null
  let postedBody: any = null
  await page.route('**/api/bundles/hal0-Default', (route) => {
    if (route.request().method() === 'POST') {
      postedTo = route.request().url()
      try { postedBody = JSON.parse(route.request().postData() || '{}') } catch { postedBody = null }
      pickerPending = false
      return json(route, {
        ok: true,
        choice: { name: 'hal0-Default', npu_opt_in: false, skipped: false, chosen_at: '2026-05-23T00:00:00+00:00', assignments: [] },
        manifest: TIERS[1],
        applied: [],
      })
    }
    return route.fallback()
  })

  await page.goto('/bundles')
  await page.locator('.tier-card[data-tier-name="hal0-Default"]').click()

  // Modal renders with the bundle contents + total size.
  await expect(page.getByRole('dialog')).toBeVisible()
  await expect(page.locator('.picker-modal-title')).toContainText('hal0-Default')
  await expect(page.locator('.picker-modal-row', { hasText: 'qwen3.5-9b' })).toBeVisible()
  await expect(page.locator('.picker-modal-row', { hasText: 'whisper-tiny' })).toBeVisible()

  await page.locator('[data-confirm-bundle]').click()

  // POST went through with an empty body (no NPU on Default).
  await expect.poll(() => postedTo).toContain('/api/bundles/hal0-Default')
  expect(postedBody).toEqual({})

  // Router replaces us back to the dashboard once confirm resolves.
  await expect(page).not.toHaveURL(/\/bundles$/, { timeout: 5_000 })
})

test('Skip — configure manually fires GET /api/bundles/skip and redirects', async ({ page, cleanState: _ }) => {
  let pickerPending = true
  await page.route('**/api/bundles', (route) =>
    json(route, bundlesPayload({ ramGb: 16, picker_pending: pickerPending })),
  )

  let skipped = false
  await page.route('**/api/bundles/skip', (route) => {
    skipped = true
    pickerPending = false
    return json(route, {
      ok: true,
      choice: { name: '', npu_opt_in: false, skipped: true, chosen_at: '2026-05-23T00:00:00+00:00', assignments: [] },
    })
  })

  await page.goto('/bundles')
  await page.locator('[data-skip-bundle]').click()

  await expect.poll(() => skipped).toBe(true)
  await expect(page).not.toHaveURL(/\/bundles$/, { timeout: 5_000 })
})

test('NPU opt-in checkbox surfaces only on Pro and rides POST body', async ({ page, cleanState: _ }) => {
  await page.route('**/api/bundles', (route) => json(route, bundlesPayload({ ramGb: 128 })))

  let postedBody: any = null
  await page.route('**/api/bundles/hal0-Pro', (route) => {
    if (route.request().method() === 'POST') {
      try { postedBody = JSON.parse(route.request().postData() || '{}') } catch { postedBody = null }
      return json(route, {
        ok: true,
        choice: { name: 'hal0-Pro', npu_opt_in: true, skipped: false, chosen_at: '2026-05-23T00:00:00+00:00', assignments: [] },
        manifest: TIERS[2],
        applied: [],
      })
    }
    return route.fallback()
  })

  await page.goto('/bundles')
  await page.locator('.tier-card[data-tier-name="hal0-Pro"]').click()

  // The NPU checkbox is present on Pro …
  const npuBox = page.locator('[data-npu-opt-in]')
  await expect(npuBox).toBeVisible()
  await npuBox.check()
  await page.locator('[data-confirm-bundle]').click()

  await expect.poll(() => postedBody).toEqual({ npu_opt_in: true })
})

test('NPU opt-in checkbox is absent on tiers that do not expose the trio', async ({ page, cleanState: _ }) => {
  await page.route('**/api/bundles', (route) => json(route, bundlesPayload({ ramGb: 128 })))

  await page.goto('/bundles')
  await page.locator('.tier-card[data-tier-name="hal0-Default"]').click()
  await expect(page.getByRole('dialog')).toBeVisible()
  await expect(page.locator('[data-npu-opt-in]')).toHaveCount(0)
})
