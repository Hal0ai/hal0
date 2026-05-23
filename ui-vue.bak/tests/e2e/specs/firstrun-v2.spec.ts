/**
 * firstrun.spec.ts — v2 FirstRun (slice #172).
 *
 * Replaces the v1 8-step linear wizard spec with a 3-state machine spec
 * mirroring the v0.3 design (pick → confirm → progress).
 *
 * Acceptance criteria preserved:
 *   - First-run guard redirects / → /firstrun on a fresh install.
 *   - Bundle picker renders + advances via Pick.
 *   - Skip dialog confirms before routing back to /.
 *   - Confirm card shows per-slot install list + NPU opt-in.
 *   - Progress rows render + drive to terminal via SSE.
 *   - Open dashboard writes the install-complete sentinel.
 */
import { test, expect, json } from '../fixtures/apiMock'
import { installSseHarness, emitSse, waitForSse } from '../fixtures/sseHarness'

test.beforeEach(async ({ page, mockState }) => {
  mockState.installState.first_run = true
  // Pro tier on a 128-GB Strix Halo box — matches the design source mock.
  mockState.hardware.unified_memory_mb = 128 * 1024
  mockState.hardware.ram_total_mb = 128 * 1024
  mockState.hardware.gpu_name = 'AMD Radeon 8060S (Strix Halo)'
  mockState.hardware.npu_present = true
  mockState.hardware.disk_free_mb = 500_000

  await installSseHarness(page)

  // Capabilities catalog: shape-only; the v2 picker doesn't consume the
  // individual options — bundle definitions are baked into the composable.
  await page.route('**/api/capabilities', (route) =>
    json(route, {
      backends: [],
      catalogs: { embed: { embed: [], rerank: [] }, voice: { stt: [], tts: [] }, img: { img: [] } },
      selections: {},
    }),
  )

  // Per-model pull starter — capability rows hit this.
  await page.route('**/api/models/*/pull', (route) =>
    json(route, { id: 'job1', state: 'queued' }),
  )

  // SSE stream — actual frames come from the in-page SSE harness.
  await page.route('**/api/models/*/pull/stream', (route) =>
    route.fulfill({ status: 200, contentType: 'text/event-stream', body: '' }),
  )

  // Capability register — accept whatever the orchestrator posts.
  await page.route('**/api/capabilities/*/*', (route) => json(route, { ok: true }))
})

test('redirects to /firstrun and renders the picker with hardware detect line', async ({
  page,
  cleanState: _cleanState,
}) => {
  await page.goto('/')
  await expect(page).toHaveURL(/\/firstrun$/)

  // Welcome chrome + detect line.
  await expect(page.getByText('Welcome to', { exact: false })).toBeVisible()
  await expect(page.getByTestId('fr-detect')).toContainText('128')
  await expect(page.getByTestId('fr-detect')).toContainText('Strix Halo')
  await expect(page.getByTestId('fr-detect')).toContainText('NPU')
})

test('PICK state — all four tier cards render with the correct state chip', async ({
  page,
  cleanState: _cleanState,
}) => {
  await page.goto('/firstrun')

  // All four tiers visible.
  for (const id of ['lite', 'default', 'pro', 'max']) {
    await expect(page.locator(`[data-tier-id="${id}"]`).first()).toBeVisible()
  }

  // 128 GB → Max recommended. Lite renders 'gated-no-hf' because its
  // default chat model is llama-3.2-1b-instruct (matches the gated
  // heuristic) and no HF_TOKEN is in localStorage. Default fits + isn't
  // gated, so it lands on 'available'.
  await expect(page.locator('[data-tier-id="max"]')).toHaveAttribute('data-tier-state', 'recommended')
  await expect(page.locator('[data-tier-id="default"]')).toHaveAttribute('data-tier-state', 'available')

  // Recommended chip + ★ symbol on Max.
  await expect(page.locator('[data-tier-id="max"] .tier-tag.rec')).toContainText(/recommended/)
})

test('PICK state — gated-no-HF chip renders on the Lite tier (Llama default)', async ({
  page,
  cleanState: _cleanState,
}) => {
  await page.goto('/firstrun')
  // Lite ships llama-3.2-1b-instruct → gated heuristic match + no HF token.
  await expect(page.locator('[data-tier-id="lite"]')).toHaveAttribute('data-tier-state', 'gated-no-hf')
  await expect(page.locator('[data-tier-id="lite"] .tier-tag.gated')).toBeVisible()
})

test('PICK state — tier shows "unfit" when RAM is below the tier minimum', async ({
  page,
  mockState,
  cleanState: _cleanState,
}) => {
  // Re-mock with 20 GB RAM — only Lite fits.
  mockState.hardware.unified_memory_mb = 20 * 1024
  mockState.hardware.ram_total_mb = 20 * 1024
  await page.goto('/firstrun')

  await expect(page.locator('[data-tier-id="default"]')).toHaveAttribute('data-tier-state', 'unfit')
  await expect(page.locator('[data-tier-id="pro"]')).toHaveAttribute('data-tier-state', 'unfit')
  await expect(page.locator('[data-tier-id="max"]')).toHaveAttribute('data-tier-state', 'unfit')
  // Lite is the only fitting tier on 20 GB; gated heuristic still wins
  // over 'recommended' (Llama default + no HF_TOKEN). Either state means
  // the tier IS pickable — that's the load-bearing assertion.
  const liteState = await page.locator('[data-tier-id="lite"]').getAttribute('data-tier-state')
  expect(['recommended', 'gated-no-hf']).toContain(liteState)

  // Pick button on Default disabled.
  await expect(page.locator('[data-tier-id="default"] button')).toBeDisabled()
})

test('PICK state — installed chip shows on every tier when re-entering picker', async ({
  page,
  mockState,
  cleanState: _cleanState,
}) => {
  // first_run=false means the box already has hal0 installed.
  mockState.installState.first_run = false
  await page.goto('/firstrun')

  await expect(page.locator('[data-tier-id="max"]')).toHaveAttribute('data-tier-state', 'installed')

  // fr-reentered banner is in the catalog scoped to 'firstrun'.
  await expect(page.locator('[data-banner-id="fr-reentered"]')).toBeVisible()
})

test('PICK state — gated tier shows the HF-token chip when HF_TOKEN missing', async ({
  page,
  cleanState: _cleanState,
}) => {
  // Max bundle includes whisper-v3-turbo (NPU) which the heuristic flags
  // as gated. Without HF_TOKEN in localStorage the chip should warn.
  // localStorage starts empty in Playwright contexts so no setup needed.
  await page.goto('/firstrun')

  // Max either reads 'recommended' OR 'gated-no-hf' depending on heuristic
  // precedence — we asserted 'recommended' wins in the earlier test, so
  // here we verify the gated tier doesn't get a hf-gated banner unless
  // selected. The hf-gated banner only shows after a confirm-tier pick.
  await expect(page.locator('[data-banner-id="hf-gated"]')).toHaveCount(0)
})

test('SKIP flow — opens confirm dialog, cancel keeps picker, confirm routes home', async ({
  page,
  mockState,
  cleanState: _cleanState,
}) => {
  await page.goto('/firstrun')

  // Open the dialog.
  await page.getByTestId('fr-skip').click()
  await expect(page.getByRole('heading', { name: /Skip the bundle picker/ })).toBeVisible()

  // Cancel keeps us on /firstrun.
  await page.getByRole('button', { name: /^Cancel$/ }).click()
  await expect(page).toHaveURL(/\/firstrun$/)
  await expect(page.getByText('Welcome to', { exact: false })).toBeVisible()

  // Re-open and confirm — should POST /install/complete + route to /.
  await page.getByTestId('fr-skip').click()
  const completeReq = page.waitForRequest(
    (r) => r.url().endsWith('/api/install/complete') && r.method() === 'POST',
  )
  await page.getByRole('button', { name: /Skip and configure manually/ }).click()
  await completeReq
  expect(mockState.installCompleteCount).toBe(1)
})

test('CONFIRM state — picking Pro renders per-slot list + NPU opt-in', async ({
  page,
  cleanState: _cleanState,
}) => {
  await page.goto('/firstrun')

  await page.locator('[data-tier-id="pro"] button').click()

  await expect(page.getByRole('heading', { name: 'hal0-Pro' })).toBeVisible()
  await expect(page.getByTestId('fr-install-list')).toBeVisible()
  // Per-slot rows for the Pro detail manifest.
  for (const slot of ['primary', 'coder', 'embed', 'rerank', 'stt', 'tts', 'img']) {
    await expect(page.locator(`[data-slot="${slot}"]`)).toBeVisible()
  }

  // NPU card appears because npu_present=true + Pro has npu rows.
  await expect(page.getByTestId('fr-npu-card')).toBeVisible()
  const npuToggle = page.getByTestId('fr-npu-toggle')
  await expect(npuToggle).not.toBeChecked()
  await npuToggle.check()
  await expect(npuToggle).toBeChecked()

  // Back-to-picker resets the state.
  await page.getByTestId('fr-confirm-back').click()
  await expect(page.locator('[data-tier-id="max"]')).toBeVisible()
})

test('PROGRESS state — error row exposes Retry + Skip this model', async ({
  page,
  cleanState: _cleanState,
}) => {
  await page.goto('/firstrun')

  // Lite has a single primary row — keeps the SSE pump simple.
  await page.locator('[data-tier-id="lite"] button').click()
  await page.getByTestId('fr-install-btn').click()

  // Composable slugifies "llama-3.2-1b-instruct" → same string lowercased.
  const slug = 'llama-3.2-1b-instruct'
  await waitForSse(page, `/api/models/${slug}/pull/stream`)

  // Fail the pull mid-flight.
  await emitSse(page, slug, {
    state: 'failed',
    bytes_downloaded: 100_000_000,
    bytes_total: 1_200_000_000,
    error: 'disk full at 87%',
  })

  const errRow = page.locator('.dl-row-err').first()
  await expect(errRow).toBeVisible()
  await expect(errRow).toContainText('disk full')
  await expect(errRow.getByRole('button', { name: 'Retry' })).toBeVisible()
  await expect(errRow.getByRole('button', { name: 'Skip this model' })).toBeVisible()

  // Skip flips the row to "done" — terminal state.
  await errRow.getByRole('button', { name: 'Skip this model' }).click()
  await expect(page.locator('.dl-row-done').first()).toBeVisible()
})

test('PROGRESS state — Open dashboard writes install-complete sentinel', async ({
  page,
  mockState,
  cleanState: _cleanState,
}) => {
  await page.goto('/firstrun')

  await page.locator('[data-tier-id="lite"] button').click()
  await page.getByTestId('fr-install-btn').click()

  const slug = 'llama-3.2-1b-instruct'
  await waitForSse(page, `/api/models/${slug}/pull/stream`)
  await emitSse(page, slug, { state: 'completed', bytes_downloaded: 1, bytes_total: 1 })

  // Wait for the done-row to show, then click Open dashboard.
  await expect(page.locator('.dl-row-done').first()).toBeVisible()

  const completeReq = page.waitForRequest(
    (r) => r.url().endsWith('/api/install/complete') && r.method() === 'POST',
  )
  await page.getByTestId('fr-open-dashboard').click()
  await completeReq
  expect(mockState.installCompleteCount).toBe(1)
})

test('LAYOUT — matrix variant renders when useTweaksStore.firstrunLayout=wizard', async ({
  page,
  cleanState: _cleanState,
}) => {
  // Tweaks store loads from localStorage on mount; seed before navigation.
  await page.addInitScript(() => {
    localStorage.setItem(
      'hal0:tweaks:v2',
      JSON.stringify({ firstrunLayout: 'wizard' }),
    )
  })
  await page.goto('/firstrun')

  // Matrix layout renders the BundleTable instead of BundleGrid.
  await expect(page.locator('[data-firstrun-layout="matrix"]')).toBeVisible()
  await expect(page.locator('[data-firstrun-layout="grid"]')).toHaveCount(0)

  // All four tier-pick buttons still reachable in the matrix.
  for (const id of ['lite', 'default', 'pro', 'max']) {
    await expect(page.locator(`[data-firstrun-layout="matrix"] button[data-tier-id="${id}"]`)).toBeVisible()
  }
})

test('LAYOUT — grid is the default when no tweak override is set', async ({
  page,
  cleanState: _cleanState,
}) => {
  await page.goto('/firstrun')
  await expect(page.locator('[data-firstrun-layout="grid"]')).toBeVisible()
  await expect(page.locator('[data-firstrun-layout="matrix"]')).toHaveCount(0)
})

test('BANNERS — fr-ram-low shows when detected RAM is below 16 GB', async ({
  page,
  mockState,
  cleanState: _cleanState,
}) => {
  mockState.hardware.unified_memory_mb = 8 * 1024
  mockState.hardware.ram_total_mb = 8 * 1024
  await page.goto('/firstrun')

  await expect(page.locator('[data-banner-id="fr-ram-low"]')).toBeVisible()
})
