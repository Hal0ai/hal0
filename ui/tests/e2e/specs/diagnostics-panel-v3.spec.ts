/**
 * diagnostics-panel-v3 — D6 Diagnostics surfacing (post-R3 surface rework).
 *
 * A generic Diagnosis renderer (Settings → Doctor): every card is the typed
 * shape { id, severity, confidence, summary, evidence[], next_steps[] } from
 * src/hal0/diagnostics.py — the UI never hardcodes a check.
 *
 * This spec covers the FALLBACK half: the fixture's `/api/` catch-all answers
 * GET /api/doctor with `{}` (no diagnosis feed), so the hook degrades to
 * synthesised GET /api/system-info hardware evidence and says so in a
 * stub-with-reason. The live-feed half lives in
 * diagnostics-doctor-feed-v3.spec.ts (#1458).
 *
 * /api/system-info is NOT in the in-bundle mock allowlist, so page.route wins.
 */
import { test, expect } from '../fixtures/apiMock'

function mockSystemInfo(page: import('@playwright/test').Page, hardware: Record<string, unknown>) {
  return page.route('**/api/system-info', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ hardware, features: {}, backends: {} }),
    }),
  )
}

async function openDoctor(page: import('@playwright/test').Page) {
  await page.goto('/#settings')
  await page.locator('.settings-nav .nav-item', { hasText: 'Doctor' }).click()
  await expect(page.getByTestId('diagnosis-panel')).toBeVisible()
}

test.describe('Diagnostics panel', () => {
  test('renders a generic Diagnosis card from live system-info hardware evidence', async ({ page }) => {
    await mockSystemInfo(page, {
      platform_label: 'Strix Halo (unified memory)',
      cpu_name: 'AMD Ryzen AI Max+ 395',
      cpu_cores: 16,
      gpu_name: 'Radeon 8060S',
      unified_memory_mb: 131072,
      disk_free_mb: 402000,
      npu_present: true,
      npu_name: 'XDNA2',
    })
    await openDoctor(page)

    const card = page.getByTestId('diagnosis-card-HAL0-SYS-INFO')
    await expect(card).toBeVisible()
    await expect(card.getByTestId('diagnosis-severity')).toContainText(/info/i)
    await expect(card.getByTestId('diagnosis-id')).toContainText('HAL0-SYS-INFO')

    // Evidence rows are derived from the payload — not hardcoded.
    const evidence = card.getByTestId('diagnosis-evidence')
    await expect(evidence).toContainText('Strix Halo')
    await expect(evidence).toContainText('AMD Ryzen AI Max+ 395')

    // Next steps render as chips.
    await expect(card.getByTestId('diagnosis-next-step').first()).toContainText(/hal0 doctor/i)

    // info-only feed rolls up to "all clear".
    await expect(page.getByTestId('diagnosis-verdict')).toContainText(/all clear/i)
  })

  test('an absent doctor feed is shown as a stub-with-reason', async ({ page }) => {
    await mockSystemInfo(page, { platform_label: 'Strix Halo', cpu_name: 'AMD' })
    await openDoctor(page)
    await expect(page.getByTestId('diagnosis-feed-stub')).toContainText(
      /doctor feed unavailable/i,
    )
  })

  test('degraded probe (no hardware) shows an honest empty state, never a fake pass', async ({ page }) => {
    await mockSystemInfo(page, {})
    await openDoctor(page)
    await expect(page.getByTestId('diagnosis-empty')).toContainText(/probe unavailable/i)
    // No fabricated diagnosis cards.
    await expect(page.getByTestId('diagnosis-panel').locator('[data-testid^="diagnosis-card-"]')).toHaveCount(0)
  })
})
