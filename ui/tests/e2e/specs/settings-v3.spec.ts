/**
 * settings-v3 — `#settings` route renders the grouped rail nav (GENERAL /
 * MODELS & INFERENCE / SYSTEM / INTEGRATIONS) and swaps the right pane on
 * click.
 *
 * settings-panel cleanup collapsed the old 8-group / 20-page tree:
 * Overview (new default landing) absorbed Health & Stats + General;
 * Loaded Models absorbed Library & Downloads; Hardware & Runtimes merged
 * Backend & GPU + Runtimes; Updates absorbed About; the blocked Hardware
 * Tuning stub was removed. Legacy #settings/<id> deep links resolve via
 * SECTION_ALIASES. Section list below is sourced from `SettingsNav.jsx`'s
 * NAV_GROUPS — the authoritative IA.
 */
import { test, expect } from '../fixtures/apiMock'

const SECTIONS = [
  'Overview', 'Security', 'Doctor',
  'Loaded Models', 'Model Defaults', 'AI Capabilities',
  'Hardware & Runtimes', 'Storage', 'Memory', 'Updates', 'Advanced',
  'Secrets', 'Agent Chat',
]

test.describe('Settings v3 (/settings)', () => {
  test('renders rail nav with all sections', async ({ page }) => {
    await page.goto('/#settings')
    await expect(page.locator('.view .vh h1')).toHaveText('Settings')
    const nav = page.locator('.settings-nav .nav-item')
    expect(await nav.count()).toBe(SECTIONS.length)
    for (const label of SECTIONS) {
      await expect(page.locator('.settings-nav .nav-item', { hasText: label })).toBeVisible()
    }
  })

  test('default section is Overview', async ({ page }) => {
    await page.goto('/#settings')
    await expect(page.locator('.settings-content h2').first()).toHaveText('Overview')
  })

  // Legacy deep links from before the settings-panel cleanup must still land
  // on the page that absorbed their content (SECTION_ALIASES).
  test('legacy ids resolve via aliases', async ({ page }) => {
    await page.goto('/#settings/health')
    await expect(page.locator('.settings-content h2').first()).toHaveText('Overview')
    await page.goto('/#settings/about')
    await expect(page.locator('.settings-content h2').first()).toHaveText('Updates')
    await page.goto('/#settings/runtimes')
    await expect(page.locator('.settings-content h2').first()).toHaveText('Hardware & Runtimes')
    await page.goto('/#settings/library')
    await expect(page.locator('.settings-content h2').first()).toHaveText('Loaded Models')
  })

  test('clicking Updates swaps the section', async ({ page }) => {
    await page.goto('/#settings')
    await page.locator('.settings-nav .nav-item', { hasText: 'Updates' }).click()
    await expect(page.locator('.settings-content h2').first()).toHaveText('Updates')
  })

  test('no legacy "Runtime" section remains (#687 Phase E)', async ({ page }) => {
    await page.goto('/#settings')
    // The old singular "Runtime" page is gone; the new D3 "Runtimes" evidence
    // page is a different section (exact-match so it isn't caught here).
    await expect(page.locator('.settings-nav .nav-item', { hasText: /^Runtime$/ })).toHaveCount(0)
  })

  // ML-4-unblocked pages (R5 data seam): both mount without a runtime error
  // (spec risk #5 — the ESM split must thread the old window-globals or pages
  // blow up at click time with no compile error).
  test('Model Defaults section mounts', async ({ page }) => {
    await page.goto('/#settings')
    await page.locator('.settings-nav .nav-item', { hasText: 'Model Defaults' }).click()
    await expect(page.locator('.settings-content h2').first()).toHaveText('Model Defaults')
  })

  test('Hardware & Runtimes section mounts', async ({ page }) => {
    await page.goto('/#settings')
    await page.locator('.settings-nav .nav-item', { hasText: 'Hardware & Runtimes' }).click()
    await expect(page.locator('.settings-content h2').first()).toHaveText('Hardware & Runtimes')
  })

  // The pages absorbed by the cleanup must surface their content on the new
  // owners: library panels on Loaded Models, health panels on Overview.
  test('Loaded Models carries the absorbed library panels', async ({ page }) => {
    await page.goto('/#settings')
    await page.locator('.settings-nav .nav-item', { hasText: 'Loaded Models' }).click()
    await expect(page.locator('.settings-content h2').first()).toHaveText('Loaded Models')
    await expect(page.locator('.settings-content')).toContainText('Models known to the catalog')
    await expect(page.locator('.settings-content')).not.toContainText('not yet wired')
  })

  test('Overview carries the absorbed health panels', async ({ page }) => {
    await page.goto('/#settings')
    await expect(page.locator('.settings-content h2').first()).toHaveText('Overview')
    await expect(page.locator('.settings-content')).toContainText('Health checks')
    await expect(page.locator('.settings-content')).toContainText('Anonymous telemetry')
    await expect(page.locator('.settings-content')).not.toContainText('not yet wired')
  })

  // VERS-flash (docs/rework/handoff-r5-drive2.md §3): index.html no longer
  // hardcodes a version literal — the build-time stamp (ui/package.json,
  // reconciled to the backend release) is what first paints, and App()
  // then syncs document.title to the *live* `/api/updates/state` value
  // (mocked here as hal0.current, see src/api/mock.ts) once it resolves.
  // Asserting against the live-mocked version (not the build-time stamp)
  // proves the post-mount sync actually runs, not just the HTML bake.
  test('document.title syncs to the live hal0 version, not a stale literal', async ({ page }) => {
    await page.goto('/#settings')
    await expect(page).toHaveTitle(/v0\.3\.0-alpha\.1/)
    await expect(page).not.toHaveTitle(/v0\.5\.0-alpha\.1/)
  })

  test('Updates ▸ About panel shows the same live version as the tab title', async ({ page }) => {
    await page.goto('/#settings/updates')
    await expect(page.locator('.settings-content h2').first()).toHaveText('Updates')
    await expect(page.locator('.settings-content')).toContainText('Apache-2.0')
    await expect(page.locator('.settings-content')).toContainText('0.3.0-alpha.1')
  })
})
