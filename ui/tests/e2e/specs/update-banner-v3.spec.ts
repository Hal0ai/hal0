/**
 * update-banner-v3 — Phase 2 of epic #322.
 *
 * The UpdateBanner reads `useUpdateState()` and self-renders into the
 * global banner slot above the active view. It must:
 *   1. stay hidden when the hook returns `available: null`
 *   2. stay hidden when `available` matches `current`
 *   3. render `"hal0 <available> available"` when there's a newer release
 *   4. honour its dismiss × button without coming back on re-render
 *
 * The dev server runs with VITE_MOCK_HAL0=1 so the forced-mock layer
 * in `src/api/mock.ts` short-circuits `/api/updates/state` without ever
 * touching the network — `page.route` can't intercept. We override the
 * mock payload at boot via `window.HAL0_DATA.updateStateOverride` instead,
 * which `buildUpdateState` checks first.
 */
import { test, expect } from '../fixtures/apiMock'

async function withUpdateState(
  page: import('@playwright/test').Page,
  override: unknown,
) {
  await page.addInitScript((payload) => {
    ;(window as any).__hal0UpdateStateOverride = payload
  }, override)
}

test.describe('UpdateBanner (#322 phase 2)', () => {
  test('renders nothing when available is null', async ({ page }) => {
    await withUpdateState(page, {
      hal0: { current: '0.3.0-alpha.1', available: null, channel: 'stable' },
      flm: { current: 'v0.9.42', source: 'manual-deb' },
      autoCheck: true,
    })
    await page.goto('/#dashboard')
    await expect(page.locator('.topbar')).toBeVisible()
    await expect(page.locator('.view-banners')).toBeAttached()
    await expect(page.locator('.view-banners .banner-info')).toHaveCount(0)
  })

  test('renders nothing when current === available', async ({ page }) => {
    await withUpdateState(page, {
      hal0: { current: '0.3.0-alpha.1', available: '0.3.0-alpha.1', channel: 'stable' },
      flm: { current: 'v0.9.42', source: 'manual-deb' },
      autoCheck: true,
    })
    await page.goto('/#dashboard')
    await expect(page.locator('.topbar')).toBeVisible()
    await expect(page.locator('.view-banners .banner-info')).toHaveCount(0)
  })

  test('renders "hal0 <version> available" when an update is offered', async ({ page }) => {
    await withUpdateState(page, {
      hal0: { current: '0.3.0-alpha.1', available: '0.3.0', channel: 'stable' },
      flm: { current: 'v0.9.42', source: 'manual-deb' },
      autoCheck: true,
    })
    await page.goto('/#dashboard')
    const banner = page.locator('.view-banners .banner-info').first()
    await expect(banner).toBeVisible()
    await expect(banner).toContainText('hal0 0.3.0 available')
    // channel from the hook payload should be reflected in the body
    await expect(banner).toContainText('stable')
  })

  test('dismiss × hides the banner', async ({ page }) => {
    await withUpdateState(page, {
      hal0: { current: '0.3.0-alpha.1', available: '0.3.0', channel: 'stable' },
      flm: { current: 'v0.9.42', source: 'manual-deb' },
      autoCheck: true,
    })
    await page.goto('/#dashboard')
    const banner = page.locator('.view-banners .banner-info').first()
    await expect(banner).toBeVisible()
    await banner.locator('.banner-dismiss').click()
    await expect(page.locator('.view-banners .banner-info')).toHaveCount(0)
  })
})
