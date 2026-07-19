/**
 * settings-v3 — `#settings` route renders the grouped rail nav (SERVER /
 * MODELS / INFERENCE / ROUTING / OBSERVABILITY / DATA / DIAGNOSTICS /
 * INTEGRATIONS) and swaps the right pane on click.
 *
 * Auth section removed per ADR-0012 (PRs #254-#267). #544 pruned the
 * fully-mock OmniRouter/Agent-policy/Memory (Cognee) sections (those
 * surfaces live on MCP + agent views); surviving sections were renamed
 * for accuracy — Models→Storage, Appearance→General. #554 added Voice +
 * Image-gen sections. #687 Phase E removed the Runtime section (the old
 * runtime admin pane) — runtime status now lives on the sidebar rollup
 * + footer chip. #1163 reorganised settings: Memory section added,
 * Default slots→Slots, default landing is General.
 *
 * P3-ui split (settings.jsx → SettingsShell/ESM) regrouped the flat rail
 * into NAV_GROUPS and added visible sections: Security + Hardware Tuning
 * (both rendered `disabled` — gated on unbuilt backend lanes), plus
 * Library & Downloads, Health & Stats, and Doctor. Section list below is
 * sourced from `SettingsNav.jsx`'s NAV_GROUPS — the authoritative IA.
 */
import { test, expect } from '../fixtures/apiMock'

const SECTIONS = [
  'General', 'Security',
  'Loaded Models', 'Library & Downloads', 'Model Defaults',
  'Backend & GPU', 'Hardware Tuning', 'NPU', 'Voice', 'Image-gen',
  'Agents / Brain',
  'Health & Stats',
  'Storage', 'Memory',
  'Doctor', 'Updates', 'Runtimes', 'Advanced', 'About',
  'Secrets',
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

  test('default section is General', async ({ page }) => {
    await page.goto('/#settings')
    await expect(page.locator('.settings-content h2').first()).toHaveText('General')
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

  test('Backend & GPU section mounts', async ({ page }) => {
    await page.goto('/#settings')
    await page.locator('.settings-nav .nav-item', { hasText: 'Backend & GPU' }).click()
    await expect(page.locator('.settings-content h2').first()).toHaveText('Backend & GPU')
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

  test('About section shows the same live version as the tab title', async ({ page }) => {
    await page.goto('/#settings')
    await page.locator('.settings-nav .nav-item', { hasText: 'About' }).click()
    await expect(page.locator('.settings-content h2').first()).toHaveText('About')
    await expect(page.locator('.s-panel')).toContainText('0.3.0-alpha.1')
  })
})
