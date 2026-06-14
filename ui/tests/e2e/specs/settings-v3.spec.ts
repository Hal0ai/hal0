/**
 * settings-v3 — `#settings` route renders the rail nav with all 8
 * sections (secrets, storage, updates, voice, image-gen, default slots,
 * general, about) and swaps the right pane on click.
 *
 * Auth section removed per ADR-0012 (PRs #254-#267); default landing
 * is now Secrets. #544 pruned the fully-mock OmniRouter/Agent-policy/
 * Memory (Cognee) sections (those surfaces live on MCP + agent views);
 * surviving sections were renamed for accuracy — Models→Storage,
 * Appearance→General. #554 added Voice + Image-gen sections.
 * #687 Phase E removed the Runtime section (the old runtime admin pane) —
 * runtime status now lives on the sidebar rollup + footer chip.
 * Task 6 added the Default slots section (relocated from slot edit drawer).
 */
import { test, expect } from '../fixtures/apiMock'

const SECTIONS = [
  'Secrets', 'Storage', 'Updates', 'Voice', 'Image-gen', 'Default slots', 'General', 'About',
]

test.describe('Settings v3 (/settings)', () => {
  test('renders rail nav with all 8 sections', async ({ page }) => {
    await page.goto('/#settings')
    await expect(page.locator('.view .vh h1')).toHaveText('Settings')
    const nav = page.locator('.settings-nav .nav-item')
    expect(await nav.count()).toBe(SECTIONS.length)
    for (const label of SECTIONS) {
      await expect(page.locator('.settings-nav .nav-item', { hasText: label })).toBeVisible()
    }
  })

  test('default section is Secrets', async ({ page }) => {
    await page.goto('/#settings')
    await expect(page.locator('.settings-content h2').first()).toHaveText('Secrets')
  })

  test('clicking Updates swaps the section', async ({ page }) => {
    await page.goto('/#settings')
    await page.locator('.settings-nav .nav-item', { hasText: 'Updates' }).click()
    await expect(page.locator('.settings-content h2').first()).toHaveText('Updates')
  })

  test('no Runtime section remains (#687 Phase E)', async ({ page }) => {
    await page.goto('/#settings')
    await expect(page.locator('.settings-nav .nav-item', { hasText: 'Runtime' })).toHaveCount(0)
  })
})
