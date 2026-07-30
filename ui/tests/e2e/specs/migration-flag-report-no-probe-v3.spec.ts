/**
 * migration-flag-report-no-probe-v3 (GH #1439) — the dashboard must not probe
 * a backend route that does not exist yet.
 *
 * useMigrationReport polled `GET /api/migrations/flag-report` every 60s from
 * app mount (MigrationBanner is rendered at the root in main.jsx, so this
 * fires on every route) — but there is no backend route for it (flagged,
 * "it lands with the migration lane" per the hook's own docstring). Against
 * a real backend this is a 404 logged to the console on every single page
 * load; the hook's try/catch only prevents a crash, it does not prevent the
 * browser from making — and logging — the failed request.
 *
 * The apiMock fixture's catch-all `/api/` route answers `{}` for anything
 * unregistered, which would silently hide this bug (no distinguishable 404
 * in the mocked harness) — so this spec removes the catch-all's cover by
 * registering an EXPLICIT 404 for this one path first and asserting it is
 * never hit, which is the real fix's contract: the query must not fire at
 * all, not just tolerate a failure quietly.
 */
import { test, expect } from '../fixtures/apiMock'

test.describe('Migration flag-report probe (#1439)', () => {
  test('GET /api/migrations/flag-report is never requested', async ({ page }) => {
    let hit = false
    await page.route('**/api/migrations/flag-report', (route) => {
      hit = true
      return route.fulfill({ status: 404, body: 'not found' })
    })

    await page.goto('/#dashboard')
    // Give any mount-time fetch a moment to fire before asserting its absence.
    await page.waitForTimeout(500)

    expect(hit).toBe(false)
  })
})
