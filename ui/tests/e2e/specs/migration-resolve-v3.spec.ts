/**
 * migration-resolve-v3 — D5 flag-migration resolution moment (post-R3 rework).
 *
 * The migrator refuses a model shared by slots with divergent launch overrides
 * and reports it. The production banner is endpoint-dormant, so this suite
 * drives the SAME resolution view off the Tweaks-panel banner registry demo
 * toggle (id "migration-unresolved") → its Resolve fires the window event the
 * MigrationResolveHost opens on, using DEMO_MIGRATION_REPORT.
 *
 * Asserts: the banner surfaces, Resolve opens the view, only conflicting flags
 * are shown side-by-side, both resolutions are selectable, and Apply is
 * disabled-with-reason (no apply endpoint yet).
 */
import { test, expect } from '../fixtures/apiMock'

async function activateDemoBanner(page: import('@playwright/test').Page) {
  await page.goto('/#dashboard')
  // The BannerProvider publishes its toggle on window.__hal0Banners.
  await expect
    .poll(() => page.evaluate(() => !!(window as any).__hal0Banners))
    .toBeTruthy()
  await page.evaluate(() => (window as any).__hal0Banners.toggle('migration-unresolved', true))
}

test.describe('Flag-migration resolution', () => {
  test('demo banner surfaces and its Resolve opens the resolution view', async ({ page }) => {
    await activateDemoBanner(page)

    const banner = page.locator('.banner-stack', { hasText: 'needs resolution' })
    await expect(banner).toBeVisible()
    await expect(banner).toContainText('HAL0-0142')

    await banner.getByRole('button', { name: 'Resolve' }).click()
    await expect(page.getByTestId('migration-resolve-view')).toBeVisible()
    await expect(page.getByTestId('migration-resolve-model')).toContainText('Qwen3-8B-Q4_K_M')
    await expect(page.getByTestId('migration-severity')).toContainText('warning')
  })

  test('side-by-side divergent values — only conflicting flags, per slot column', async ({ page }) => {
    await activateDemoBanner(page)
    await page.locator('.banner-stack').getByRole('button', { name: 'Resolve' }).click()

    // The three sharing slots each get a column header.
    await expect(page.getByTestId('migration-canonical-primary')).toContainText('#1')
    await expect(page.getByTestId('migration-canonical-chat-alt')).toContainText('#4')
    await expect(page.getByTestId('migration-canonical-coder-lite')).toContainText('#6')

    // Conflicting flags rendered; the -b row shows the divergent values.
    const bRow = page.getByTestId('migration-conflict-b')
    await expect(bRow).toContainText('-b')
    await expect(bRow).toContainText('4096')
  })

  test('pick-canonical and split are both selectable; Apply is disabled-with-reason', async ({ page }) => {
    await activateDemoBanner(page)
    await page.locator('.banner-stack').getByRole('button', { name: 'Resolve' }).click()

    // Nothing chosen yet → reason says so; Apply disabled regardless.
    await expect(page.getByTestId('migration-apply')).toBeDisabled()
    await expect(page.getByTestId('migration-apply-reason')).toContainText(/until you choose/i)

    // Pick a canonical column.
    await page.getByTestId('migration-canonical-coder-lite').click()
    await expect(page.getByTestId('migration-apply-reason')).toContainText(/coder-lite becomes canonical/i)

    // Switch to split.
    await page.getByTestId('migration-mode-split').click()
    await expect(page.getByTestId('migration-apply-reason')).toContainText(/split into 3 separate models/i)

    // Apply is still gated on the missing endpoint.
    await expect(page.getByTestId('migration-apply')).toBeDisabled()
    await expect(page.getByTestId('migration-apply-reason')).toContainText(/API-lane/i)
  })

  test('pager walks the refused models (1 of 2 → 2 of 2)', async ({ page }) => {
    await activateDemoBanner(page)
    await page.locator('.banner-stack').getByRole('button', { name: 'Resolve' }).click()

    await expect(page.getByTestId('migration-resolve-model')).toContainText('Qwen3-8B-Q4_K_M')
    await expect(page.getByTestId('migration-pager-prev')).toBeDisabled()
    await page.getByTestId('migration-pager-next').click()
    await expect(page.getByTestId('migration-resolve-model')).toContainText('gemma3-4b-it')
    await expect(page.getByTestId('migration-pager-next')).toBeDisabled()
  })
})
