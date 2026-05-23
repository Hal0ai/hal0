/**
 * footer-chips-v3 — issue #221.
 *
 * The footer "queued" chip used to render `L.queued` which the hook
 * hardcoded to 0 (lemond never surfaced the field). The "coresident"
 * chip in both the footer and the sidebar status block was a literal
 * string with no backend signal. Both now honor `null` by hiding.
 *
 * Spec runs against the MOCK_LEMONADE-forced dev server. `buildHealth`
 * round-trips HAL0_DATA.lemond.{queued,coresident} so the demo keeps
 * showing the chips; clearing those fields in `HAL0_DATA` simulates a
 * lemond build that doesn't surface them yet, and the chips hide.
 */
import { test, expect } from '../fixtures/apiMock'

test.describe('Footer chips honor backend null (#221)', () => {
  test('queued + coresident chips appear with default HAL0_DATA', async ({ page }) => {
    await page.goto('/')
    const footer = page.locator('.footer')
    await expect(footer).toBeVisible()
    // Give the 2s health poll a chance to resolve.
    await expect(page.locator('.sb-status .row .v', { hasText: /^v\d/ })).toBeVisible({ timeout: 6_000 })

    const queuedChip = footer.locator('.foot-chip', { has: page.locator('.k', { hasText: /^queued$/ }) })
    await expect(queuedChip).toBeVisible()
    // HAL0_DATA seeds queued=0 — chip should render the literal "0".
    await expect(queuedChip.locator('.v')).toHaveText('0')

    const npuChip = footer.locator('.foot-chip', { has: page.locator('.k', { hasText: /^npu$/ }) })
    await expect(npuChip).toBeVisible()
    await expect(npuChip.locator('.v')).toHaveText('coresident')

    const sidebarNpu = page.locator('.sb-status .row .k', { hasText: /^npu$/ })
    await expect(sidebarNpu).toBeVisible()
  })

  test('queued + coresident chips hidden when fields absent from health', async ({ page }) => {
    // Clobber HAL0_DATA.lemond.{queued,coresident} BEFORE dash modules
    // load. The mock harness reads HAL0_DATA lazily on every fetch, so
    // by the time /v1/health is called the rollup will see undefined →
    // hook coerces to null → chips hide.
    await page.addInitScript(() => {
      const id = setInterval(() => {
        const d = (window as any).HAL0_DATA
        if (d && d.lemond) {
          d.lemond.queued = undefined
          d.lemond.coresident = undefined
          clearInterval(id)
        }
      }, 5)
    })

    await page.goto('/')
    const footer = page.locator('.footer')
    await expect(footer).toBeVisible()
    // Wait for sidebar rollup to repaint with hook data.
    await expect(page.locator('.sb-status .row .v', { hasText: /^v\d/ })).toBeVisible({ timeout: 6_000 })
    // Extra beat so the second health poll lands.
    await page.waitForTimeout(2_500)

    const queuedChip = footer.locator('.foot-chip', { has: page.locator('.k', { hasText: /^queued$/ }) })
    await expect(queuedChip).toHaveCount(0)

    const npuChip = footer.locator('.foot-chip', { has: page.locator('.k', { hasText: /^npu$/ }) })
    await expect(npuChip).toHaveCount(0)

    const sidebarNpu = page.locator('.sb-status .row .k', { hasText: /^npu$/ })
    await expect(sidebarNpu).toHaveCount(0)
  })
})
