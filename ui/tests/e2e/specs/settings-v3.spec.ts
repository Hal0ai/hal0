/**
 * settings-v3 — `#settings` route renders the rail nav with all 11
 * sections (general, slots, npu, memory, voice, imagegen, storage,
 * secrets, updates, advanced, about) and swaps the right pane on click.
 *
 * Auth section removed per ADR-0012 (PRs #254-#267). #544 pruned the
 * fully-mock OmniRouter/Agent-policy/Memory (Cognee) sections (those
 * surfaces live on MCP + agent views); surviving sections were renamed
 * for accuracy — Models→Storage, Appearance→General. #554 added Voice +
 * Image-gen sections. #687 Phase E removed the Runtime section (the old
 * runtime admin pane) — runtime status now lives on the sidebar rollup
 * + footer chip. #1163 reorganised settings: Memory section added,
 * Default slots→Slots, default landing is General.
 */
import { test, expect } from '../fixtures/apiMock'

const SECTIONS = [
  'General', 'Slots', 'NPU', 'Memory', 'Agents / Brain', 'Voice', 'Image-gen', 'Storage', 'Secrets', 'Updates', 'Advanced', 'About',
]

test.describe('Settings v3 (/settings)', () => {
  test('renders rail nav with all 12 sections', async ({ page }) => {
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

  test('no Runtime section remains (#687 Phase E)', async ({ page }) => {
    await page.goto('/#settings')
    await expect(page.locator('.settings-nav .nav-item', { hasText: 'Runtime' })).toHaveCount(0)
  })
})
