/**
 * logs.spec.ts — γ-5 Logs SSE tail (PLAN §10.3 path 5).
 *
 * Adapted for the v2 unified Logs view (slice #174):
 *   • Source defaults to 'merged' — hal0 SSE pipe is open immediately
 *     on mount (no per-unit selector; lemond is now a source-toggle).
 *   • Pause button stops appending new SSE frames; resume re-enables.
 *   • Source-toggle 'lemond' surfaces the PR-14 LemonadeJournalPanel
 *     instead of the merged viewport.
 *
 * Backend SSE format unchanged: each frame's `data` is JSON-encoded
 * so the client can JSON.parse it.
 */
import { test, expect, json } from '../fixtures/apiMock'
import { installSseHarness, emitSse, waitForSse } from '../fixtures/sseHarness'

test.beforeEach(async ({ page }) => {
  await installSseHarness(page)
})

test('tails SSE, pauses, resumes', async ({ page, mockState, cleanState }) => {
  await page.route('**/api/logs*', (route) => {
    const req = route.request()
    if (req.url().includes('/api/logs/stream')) {
      return route.fulfill({ status: 200, contentType: 'text/event-stream', body: '' })
    }
    return json(route, { unit: '', lines: [], count: 0 })
  })

  mockState.status.slots.push({ name: 'primary', status: 'ready', port: 8081 })

  await page.goto('/logs')
  await waitForSse(page, '/api/logs/stream', 4000)

  // ── Emit 10 SSE lines (JSON-wrapped per backend contract) ─────
  const streamUrl = '/api/logs/stream'
  for (let i = 0; i < 10; i++) {
    await emitSse(page, streamUrl, JSON.stringify(`line-${i}`))
  }
  for (let i = 0; i < 10; i++) {
    await expect(page.locator('.log-line', { hasText: `line-${i}` })).toBeVisible()
  }

  // ── Pause ─────────────────────────────────────────────────────
  await page.getByTestId('log-pause').click()
  // 5 more frames arrive while paused — must not append.
  for (let i = 10; i < 15; i++) {
    await emitSse(page, streamUrl, JSON.stringify(`line-${i}`))
  }
  await page.waitForTimeout(200)
  for (let i = 10; i < 15; i++) {
    await expect(page.locator('.log-line', { hasText: `line-${i}` })).toHaveCount(0)
  }

  // ── Resume + emit marker ──────────────────────────────────────
  await page.getByTestId('log-pause').click()
  await emitSse(page, streamUrl, JSON.stringify('post-resume-marker'))
  await expect(
    page.locator('.log-line', { hasText: 'post-resume-marker' }),
  ).toBeVisible()
})
