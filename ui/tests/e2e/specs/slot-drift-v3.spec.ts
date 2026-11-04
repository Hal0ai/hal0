/**
 * slot-drift-v3 (#1111, #2660, #3044) — post-update slot drift reaches the
 * operator, and the restart is their call.
 *
 * `hal0 update` re-renders each slot unit but never restarts a running slot, so
 * a slot can keep serving the pre-update argv. `GET /api/updates/slot-drift`
 * (src/hal0/api/routes/updater.py `slot_drift`) lists them; `POST
 * /api/updates/restart-slots` bounces only those. The topbar bell and the
 * Overview "needs you" inbox each offer the restart; neither fires it on its own.
 */
import { test, expect, json } from '../fixtures/apiMock'
import type { Page } from '@playwright/test'

const DRIFT = {
  count: 2,
  slots: [
    { slot: 'chat', diffs: [{ key: '--ctx-size', running: '4096', rendered: '131072' }] },
    { slot: 'embed', diffs: [{ key: '--batch-size', running: '512', rendered: '2048' }] },
  ],
  auto_restart: null,
}
const CLEAN = { count: 0, slots: [], auto_restart: null }

async function upToDate(page: Page) {
  await page.addInitScript(() => {
    ;(window as unknown as { __hal0UpdateStateOverride?: unknown }).__hal0UpdateStateOverride = {
      hal0: { current: '0.3.0-alpha.1', available: null, channel: 'stable' },
      flm: { current: 'v0.9.42', source: 'manual-deb' },
      autoCheck: true,
    }
  })
}

/** Serve drift until a restart is posted, then serve a clean box. Records the POST bodies. */
async function driftUntilRestarted(page: Page) {
  const restarts: unknown[] = []
  await page.route('**/api/updates/slot-drift', (route) =>
    json(route, restarts.length ? CLEAN : DRIFT),
  )
  await page.route('**/api/updates/restart-slots', (route) => {
    restarts.push(route.request().postDataJSON())
    return json(route, { restarted: ['chat', 'embed'], failed: [], skipped_busy: [], skipped_render_error: [], count: 2 })
  })
  return restarts
}

test.describe('Post-update slot drift', () => {
  test('the bell counts drift and Restart posts an unrestricted sweep', async ({ page }) => {
    await upToDate(page)
    const restarts = await driftUntilRestarted(page)
    await page.goto('/')

    await expect(page.getByTestId('tb-bell-badge')).toHaveText('1')
    await page.getByTestId('tb-bell').click()
    const sec = page.getByTestId('notif-sec-updates')
    await expect(sec).toContainText('2 slots running a pre-update config — restart to apply')

    // Nothing restarted until the operator asks.
    expect(restarts).toHaveLength(0)
    await sec.getByRole('button', { name: 'Restart' }).click()

    // No `slots` key: "all currently-drifted slots" (restart_drifted_slots).
    await expect.poll(() => restarts).toEqual([{}])
    // The drift query is invalidated, so the row and the badge clear.
    await expect(page.getByTestId('tb-bell-badge')).toHaveCount(0)
    await expect(page.getByTestId('notif-empty')).toBeVisible()
  })

  test('a single drifted slot reads in the singular', async ({ page }) => {
    await upToDate(page)
    await page.route('**/api/updates/slot-drift', (route) =>
      json(route, { count: 1, slots: [DRIFT.slots[0]], auto_restart: null }),
    )
    await page.goto('/')
    await page.getByTestId('tb-bell').click()
    await expect(page.getByTestId('notif-sec-updates')).toContainText('1 slot running a pre-update config')
  })

  test('the Overview inbox names the drifted slots and its action posts the restart', async ({ page }) => {
    await upToDate(page)
    const restarts = await driftUntilRestarted(page)
    await page.goto('/#dashboard')

    const row = page.locator('.ov-att', { hasText: 'slots need restart' })
    await expect(row).toBeVisible()
    await expect(row).toContainText('2 slots need restart — chat, embed')
    await expect(row).toContainText('still running the pre-update launch command')
    expect(restarts).toHaveLength(0)

    await row.getByRole('button', { name: 'restart drifted slots' }).click()
    await expect.poll(() => restarts).toEqual([{}])
    await expect(page.locator('.ov-att', { hasText: 'slots need restart' })).toHaveCount(0)
  })

  for (const source of ['bell', 'overview'] as const) {
    test(`${source} explains partial restart outcomes`, async ({ page }) => {
      await upToDate(page)
      await page.route('**/api/updates/slot-drift', (route) => json(route, {
        count: 1, slots: [{ slot: 'brain', diffs: [], render_error: 'invalid tune key' }], auto_restart: null,
      }))
      await page.route('**/api/updates/restart-slots', (route) => json(route, {
        restarted: ['ok'], count: 1, failed: [{ slot: 'chat', error: 'unit failed' }],
        skipped_busy: ['embed'], skipped_render_error: ['brain'],
      }))
      await page.goto('/#dashboard')
      await expect(page.locator('.ov-att', { hasText: 'slots need restart' })).toContainText('brain: invalid tune key')
      if (source === 'bell') {
        await page.getByTestId('tb-bell').click()
        await expect(page.getByTestId('notif-sec-updates')).toContainText('brain: invalid tune key')
        await page.screenshot({ path: 'test-results/slot-drift-bell.png', animations: 'disabled' })
        await page.getByTestId('notif-sec-updates').getByRole('button', { name: 'Restart' }).click()
      } else {
        await page.locator('.ov-att', { hasText: 'slots need restart' }).getByRole('button', { name: 'restart drifted slots' }).click()
      }
      const toast = page.locator('.hal0-toast', { hasText: 'Failed to restart chat: unit failed' })
      await expect(toast).toContainText('Skipped busy slots: embed')
      await expect(toast).toContainText('could not be rendered: brain')
      if (source === 'overview') await page.screenshot({ path: 'test-results/slot-drift-outcomes.png', animations: 'disabled' })
    })
  }

  test('reports a failed restart request', async ({ page }) => {
    await upToDate(page)
    await page.route('**/api/updates/slot-drift', (route) => json(route, DRIFT))
    await page.route('**/api/updates/restart-slots', (route) => route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: { code: 'system.unavailable', message: 'service unavailable' } }) }))
    await page.goto('/#dashboard')
    await page.locator('.ov-att', { hasText: 'slots need restart' }).getByRole('button', { name: 'restart drifted slots' }).click()
    await expect(page.locator('.hal0-toast')).toContainText('service unavailable')
    await page.locator('.ov-att', { hasText: 'slots need restart' }).getByRole('button', { name: 'restart drifted slots' }).click()
    await expect(page.locator('.hal0-toast')).toHaveCount(1)
  })

  for (const failure of ['request', 'partial'] as const) {
    test(`clears a ${failure} warning after a successful retry`, async ({ page }) => {
      await upToDate(page)
      let attempts = 0
      await page.route('**/api/updates/slot-drift', (route) => json(route, attempts >= 2 ? CLEAN : DRIFT))
      await page.route('**/api/updates/restart-slots', (route) => {
        attempts++
        if (attempts === 1 && failure === 'request') return route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: { code: 'system.unavailable', message: 'service unavailable' } }) })
        if (attempts === 1) return json(route, { restarted: [], failed: [{ slot: 'chat', error: 'unit failed' }], skipped_busy: [], skipped_render_error: [], count: 0 })
        return json(route, { restarted: ['chat'], failed: [], skipped_busy: [], skipped_render_error: [], count: 1 })
      })
      await page.goto('/#dashboard')
      const action = page.locator('.ov-att', { hasText: 'slots need restart' }).getByRole('button', { name: 'restart drifted slots' })
      await action.click()
      await expect(page.locator('.hal0-toast')).toContainText(failure === 'request' ? 'service unavailable' : 'unit failed')
      await action.click()
      await expect(page.locator('.hal0-toast')).toHaveCount(0)
      await expect(page.locator('.ov-att', { hasText: 'slots need restart' })).toHaveCount(0)
    })
  }

  test('a clean box shows no drift anywhere', async ({ page }) => {
    await upToDate(page)
    await page.route('**/api/updates/slot-drift', (route) => json(route, CLEAN))
    await page.goto('/#dashboard')
    await expect(page.locator('.ov-att', { hasText: 'need restart' })).toHaveCount(0)
    await page.getByTestId('tb-bell').click()
    await expect(page.getByTestId('notif-sec-updates')).toHaveCount(0)
  })
})
