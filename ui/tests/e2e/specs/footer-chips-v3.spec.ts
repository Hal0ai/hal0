/**
 * footer-chips-v3 — issue #221, retargeted for the legacy-runtime removal
 * (#687 Phase E).
 *
 * History: the footer used to carry runtime-rollup "queued" and npu
 * "coresident" chips; both surfaces are gone with the old rollup hook.
 * The surviving footer chip is the runtime chip, driven by
 * useRuntimeRollup() over the shared /api/slots poll. The #221 intent
 * carries over: the chip reflects the real backend signal (container
 * readiness counts), never a hardcoded literal.
 *
 * Spec runs against the VITE_MOCK_HAL0-forced dev server: /api/slots is
 * served from window.HAL0_DATA.slots, so readiness states are driven by
 * clobbering HAL0_DATA before the dash modules read it.
 */
import { test, expect } from '../fixtures/apiMock'

test.describe('Footer runtime chip reflects container readiness (#221)', () => {
  test('runtime chip shows ready/total counts with default HAL0_DATA', async ({ page }) => {
    await page.goto('/')
    const footer = page.locator('.footer')
    await expect(footer).toBeVisible()

    const runtimeChip = footer.locator('.foot-chip', {
      has: page.locator('.k', { hasText: /^runtime:$/ }),
    })
    await expect(runtimeChip).toBeVisible()
    // HAL0_DATA seeds 10 enabled slots (legacy is disabled); all but the
    // warming-demo slot are ready → "9/10 ready", chip dot lit "up".
    await expect(runtimeChip.locator('.v')).toHaveText('9/10 ready')
    await expect(runtimeChip).toHaveClass(/\bup\b/)
  })

  test('runtime chip counts drop when containers stop', async ({ page }) => {
    // Stop every container BEFORE dash modules load — the rollup must
    // re-derive readiness from the live slot fields, not cache a literal.
    await page.addInitScript(() => {
      const id = setInterval(() => {
        const d = (window as any).HAL0_DATA
        if (d && Array.isArray(d.slots)) {
          for (const s of d.slots) {
            if (s._synthetic) continue
            s.container_status = 'stopped'
            s.container_health = false
            s.state = 'offline'
          }
          clearInterval(id)
        }
      }, 5)
    })

    await page.goto('/')
    const footer = page.locator('.footer')
    await expect(footer).toBeVisible()

    const runtimeChip = footer.locator('.foot-chip', {
      has: page.locator('.k', { hasText: /^runtime:$/ }),
    })
    await expect(runtimeChip).toBeVisible()
    await expect(runtimeChip.locator('.v')).toHaveText('0/10 ready')
  })
})
