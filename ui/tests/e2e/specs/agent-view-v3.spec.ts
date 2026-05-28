/**
 * agent-view-v3 — v0.3 PR-8 (master plan §4 PR-8).
 *
 * Pins the post-refactor AgentView contract:
 *   - HermesChat is the default tab (placeholder lives here until PR-10)
 *   - Tab nav covers chat / personas / skills / memory / plugins
 *   - Inbox and Peers tabs are GONE (Inbox → sidebar pip; Peers → folded
 *     into Memory as "Peer memory" subsection)
 *   - Hash routes `#agent/<tab>` work; legacy `#peers` redirects to
 *     `#agent/memory?subsection=peer`
 */
import { test, expect, json } from '../fixtures/apiMock'

const FIVE_S = 5_500

test.describe('AgentView v3 (#agent — PR-8 refactor)', () => {
  test.beforeEach(async ({ page }) => {
    // Stub the live endpoints the new tabs hit so the spec is hermetic.
    await page.route('**/api/agents/hermes/personas', (route) =>
      json(route, {
        agent_id: 'hermes',
        active: 'default',
        personas: [
          { id: 'default', display_name: 'Hermes', description: 'Default', active: true },
        ],
      }),
    )
    await page.route('**/api/memory/graph/status', (route) =>
      json(route, { enabled: false, route: 'upstream' }),
    )
    await page.route('**/api/memory/search', (route) =>
      json(route, { items: [] }),
    )
  })

  test('default tab is chat — surface visible, no Inbox or Peers in nav', async ({ page }) => {
    await page.goto('/#agent')
    await expect(page.locator('[data-testid="agent-tab-nav"]')).toBeVisible({ timeout: FIVE_S })

    // Default tab content is the HermesChat surface (PR-10 replaced
    // PR-8's placeholder with the composer/transcript/sidecar grid).
    await expect(page.locator('[data-testid="hermes-chat-surface"]')).toBeVisible()

    // Nav contains the new 5-tab set, and ONLY that set.
    for (const id of ['chat', 'personas', 'skills', 'memory', 'plugins']) {
      await expect(page.locator(`[data-testid="agent-tab-${id}"]`)).toBeVisible()
    }
    // The dropped tabs MUST NOT be present.
    await expect(page.locator('[data-testid="agent-tab-inbox"]')).toHaveCount(0)
    await expect(page.locator('[data-testid="agent-tab-peers"]')).toHaveCount(0)
    await expect(page.locator('[data-testid="agent-tab-overview"]')).toHaveCount(0)

    // The label text "Inbox" / "Peers" must not appear in the nav.
    const nav = page.locator('[data-testid="agent-tab-nav"]')
    await expect(nav).not.toContainText('Inbox')
    await expect(nav).not.toContainText('Peers')
  })

  test('clicking Personas, Skills, Memory, Plugins swaps content', async ({ page }) => {
    await page.goto('/#agent')

    await page.locator('[data-testid="agent-tab-personas"]').click()
    await expect(page.locator('[data-testid="personas-tab"]')).toBeVisible({ timeout: FIVE_S })

    await page.locator('[data-testid="agent-tab-skills"]').click()
    await expect(page.locator('[data-testid="skills-tab"]')).toBeVisible()

    await page.locator('[data-testid="agent-tab-memory"]').click()
    await expect(page.locator('[data-testid="memory-tab"]')).toBeVisible()

    await page.locator('[data-testid="agent-tab-plugins"]').click()
    await expect(page.locator('[data-testid="plugins-tab"]')).toBeVisible()
  })

  test('Memory tab shows the "Peer memory" subsection', async ({ page }) => {
    await page.goto('/#agent/memory')
    await expect(page.locator('[data-testid="memory-tab"]')).toBeVisible({ timeout: FIVE_S })
    const peer = page.locator('[data-testid="peer-memory-section"]')
    await expect(peer).toBeVisible()
    await expect(peer).toContainText('Peer memory')
  })

  test('legacy #peers route redirects to #agent/memory?subsection=peer', async ({ page }) => {
    await page.goto('/#peers')
    // The redirect happens client-side via hashchange handling inside
    // agent-view.jsx. Wait for the URL to land on the new shape.
    await expect(page).toHaveURL(/#agent\/memory\?subsection=peer/, { timeout: FIVE_S })
    await expect(page.locator('[data-testid="memory-tab"]')).toBeVisible()
  })

  test('#agent/chat hash routes to the chat tab', async ({ page }) => {
    await page.goto('/#agent/chat')
    await expect(page.locator('[data-testid="hermes-chat-surface"]')).toBeVisible({ timeout: FIVE_S })
  })
})
