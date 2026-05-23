/**
 * hardware.spec.ts — γ-6 Hardware probe re-run (PLAN §10.3 path 6).
 *
 * Adapted for the v2 Hardware view (slice #174): vertical-stack of 6
 * panels (Host / CPU / GPU / NPU / Memory / Storage). The refresh
 * button re-hits /api/hardware (not /api/install/probe; that's
 * FirstRun-specific). The unified-memory bar now lives inside the
 * Memory card (`<MemoryBar>` segments).
 */
import { test, expect, json } from '../fixtures/apiMock'

test('renders 6 hardware panels and refresh re-hits /api/hardware', async ({
  page,
  mockState,
  cleanState,
}) => {
  let hardwareHits = 0
  await page.route('**/api/hardware', (route) => {
    hardwareHits += 1
    return json(route, mockState.hardware)
  })

  await page.goto('/hardware')
  await expect(page.locator('h1').filter({ hasText: 'Hardware' })).toBeVisible()
  await expect(page.getByTestId('hw-card')).toHaveCount(6)

  // Refresh triggers another GET /api/hardware.
  await page.getByTestId('hw-refresh').click()
  await expect.poll(() => hardwareHits, { timeout: 5000 }).toBeGreaterThanOrEqual(2)
})
