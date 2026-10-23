/**
 * agent-view-v3 — v0.5 Agents shell contract.
 *
 * The `#agent` route is the "Agents" shell. It lands on an **Overview** tab:
 * the agent-card library. Hermes and Pi are state-aware cards (their face
 * follows `GET /api/agents/{name}/overview` and falls back to the
 * `/api/agents` record: agents-overview.jsx + agent-phase.ts) with a console
 * on the back; the rest of the library are roadmap entries behind a grey
 * "coming soon" mask. Memory (full Hindsight page, gated) and MCP remain as
 * tabs. The per-phase faces are pinned in agent-cards-phases-v3, the upgrade
 * and repair jobs in agent-jobs-v3; this spec pins the shell around them:
 *   - bare #agent lands on the Overview tab (selected), cards render
 *   - an installed Hermes card flips to a Restart button wired to
 *     POST /api/agents/hermes/restart
 *   - an installed Pi card (a CLI tool with no systemd unit) flips to copyable
 *     commands and has no Restart/Persona/Logs actions
 *   - the locked roadmap cards render behind the coming-soon mask
 *   - Overview · Memory · MCP tabs are present; the long-removed surfaces
 *     (chat / personas / skills / plugins / inbox / peers) stay ABSENT
 *   - the header no longer carries the `hermes chat` terminal hint (copy-only
 *     removal, 2026-08-22 — web chat is still replaced by the TUI; only the
 *     visible reminder string was dropped, not the underlying behavior)
 *   - hash route #agent/memory lands on the Memory tab
 */
import { test, expect, json } from '../fixtures/apiMock'

const FIVE_S = 5_500

const HERMES_RECORD = {
  name: 'hermes',
  installed_at: '2026-07-30T00:00:00Z',
  status: 'installed',
  data_dir: '/var/lib/hal0/agents/hermes',
  config_path: '/etc/hal0/agents/hermes.toml',
  unit_active: true,
}

const PI_RECORD = {
  name: 'pi',
  installed_at: '2026-07-30T00:00:00Z',
  status: 'installed',
  unit_active: null,
}

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

    // The live Hermes + Pi foils, and the locked roadmap cards, are present.
    await expect(page.locator('[data-testid="agent-card-hermes"]')).toBeVisible()
    await expect(page.locator('[data-testid="agent-card-pi"]')).toBeVisible()
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
    // A card only offers Restart for an installed Hermes: give it the record
    // and an overview that agrees (the default `{}` mock would leave it
    // not-installed, with an install command instead).
    await page.route('**/api/agents', (route) =>
      json(route, { agents: [HERMES_RECORD], count: 1 }),
    )
    await page.route('**/api/agents/hermes/overview*', (route) =>
      json(route, {
        name: 'hermes',
        kind: 'daemon',
        installed: true,
        status: 'installed',
        unit_active: true,
        phase: 'ready',
        degraded: [],
        units: { agent: 'active', gateway: 'active', gateway_enabled: true },
      }),
    )
    let restartCalled = false
    await page.route('**/api/agents/hermes/restart', (route) => {
      restartCalled = true
      return json(route, { status: 'restarted' })
    })

    await page.goto('/#agent')
    const card = page.locator('[data-testid="agent-card-hermes"]')
    await expect(card).toBeVisible({ timeout: FIVE_S })

    // Flip to the back (click the header strip, clear of every control), then
    // hit Restart on the Controls tab.
    await card.click({ position: { x: 170, y: 24 } })
    const restartBtn = card.locator('[data-testid="agent-action-restart"]')
    await expect(restartBtn).toBeVisible()
    await restartBtn.click()

    await expect.poll(() => restartCalled).toBe(true)
    await expect(page.locator('.hal0-toast.success', { hasText: 'Hermes restarted' })).toBeVisible()
  })

  test('Pi card flips to copyable commands with no Restart/Persona actions (CLI tool, no unit)', async ({
    page,
  }) => {
    // An installed Pi, so the back is the CLI controls and not the install pane.
    await page.route('**/api/agents', (route) => json(route, { agents: [PI_RECORD], count: 1 }))
    await page.goto('/#agent')
    const card = page.locator('[data-testid="agent-card-pi"]')
    await expect(card).toBeVisible({ timeout: FIVE_S })

    await card.click({ position: { x: 170, y: 24 } })
    // Positive control: the flip happened and the CLI controls are showing.
    await expect(card.locator('[data-testid="agent-action-copy-upgrade"]')).toBeVisible()
    // Scoped to Pi's own card — Hermes' Restart button would otherwise leak
    // into a page-wide query.
    await expect(card.locator('[data-testid="agent-action-restart"]')).toHaveCount(0)
    await expect(card.locator('[data-testid="agent-action-persona"]')).toHaveCount(0)
    await expect(card.locator('[data-testid="agent-action-logs"]')).toHaveCount(0)
  })

  test('header no longer carries the `hermes chat` terminal hint (copy-only removal)', async ({
    page,
  }) => {
    await page.goto('/#agent')
    await expect(page.locator('.view .vh h1')).toHaveText('Agents')
    await expect(page.locator('.view .vh')).not.toContainText('hermes chat')
    await expect(page.locator('.view .vh .hint')).toHaveCount(0)
  })

  test('#agent/memory hash routes to the Memory tab', async ({ page }) => {
    await page.goto('/#agent/memory')
    await expect(page.locator('[data-testid="mem-tab-overview"]')).toBeVisible({ timeout: FIVE_S })
  })
})
