/**
 * runner-images-slots-v3 — Slots ▸ Runner Images tab (runner-catalogue-v2).
 *
 * The Runner Images catalogue moved from Models ▸ Runner Images to a Slots
 * sub-page (nav id slots/runner-images) — runner images are what slots RUN,
 * a lifecycle concern. The listing is served by the FORCED VITE_MOCK_HAL0
 * path (mockFixtures.ts buildRunnerImages, contract-shaped rows carrying
 * available_tags / is_default / in_use_by), same as models-v3.spec.ts.
 */
import { test, expect } from '../fixtures/apiMock'

test.describe('Runner Images under Slots (/slots/runner-images)', () => {
  test('#slots/runner-images deep-links the tab; page keeps the Slots heading', async ({ page }) => {
    await page.goto('/#slots/runner-images')
    await expect(page.locator('.slot-tab.on:has-text("Runner Images")')).toBeVisible()
    // Like Endpoints/Stacks, the sub-tab keeps the single Slots page heading.
    await expect(page.locator('.view .vh h1')).toHaveText('Slots')
    await expect(page.locator('.models-layout')).toBeVisible()
    // Sync CTA lives with the catalogue surface now (not the page header).
    await expect(page.getByTestId('ri-sync')).toBeVisible()
  })

  test('sidebar sub-link navigates to the tab', async ({ page }) => {
    await page.goto('/#slots')
    const sb = page.locator('.sidebar')
    await sb.locator('[data-testid="nav-slots-runner-images"]').click()
    await expect(page).toHaveURL(/#slots\/runner-images/)
    await expect(page.locator('.slot-tab.on:has-text("Runner Images")')).toBeVisible()
  })

  test('family strip + per-row enrichment chips render from contract rows', async ({ page }) => {
    await page.goto('/#slots/runner-images')
    // Family strip: family → effective ref + source badge (launch-truth,
    // Task 10 — served by the `families` payload, not derived from rows).
    const strip = page.getByTestId('ri-family-strip')
    await expect(strip).toBeVisible()
    const familyRow = strip.getByTestId('ri-family-rocmfpx')
    await expect(familyRow).toContainText('rocmfpx')
    await expect(familyRow).toContainText('ghcr.io/hal0ai/hal0-combined:0822')
    // Row card: pull CTA present; tag picker renders when several tags exist.
    await expect(page.getByTestId('ri-pull')).toBeVisible()
    await expect(page.getByTestId('ri-tag-pick')).toBeVisible()
    await expect(page.getByTestId('ri-set-default')).toBeVisible()
  })

  test('Models page no longer hosts the Runner Images tab', async ({ page }) => {
    await page.goto('/#models')
    await expect(page.locator('.slot-tabs')).toBeVisible()
    await expect(page.locator('.slot-tab:has-text("Runner Images")')).toHaveCount(0)
  })
})
