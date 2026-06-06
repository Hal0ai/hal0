/**
 * firstrun-v3 — `#firstrun` route drives the 3-state machine
 * (picker → confirm → progress). Tests cover:
 *   1. picker smoke (tier cards, skip link, detect bar)
 *   2. progress pane — live SSE rows via FrDownloadRow / usePullJob
 *
 * SSE pattern mirrors footer-journal-pane-v3.spec.ts: installSseHarness
 * replaces window.EventSource with FakeEventSource, then emitSseTyped
 * dispatches typed pull.* events (progress / completed / failed) on the
 * per-model /api/models/{id}/pull/stream URL.
 *
 * The progress pane is reached by mounting FirstRunProgress directly via
 * window.FirstRunProgress (exposed in firstrun.jsx Object.assign). This
 * sidesteps the picker → storage → confirm transition so the spec stays
 * focused and avoids the test.fixme multi-step flow.
 */
import { test, expect } from '../fixtures/apiMock'
import { installSseHarness, emitSseTyped, waitForSse } from '../fixtures/sseHarness'

// ── Helpers ──────────────────────────────────────────────────────────

/** Mount FirstRunProgress directly via the window export.
 *  Wraps in Hal0QueryClientProvider so usePullJob's useQueryClient works. */
async function mountProgress(page: any, modelIds: string[]) {
  await page.evaluate((ids: string[]) => {
    const root = document.getElementById('root') || document.body
    root.innerHTML = '<div id="fr-test-mount"></div>'
    const el = document.getElementById('fr-test-mount')!
    const Comp = (window as any).FirstRunProgress
    if (!Comp) throw new Error('FirstRunProgress not on window')
    const QCP = (window as any).Hal0QueryClientProvider
    const qc  = (window as any).Hal0QueryClient
    const R   = (window as any).React
    ;(window as any).ReactDOM.createRoot(el).render(
      R.createElement(QCP, { client: qc },
        R.createElement(Comp, { bundleId: 'default', modelIds: ids, onDone: () => {} }),
      ),
    )
  }, modelIds)
}

// ── Tests ─────────────────────────────────────────────────────────────

test.describe('FirstRun v3 (/firstrun)', () => {
  test('picker (state 1) renders welcome + tier cards', async ({ page }) => {
    await page.goto('/#firstrun')
    await expect(page.locator('.fr-title')).toBeVisible()
    await expect(page.locator('.fr-title')).toContainText('hal0')
    // tier cards (grid layout default)
    const tiers = page.locator('.tier-card')
    expect(await tiers.count()).toBeGreaterThan(0)
    // skip link
    await expect(page.locator('.fr-skip')).toBeVisible()
  })

  test('host-detected RAM/GPU/NPU segments render', async ({ page }) => {
    await page.goto('/#firstrun')
    await expect(page.locator('.fr-detect')).toBeVisible()
    await expect(page.locator('.fr-detect .seg', { hasText: 'RAM' })).toBeVisible()
    await expect(page.locator('.fr-detect .seg', { hasText: 'GPU' })).toBeVisible()
    await expect(page.locator('.fr-detect .seg', { hasText: 'NPU' })).toBeVisible()
  })

  test.fixme('clicking a tier transitions to confirm (state 2)', async ({ page }) => {
    await page.goto('/#firstrun')
    // tier buttons inside `.unfit` cards are `disabled={!fits}` (firstrun.jsx:108,163);
    // pick the recommended tier — guaranteed to fit per recommendation logic.
    const firstTierBtn = page.locator('.tier-card button:not([disabled])').first()
    await firstTierBtn.click()
    // confirm card header
    await expect(page.locator('.fr-confirm-h')).toBeVisible({ timeout: 5000 })
  })

  // ── Progress pane — live SSE rows ──────────────────────────────────

  test.describe('progress pane (state 3) — live SSE rows', () => {
    const MODEL_A = 'nomic-v1.5'
    const MODEL_B = 'qwen3-coder-30b'

    test.beforeEach(async ({ page }) => {
      await installSseHarness(page)
      // Mock pull/status to return { state: 'queued' } so reattach() calls
      // attachStream and opens the per-model EventSource on mount.
      await page.route(/\/api\/models\/[^/]+\/pull\/status/, (route) =>
        route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ state: 'queued' }),
        }),
      )
    })

    test('empty modelIds shows graceful empty state (no HAL0_DATA mock rows)', async ({ page }) => {
      await page.goto('/#firstrun')
      await mountProgress(page, [])
      // Graceful placeholder — NOT the old mock row text.
      await expect(page.locator('.fr-prog-list')).toContainText('Install started')
      // Placeholder is one dl-row with no dl-name children (no real model rows).
      await expect(page.locator('.fr-prog-list .dl-name')).toHaveCount(0)
      // Confirm the old HAL0_DATA fixture text is absent from the production bundle.
      await expect(page.locator('text=Qwen3.6-27B-MTP-Q4_K_M.gguf')).toHaveCount(0)
    })

    test('modelIds renders one dl-row per model in queued/idle state', async ({ page }) => {
      await page.goto('/#firstrun')
      await mountProgress(page, [MODEL_A, MODEL_B])
      // Two rows mounted — one per model ID.
      await expect(page.locator('.fr-prog-list .dl-row')).toHaveCount(2)
      // Each row shows the model ID as the name.
      await expect(page.locator('.dl-name', { hasText: MODEL_A })).toBeVisible()
      await expect(page.locator('.dl-name', { hasText: MODEL_B })).toBeVisible()
    })

    test('progress SSE event updates bar and pct for a running model', async ({ page }) => {
      await page.goto('/#firstrun')
      await mountProgress(page, [MODEL_A])
      // FrDownloadRow calls reattach → opens EventSource for MODEL_A's pull stream.
      await waitForSse(page, `/api/models/${MODEL_A}/pull/stream`, 6_000)

      // Emit a progress event — typed "progress" matches usePullJob's listener.
      await emitSseTyped(page, `/api/models/${MODEL_A}/pull/stream`, 'progress', {
        state: 'running',
        bytes_downloaded: 500_000_000,
        bytes_total: 1_000_000_000,
        speed_bps: 10_485_760, // 10 MB/s
        eta_s: 47,
      })

      // pct label should show 50%.
      await expect(page.locator('.dl-pct', { hasText: '50%' })).toBeVisible({ timeout: 3_000 })
      // dl-bar fill is set via inline style width: "50%"
      const barFill = page.locator('.dl-bar i').first()
      await expect(barFill).toHaveAttribute('style', /width:\s*50%/)
    })

    test('completed SSE event marks row ok', async ({ page }) => {
      await page.goto('/#firstrun')
      await mountProgress(page, [MODEL_A])
      await waitForSse(page, `/api/models/${MODEL_A}/pull/stream`, 6_000)

      await emitSseTyped(page, `/api/models/${MODEL_A}/pull/stream`, 'completed', {
        state: 'completed',
        bytes_downloaded: 1_000_000_000,
        bytes_total: 1_000_000_000,
      })

      await expect(page.locator('.dl-pct.ok', { hasText: '✓ 100%' })).toBeVisible({ timeout: 3_000 })
      await expect(page.locator('.dl-state', { hasText: 'complete' })).toBeVisible()
    })

    test('failed SSE event shows error row with Retry button', async ({ page }) => {
      await page.goto('/#firstrun')
      await mountProgress(page, [MODEL_A])
      await waitForSse(page, `/api/models/${MODEL_A}/pull/stream`, 6_000)

      await emitSseTyped(page, `/api/models/${MODEL_A}/pull/stream`, 'failed', {
        state: 'failed',
        error: { code: 'pull.failed', message: 'sha256 mismatch on shard 2' },
      })

      await expect(page.locator('.dl-err')).toBeVisible({ timeout: 3_000 })
      await expect(page.locator('.dl-err', { hasText: 'sha256 mismatch on shard 2' })).toBeVisible()
      await expect(page.locator('.dl-err button', { hasText: 'Retry' })).toBeVisible()
    })

    test('multiple models open independent SSE streams', async ({ page }) => {
      await page.goto('/#firstrun')
      await mountProgress(page, [MODEL_A, MODEL_B])
      await waitForSse(page, `/api/models/${MODEL_A}/pull/stream`, 6_000)
      await waitForSse(page, `/api/models/${MODEL_B}/pull/stream`, 6_000)

      // Push progress only on MODEL_B — MODEL_A stays queued.
      await emitSseTyped(page, `/api/models/${MODEL_B}/pull/stream`, 'progress', {
        state: 'running',
        bytes_downloaded: 200_000_000,
        bytes_total: 1_000_000_000,
        speed_bps: 5_242_880,
        eta_s: 152,
      })

      // MODEL_B shows 20%, MODEL_A still queued.
      await expect(page.locator('.dl-pct', { hasText: '20%' })).toBeVisible({ timeout: 3_000 })
      await expect(page.locator('.dl-pct.dim')).toHaveCount(1)
    })
  })
})
