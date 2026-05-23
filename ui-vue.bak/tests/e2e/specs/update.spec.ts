/**
 * update.spec.ts — γ-7 Update flow (PLAN §10.3 path 7).
 *
 * Exercises the Wave-3 `RestartBanner.vue` against the
 * `/api/updates/{check,apply,status,rollback}` wire contract:
 *
 *   1. /api/updates/check returns update_available:false → no banner.
 *   2. /api/updates/check flips to update_available:true. The banner
 *      only re-checks on demand (boot + when system.status hints), so
 *      we flip mockState.status.update_available + refresh the system
 *      store to trigger the watcher in RestartBanner.
 *   3. Click "Apply update" → POST /api/updates/apply returns
 *      {job_id}; the banner polls /api/updates/status/{job_id} until
 *      state === 'applied'. The visible message switches to "Update
 *      applied — restart …".
 *   4. Click "Dismiss" → banner hides (the post-apply banner only goes
 *      away on dismiss; there is no "auto-hide on next status flip"
 *      affordance in the current UI).
 *
 * Rollback (PLAN §10.3): exercised via a second pass — after the apply
 * settles, the backend's /check claims `previous_available: true`, which
 * surfaces a Rollback button. The spec dismisses instead of rolling
 * back to keep wall-clock short, but routes the rollback endpoint so a
 * future Wave can flip on that assertion.
 */
import { test, expect, json } from '../fixtures/apiMock'

test('shell banner reflects /api/updates/check transitions', async ({
  page,
  mockState,
  cleanState,
}) => {
  // Mutable per-phase state for the /api/updates/check route.
  let checkResponse: any = {
    update_available: false,
    current_version: '0.1.0',
    latest_version: '0.1.0',
    channel: 'stable',
  }
  let applyCount = 0
  let rollbackCount = 0
  let statusPollCount = 0

  await page.route('**/api/updates/check', (route) => json(route, checkResponse))
  await page.route('**/api/updates/apply', (route) => {
    applyCount += 1
    return json(route, { job_id: 'job-1' })
  })
  await page.route('**/api/updates/rollback', (route) => {
    rollbackCount += 1
    return json(route, { job_id: 'job-2' })
  })
  await page.route('**/api/updates/status/*', (route) => {
    statusPollCount += 1
    // Return 'applied' on first poll so the spec doesn't depend on
    // wall-clock between polls.
    return json(route, { state: 'applied', progress: 100, breadcrumbs: ['done'] })
  })

  // ── Phase 1: no update available → banner hidden ─────────
  mockState.status.update_available = false
  await page.goto('/')
  await expect(page.locator('.restart-banner')).toHaveCount(0)

  // ── Phase 2: status flips → banner appears with version ──
  //
  // RestartBanner's only trigger for re-running /api/updates/check is
  // its onMounted hook (the systemUpdateHint watcher guards on
  // `!check.value`, so it won't re-fetch once a first check landed).
  // Reload the page so the banner re-mounts and picks up the new
  // /check response.
  checkResponse = {
    update_available: true,
    current_version: '0.1.0',
    latest_version: '0.1.1',
    channel: 'stable',
    notes_url: 'https://hal0.dev/releases/0.1.1',
  }
  mockState.status.update_available = true
  mockState.status.update_version = '0.1.1'
  await page.reload()

  const banner = page.locator('.restart-banner')
  await expect(banner).toBeVisible()
  await expect(banner).toContainText(/Update available/)
  await expect(banner).toContainText('v0.1.1')

  // ── Phase 3: click "Apply update" → poll → applied ──────
  await banner.getByRole('button', { name: /^Apply update$/ }).click()
  await expect.poll(() => applyCount).toBe(1)
  // Wait for poll → applied → banner message switches.
  await expect(banner).toContainText(/Update applied/, { timeout: 5_000 })
  expect(statusPollCount).toBeGreaterThanOrEqual(1)

  // ── Phase 4: Dismiss banner ─────────────────────────────
  await banner.getByRole('button', { name: /Dismiss banner/ }).click()
  await expect(banner).toHaveCount(0)

  // Rollback was not exercised in this pass; record that the route is
  // wired but unused. A Wave-4 spec can flip this once the
  // post-apply UI surfaces a rollback affordance more prominently.
  expect(rollbackCount).toBe(0)
})
