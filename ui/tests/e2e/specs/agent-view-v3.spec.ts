/**
 * agent-view-v3 — v0.4 Memory-only AgentView contract.
 *
 * The `#agent` route was reduced to the Memory capability ONLY. Web chat
 * (HermesChatTab) was abandoned in favour of the `hermes chat` TUI, and the
 * Personas / Skills / Plugins tabs were removed (they surfaced fixtures,
 * not live data). The single-tab nav is kept so the route + deep-link
 * shape stay stable.
 *
 * This spec pins:
 *   - the tab nav renders with the lone "Memory" tab, default + selected
 *   - the Memory tab content is visible by default
 *   - the removed tabs (chat / personas / skills / plugins / inbox /
 *     peers / overview) are ABSENT from the nav
 *   - the header carries the `hermes chat` terminal hint
 *   - hash routes `#agent`, `#agent/memory`, and legacy `#peers` all land
 *     on the Memory tab
 */
import { test, expect, json } from '../fixtures/apiMock'

const FIVE_S = 5_500

test.describe('AgentView v3 (#agent — v0.4 Memory-only)', () => {
  test.beforeEach(async ({ page }) => {
    // Stub the live endpoints the Memory tab hits so the spec is hermetic.
    await page.route('**/api/memory/graph/status', (route) =>
      json(route, { enabled: false, route: 'upstream' }),
    )
    await page.route('**/api/memory/search', (route) => json(route, { items: [] }))
  })

  test('default view is the Memory tab; removed tabs are absent', async ({ page }) => {
    await page.goto('/#agent')
    const nav = page.locator('[data-testid="agent-tab-nav"]')
    await expect(nav).toBeVisible({ timeout: FIVE_S })

    // The lone Memory tab is present and selected by default.
    const memoryTabBtn = page.locator('[data-testid="agent-tab-memory"]')
    await expect(memoryTabBtn).toBeVisible()
    await expect(memoryTabBtn).toHaveText('Memory')

    // Memory tab content renders by default.
    await expect(page.locator('[data-testid="memory-tab"]')).toBeVisible()

    // All removed tabs MUST be gone — web chat + personas/skills/plugins
    // (and the older inbox/peers/overview tabs) no longer exist.
    for (const id of ['chat', 'personas', 'skills', 'plugins', 'inbox', 'peers', 'overview']) {
      await expect(page.locator(`[data-testid="agent-tab-${id}"]`)).toHaveCount(0)
    }
    // The removed surfaces' own testids must also be absent.
    await expect(page.locator('[data-testid="hermes-chat-surface"]')).toHaveCount(0)
    await expect(page.locator('[data-testid="personas-tab"]')).toHaveCount(0)
    await expect(page.locator('[data-testid="skills-tab"]')).toHaveCount(0)
    await expect(page.locator('[data-testid="plugins-tab"]')).toHaveCount(0)

    // Removed nav labels must not appear in the tab nav.
    await expect(nav).not.toContainText('Chat')
    await expect(nav).not.toContainText('Personas')
    await expect(nav).not.toContainText('Skills')
    await expect(nav).not.toContainText('Plugins')
  })

  test('header carries the `hermes chat` terminal hint (web chat replaced by TUI)', async ({
    page,
  }) => {
    await page.goto('/#agent')
    await expect(page.locator('.view .vh')).toContainText('hermes chat', { timeout: FIVE_S })
  })

  test('#agent/memory hash routes to the Memory tab', async ({ page }) => {
    await page.goto('/#agent/memory')
    await expect(page.locator('[data-testid="memory-tab"]')).toBeVisible({ timeout: FIVE_S })
  })

  test('legacy #peers route redirects to #agent/memory?subsection=peer', async ({ page }) => {
    await page.goto('/#peers')
    // The redirect happens client-side via hashchange handling inside
    // agent-view.jsx. Wait for the URL to land on the new shape.
    await expect(page).toHaveURL(/#agent\/memory\?subsection=peer/, { timeout: FIVE_S })
    await expect(page.locator('[data-testid="memory-tab"]')).toBeVisible()
  })
})
