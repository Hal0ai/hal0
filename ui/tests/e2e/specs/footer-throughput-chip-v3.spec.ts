/**
 * footer-throughput-chip-v3 — issue #326 (epic #322 Phase 4), updated #340,
 * retargeted #687 Phase E, MIGRATED for dashboard-overhaul (feat/dashboard-
 * overhaul).
 *
 * History: a footer throughput chip → the old sidebar ThroughputCard (slot
 * tok/s sum) → now the in-grid **ThroughputCard2**, which consumes the NEW
 * /api/stats/throughput/history endpoint (samples[].total_tps) instead of
 * summing slot metrics client-side.
 *
 * The #221 null-honoring invariant carries over verbatim (it IS our no-stub
 * rule): a card must never show a fabricated number. The history endpoint
 * makes "no signal" vs "measured zero" explicit, so the contract is:
 *
 *   - positive reading            → the real number renders.
 *   - empty history (no samples)  → "—" (no signal), never "0.0".
 *   - measured zero (a real 0 sample with N serving) → "0.0" IS shown,
 *     BUT the "<n> slots serving" subrow is GUARANTEED to render alongside
 *     it, so a measured 0.0 can never be misread as fake activity. A bare
 *     "0.0" with no serving context would violate #221; the subrow is the
 *     disambiguator (see metric-cards.jsx → ThroughputCard2 `hasReading`).
 *
 * Mock seam: /api/stats/throughput/history is NOT in the mockFetch allowlist
 * (mock.ts), so under the VITE_MOCK_HAL0-forced dev server it falls through
 * to a real fetch — which Playwright `page.route` CAN intercept. We drive the
 * three cases by fulfilling that route per test.
 */
import { test, expect, json, type Page } from '../fixtures/apiMock'

const card = (page: Page) =>
  page.locator('.dcard', {
    has: page.locator('.dcard-h', { hasText: /throughput/i }),
  }).first()

async function routeHistory(page: Page, body: unknown) {
  await page.route('**/api/stats/throughput/history*', (route) => json(route, body))
}

test.describe('Dashboard ThroughputCard2 — history-backed, #221 null-honoring', () => {
  test('renders the real number when history reports positive throughput', async ({ page }) => {
    await routeHistory(page, {
      window_s: 100,
      bucket_s: 5,
      samples: [
        { ts: 1000, total_tps: 40.0, serving_slots: 1 },
        { ts: 1005, total_tps: 85.0, serving_slots: 2 },
      ],
    })
    await page.goto('/#dashboard')
    const c = card(page)
    await expect(c).toBeVisible({ timeout: 10_000 })
    // Latest sample (85.0) is the hero reading.
    await expect(c.locator('.mc-hero-num')).toContainText('85.0', { timeout: 8_000 })
    await expect(c.locator('.mc-sub-serving')).toContainText('2 slots serving')
  })

  test('shows em-dash for empty history (no signal) — never a fake 0', async ({ page }) => {
    await routeHistory(page, { window_s: 100, bucket_s: 5, samples: [] })
    await page.goto('/#dashboard')
    const c = card(page)
    await expect(c).toBeVisible({ timeout: 10_000 })
    // Empty samples → the card gates ("source pending") OR shows "—": either
    // way it must NEVER render a fabricated 0.0 hero with no serving context.
    const hero = c.locator('.mc-hero-num')
    const heroCount = await hero.count()
    if (heroCount > 0) {
      await expect(hero).toContainText('—')
      // and crucially NOT a fake numeric reading
      await expect(hero).not.toContainText(/\d/)
    } else {
      // gated body instead of a hero number
      await expect(c.locator('.mc-pending-label')).toContainText(/source pending/i)
    }
    // No serving subrow when there is no real reading.
    await expect(c.locator('.mc-sub-serving')).toHaveCount(0)
  })

  test('measured zero shows "0.0" ONLY with the serving subrow as disambiguator', async ({ page }) => {
    await routeHistory(page, {
      window_s: 100,
      bucket_s: 5,
      samples: [{ ts: 2000, total_tps: 0, serving_slots: 0 }],
    })
    await page.goto('/#dashboard')
    const c = card(page)
    await expect(c).toBeVisible({ timeout: 10_000 })
    // A real measured 0 → "0.0" renders …
    await expect(c.locator('.mc-hero-num')).toContainText('0.0', { timeout: 8_000 })
    // … and the serving subrow MUST be present so 0.0 can't read as activity.
    await expect(c.locator('.mc-sub-serving')).toContainText('0 slots serving')
  })
})
