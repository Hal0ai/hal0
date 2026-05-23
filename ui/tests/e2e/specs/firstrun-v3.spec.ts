/**
 * firstrun-v3 — `#firstrun` route drives the 3-state machine
 * (picker → confirm → progress). Smoke: confirm the picker mounts +
 * tier cards render + skip-link present.
 */
import { test, expect } from '../fixtures/apiMock'

test.describe('FirstRun v3 (/firstrun)', () => {
  test('picker (state 1) renders welcome + tier cards', async ({ page }) => {
    await page.goto('/#firstrun')
    await expect(page.locator('.fr-title')).toBeVisible()
    await expect(page.locator('.fr-title')).toContainText('hal0')
    // tier cards (grid layout default)
    const tiers = page.locator('.tier-card')
    expect(await tiers.count()).toBeGreaterThan(0)
    // skip link
    await expect(page.locator('.fr-skip')).toBeVisible()
  })

  test('host-detected RAM/GPU/NPU segments render', async ({ page }) => {
    await page.goto('/#firstrun')
    await expect(page.locator('.fr-detect')).toBeVisible()
    await expect(page.locator('.fr-detect .seg', { hasText: 'RAM' })).toBeVisible()
    await expect(page.locator('.fr-detect .seg', { hasText: 'GPU' })).toBeVisible()
    await expect(page.locator('.fr-detect .seg', { hasText: 'NPU' })).toBeVisible()
  })

  test.fixme('clicking a tier transitions to confirm (state 2)', async ({ page }) => {
    await page.goto('/#firstrun')
    // tier buttons inside `.unfit` cards are `disabled={!fits}` (firstrun.jsx:108,163);
    // pick the recommended tier — guaranteed to fit per recommendation logic.
    const firstTierBtn = page.locator('.tier-card button:not([disabled])').first()
    await firstTierBtn.click()
    // confirm card header
    await expect(page.locator('.fr-confirm-h')).toBeVisible({ timeout: 5000 })
  })
})
