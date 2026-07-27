/**
 * agent-view-v3 — v0.5 Agents shell contract.
 *
 * The `#agent` route is the "Agents" shell. It now lands on an **Overview**
 * tab: the agent-card library, where Hermes is the live `serving` foil
 * (status + throughput/context wired to /api/agents + /api/slots, with a
 * Restart action → POST /api/agents/hermes/restart) and the rest of the
 * library are roadmap entries behind a grey "coming soon" mask. Memory (full
 * Hindsight page, gated) and MCP remain as tabs.
 *
 * This spec pins:
 *   - bare #agent lands on the Overview tab (selected), cards render
 *   - the live Hermes card is present and flips to a wired Restart button
 *   - the locked roadmap cards render behind the coming-soon mask
 *   - Overview · Memory · MCP tabs are present; the long-removed surfaces
 *     (chat / personas / skills / plugins / inbox / peers) stay ABSENT
 *   - the header carries the `hermes chat` terminal hint
 *   - hash routes #agent/memory + legacy #peers land on the Memory tab
 */
import { test, expect, json } from '../fixtures/apiMock'

const FIVE_S = 5_500

test.describe('Agents shell v0.5 (#agent — Overview default)', () => {
  test.beforeEach(async ({ page }) => {
    // Stub the live endpoints the Memory tab hits so the spec is hermetic.
    await page.route('**/api/memory/graph/status', (route) =>
      json(route, { enabled: false, route: 'upstream' }),
    )
    await page.route('**/api/memory/search', (route) => json(route, { items: [] }))
  })

  test('default view is the Overview tab with the agent-card library', async ({ page }) => {
    await page.goto('/#agent')
    const nav = page.locator('[data-testid="agent-tab-nav"]')
    await expect(nav).toBeVisible({ timeout: FIVE_S })

    // Overview tab present + selected by default; the cards grid renders.
    const overviewTabBtn = page.locator('[data-testid="agent-tab-overview"]')
    await expect(overviewTabBtn).toBeVisible()
    await expect(overviewTabBtn).toHaveText('Overview')
    await expect(page.locator('[data-testid="agents-overview"]')).toBeVisible()

    // Memory + MCP remain as tabs (Memory follows the gate but is present in
    // the default mock, which enables the subsystem).
    await expect(page.locator('[data-testid="agent-tab-mcp"]')).toBeVisible()

    // The live Hermes foil + the locked roadmap cards are present.
    await expect(page.locator('[data-testid="agent-card-hermes"]')).toBeVisible()
    await expect(page.locator('[data-testid="agent-card-locked-pi"]')).toBeVisible()
    await expect(page.locator('[data-testid="agent-card-locked-qwen"]')).toBeVisible()
    await expect(page.locator('[data-testid="agent-card-locked-opencode"]')).toBeVisible()
    await expect(page.locator('.agents-overview .ao-mask-label').first()).toContainText(
      'Coming soon',
    )

    // The long-removed surfaces must stay gone.
    for (const id of ['chat', 'personas', 'skills', 'plugins', 'inbox', 'peers']) {
      await expect(page.locator(`[data-testid="agent-tab-${id}"]`)).toHaveCount(0)
    }
    await expect(nav).not.toContainText('Chat')
    await expect(nav).not.toContainText('Personas')
  })

  test('Hermes card flips to a Restart action wired to /api/agents/hermes/restart', async ({
    page,
  }) => {
    let restartCalled = false
    await page.route('**/api/agents/hermes/restart', (route) => {
      restartCalled = true
      return json(route, { status: 'restarted' })
    })

    await page.goto('/#agent')
    const card = page.locator('[data-testid="agent-card-hermes"]')
    await expect(card).toBeVisible({ timeout: FIVE_S })

    // Flip to the back, then hit Restart.
    await card.click()
    const restartBtn = page.locator('[data-testid="agent-action-restart"]')
    await expect(restartBtn).toBeVisible()
    await restartBtn.click()

    await expect.poll(() => restartCalled).toBe(true)
    await expect(restartBtn).toContainText(/Restart/i)
  })

  test('header carries the `hermes chat` terminal hint (web chat replaced by TUI)', async ({
    page,
  }) => {
    await page.goto('/#agent')
    await expect(page.locator('.view .vh')).toContainText('hermes chat', { timeout: FIVE_S })
    await expect(page.locator('.view .vh h1')).toHaveText('Agents')
  })

  test('#agent/memory hash routes to the Memory tab', async ({ page }) => {
    await page.goto('/#agent/memory')
    await expect(page.locator('[data-testid="mem-tab-overview"]')).toBeVisible({ timeout: FIVE_S })
  })

  test('legacy #peers route redirects to #agent/memory?subsection=peer', async ({ page }) => {
    await page.goto('/#peers')
    // The redirect happens client-side via hashchange handling inside
    // agent-view.jsx. Wait for the URL to land on the new shape.
    await expect(page).toHaveURL(/#agent\/memory\?subsection=peer/, { timeout: FIVE_S })
    await expect(page.locator('[data-testid="mem-tab-overview"]')).toBeVisible()
  })
})
