/**
 * footer-throughput-chip-v3 — issue #326 (epic #322 Phase 4), updated #340,
 * retargeted for the legacy-runtime removal (#687 Phase E).
 *
 * History: the footer used to render a throughput chip fed by the old
 * runtime health/stats poll. That chip is gone — tok/s now surfaces on the
 * dashboard's ThroughputCard, which sums `metrics.toks` across serving
 * slots from the shared /api/slots poll. The intent carries over:
 *   - show a real number when slots report positive tok/s
 *   - honor "no signal" (all zero/absent) by showing the em-dash, never
 *     a fake 0 (#221 null-honoring discipline).
 *
 * Spec runs against the VITE_MOCK_HAL0-forced dev server: /api/slots is
 * served from window.HAL0_DATA.slots, so we drive states by clobbering
 * HAL0_DATA before the dash modules read it.
 */
import { test, expect } from '../fixtures/apiMock'

const throughputCard = (page: import('@playwright/test').Page) =>
  page.locator('.side-card', {
    has: page.locator('.side-card-h', { hasText: 'Throughput' }),
  })

test.describe('Dashboard ThroughputCard sums slot tok/s (#326, #340)', () => {
  test('renders the summed tok/s when slots report positive throughput', async ({ page }) => {
    await page.goto('/')
    const card = throughputCard(page)
    await expect(card).toBeVisible()
    // HAL0_DATA seeds primary=45 + agent=40 tok/s (only positive `toks`
    // count) → card header shows the 85.0 sum.
    await expect(card.locator('.side-card-h .right')).toContainText('85.0', { timeout: 8_000 })
    await expect(card.locator('.side-card-h .right')).toContainText('tok/s')
  })

  test('shows em-dash when no slot reports positive tok/s', async ({ page }) => {
    // Zero from a system that isn't serving traffic is "no signal" —
    // the card must show "—", never a fake summed 0.
    await page.addInitScript(() => {
      const id = setInterval(() => {
        const d = (window as any).HAL0_DATA
        if (d && Array.isArray(d.slots)) {
          for (const s of d.slots) {
            if (s.metrics && typeof s.metrics.toks === 'number') s.metrics.toks = 0
          }
          clearInterval(id)
        }
      }, 5)
    })

    await page.goto('/')
    const card = throughputCard(page)
    await expect(card).toBeVisible()
    await expect(card.locator('.side-card-h .right')).toContainText('—')
    await expect(card.locator('.side-card-h .right')).not.toContainText(/\d/)
  })
})
