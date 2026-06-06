/**
 * footer-throughput-chip-v3 — issue #326 (epic #322 Phase 4), updated #340.
 *
 * #326: chip hid entirely when Lemonade omitted throughput_mbps.
 * #340: chip now shows tok/s from /v1/stats (always present on a serving
 *       backend) instead of the always-null MB/s field. MB/s remains as a
 *       fallback in case lastTokPerSec is absent. Both branches hide on
 *       null/0 — the same null-honoring discipline as queued + coresident
 *       (#221).
 *
 * Spec runs against MOCK_LEMONADE-forced dev. `buildStats` round-trips
 * HAL0_DATA.lemond.lastTokPerSec (added #340) and `buildHealth` round-trips
 * HAL0_DATA.lemond.throughput, so we can drive all states by clobbering
 * HAL0_DATA before the dash modules read it.
 */
import { test, expect } from '../fixtures/apiMock'

test.describe('Footer throughput chip shows tok/s (#326, #340)', () => {
  test('renders "45 tok/s" when /v1/stats surfaces a positive tok/s', async ({ page }) => {
    await page.goto('/')
    const footer = page.locator('.footer')
    await expect(footer).toBeVisible()
    // Wait for the 5s stats poll to land — sidebar version row is a
    // good proxy for "rollup data has propagated".
    await expect(page.locator('[data-testid="runtime-row-lemond"] .v', { hasText: /v\d/ })).toBeVisible({ timeout: 8_000 })

    const throughputChip = footer.locator('.foot-chip', { has: page.locator('.k', { hasText: /^throughput$/ }) })
    await expect(throughputChip).toBeVisible()
    // HAL0_DATA seeds lastTokPerSec=45.0 → chip shows "45 tok/s".
    await expect(throughputChip.locator('.v')).toHaveText('45 tok/s')
  })

  test('chip hidden when both lastTokPerSec and throughput_mbps are null', async ({ page }) => {
    await page.addInitScript(() => {
      const id = setInterval(() => {
        const d = (window as any).HAL0_DATA
        if (d && d.lemond) {
          d.lemond.lastTokPerSec = undefined
          d.lemond.throughput = undefined
          clearInterval(id)
        }
      }, 5)
    })

    await page.goto('/')
    const footer = page.locator('.footer')
    await expect(footer).toBeVisible()
    await expect(page.locator('[data-testid="runtime-row-lemond"] .v', { hasText: /v\d/ })).toBeVisible({ timeout: 8_000 })
    await page.waitForTimeout(6_000)

    const throughputChip = footer.locator('.foot-chip', { has: page.locator('.k', { hasText: /^throughput$/ }) })
    await expect(throughputChip).toHaveCount(0)
  })

  test('chip hidden when both lastTokPerSec and throughput_mbps are zero', async ({ page }) => {
    // Zero from a live system that's actually serving traffic is just
    // as meaningless as null — treat 0 as "no signal" and hide.
    await page.addInitScript(() => {
      const id = setInterval(() => {
        const d = (window as any).HAL0_DATA
        if (d && d.lemond) {
          d.lemond.lastTokPerSec = 0
          d.lemond.throughput = 0
          clearInterval(id)
        }
      }, 5)
    })

    await page.goto('/')
    const footer = page.locator('.footer')
    await expect(footer).toBeVisible()
    await expect(page.locator('[data-testid="runtime-row-lemond"] .v', { hasText: /v\d/ })).toBeVisible({ timeout: 8_000 })
    await page.waitForTimeout(6_000)

    const throughputChip = footer.locator('.foot-chip', { has: page.locator('.k', { hasText: /^throughput$/ }) })
    await expect(throughputChip).toHaveCount(0)
  })

  test('falls back to MB/s when lastTokPerSec is null but throughput_mbps present', async ({ page }) => {
    await page.addInitScript(() => {
      const id = setInterval(() => {
        const d = (window as any).HAL0_DATA
        if (d && d.lemond) {
          d.lemond.lastTokPerSec = undefined
          d.lemond.throughput = 12.4
          clearInterval(id)
        }
      }, 5)
    })

    await page.goto('/')
    const footer = page.locator('.footer')
    await expect(footer).toBeVisible()
    await expect(page.locator('[data-testid="runtime-row-lemond"] .v', { hasText: /v\d/ })).toBeVisible({ timeout: 8_000 })
    await page.waitForTimeout(6_000)

    const throughputChip = footer.locator('.foot-chip', { has: page.locator('.k', { hasText: /^throughput$/ }) })
    await expect(throughputChip).toBeVisible()
    await expect(throughputChip.locator('.v')).toHaveText('12.4 MB/s')
  })
})
