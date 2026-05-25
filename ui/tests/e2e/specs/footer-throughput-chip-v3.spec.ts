/**
 * footer-throughput-chip-v3 — issue #326 (epic #322 Phase 4).
 *
 * The throughput chip in the footer used to render `${L.throughput} MB/s`
 * with a `—` fallback when Lemonade did not surface `throughput_mbps`.
 * That produced misleading "—" output (and would have produced "0.0 MB/s"
 * if any layer of the fallback chain ever defaulted to 0). The chip now
 * hides entirely when throughput is null or 0, matching the same null-
 * honoring discipline as queued + coresident (#221).
 *
 * Spec runs against MOCK_LEMONADE-forced dev. `buildHealth` round-trips
 * HAL0_DATA.lemond.throughput so we can drive the three states by
 * clobbering HAL0_DATA before the dash modules read it.
 */
import { test, expect } from '../fixtures/apiMock'

test.describe('Footer throughput chip hides on missing/zero (#326)', () => {
  test('renders "12.4 MB/s" when health surfaces a positive throughput', async ({ page }) => {
    await page.goto('/')
    const footer = page.locator('.footer')
    await expect(footer).toBeVisible()
    // Wait for the 2s health poll to land — sidebar version row is a
    // good proxy for "rollup data has propagated".
    await expect(page.locator('.sb-status .row .v', { hasText: /^v\d/ })).toBeVisible({ timeout: 6_000 })

    const throughputChip = footer.locator('.foot-chip', { has: page.locator('.k', { hasText: /^throughput$/ }) })
    await expect(throughputChip).toBeVisible()
    // HAL0_DATA seeds throughput=12.4.
    await expect(throughputChip.locator('.v')).toHaveText('12.4 MB/s')
  })

  test('chip hidden when health omits throughput_mbps', async ({ page }) => {
    await page.addInitScript(() => {
      const id = setInterval(() => {
        const d = (window as any).HAL0_DATA
        if (d && d.lemond) {
          d.lemond.throughput = undefined
          clearInterval(id)
        }
      }, 5)
    })

    await page.goto('/')
    const footer = page.locator('.footer')
    await expect(footer).toBeVisible()
    await expect(page.locator('.sb-status .row .v', { hasText: /^v\d/ })).toBeVisible({ timeout: 6_000 })
    await page.waitForTimeout(2_500)

    const throughputChip = footer.locator('.foot-chip', { has: page.locator('.k', { hasText: /^throughput$/ }) })
    await expect(throughputChip).toHaveCount(0)
  })

  test('chip hidden when health reports throughput_mbps=0', async ({ page }) => {
    // Zero from a live system that's actually serving traffic is just
    // as meaningless as null — a "0.0 MB/s" badge in the footer would
    // look like a metrics bug. Treat 0 as "no signal" and hide.
    await page.addInitScript(() => {
      const id = setInterval(() => {
        const d = (window as any).HAL0_DATA
        if (d && d.lemond) {
          d.lemond.throughput = 0
          clearInterval(id)
        }
      }, 5)
    })

    await page.goto('/')
    const footer = page.locator('.footer')
    await expect(footer).toBeVisible()
    await expect(page.locator('.sb-status .row .v', { hasText: /^v\d/ })).toBeVisible({ timeout: 6_000 })
    await page.waitForTimeout(2_500)

    const throughputChip = footer.locator('.foot-chip', { has: page.locator('.k', { hasText: /^throughput$/ }) })
    await expect(throughputChip).toHaveCount(0)
  })
})
