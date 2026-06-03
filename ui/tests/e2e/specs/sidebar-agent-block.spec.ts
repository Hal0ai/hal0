/**
 * sidebar-agent-block-v3 — v0.4 W9 honest-minimal rewrite.
 *
 * Pins the SidebarAgentBlock UI contract: a compact agent rollup mounted
 * in the left sidebar above the lemond status block.
 *
 * W9 simplification (wave-1): the widget no longer renders approvals /
 * skills / memory-writes / MCP-pip rows. Those leaned on endpoints that
 * frequently 404 and surfaced misleading "—" placeholders. The widget is
 * now an honest health indicator only:
 *
 *   - health dot + status label, keyed off /api/agents `status`
 *     (installed→running/up, broken→down, else unknown→warn)
 *   - an OPTIONAL active-profile row, only when a persona name exists
 *   - an "Open chat →" button → onGo("agent")
 *   - empty state when no agent installed → "Install Hermes →" CTA
 *
 * Tests below assert the new minimal surface; removed-row testids
 * (sidebar-agent-approvals/skills/memory/mcp) MUST be absent.
 */
import { test, expect, json } from '../fixtures/apiMock'

const FIVE_S = 5_500

const MOCK_AGENTS_INSTALLED = {
  agents: [{ name: 'hermes', installed_at: '2026-05-25T12:00:00Z', status: 'installed' }],
  count: 1,
}

const MOCK_AGENTS_EMPTY = { agents: [], count: 0 }

const MOCK_PERSONAS = {
  agent_id: 'hermes',
  active: 'default',
  personas: [
    { id: 'default', display_name: 'Hermes', description: 'Default persona', active: true },
    { id: 'coder', display_name: 'Hermes Coder', description: 'Coder persona', active: false },
  ],
}

const MOCK_APPROVALS_PENDING = {
  approvals: [
    { id: 'a1', tool: 'shell_exec', args: { cmd: 'ls' } },
    { id: 'a2', tool: 'fs_write', args: { path: '/tmp/x' } },
    { id: 'a3', tool: 'model_pull', args: { id: 'qwen3' } },
  ],
}

const MOCK_APPROVALS_EMPTY = { approvals: [] }

const MOCK_MCP_GREEN = {
  servers: [
    { id: 'hal0-admin', name: 'hal0-admin', bundled: true, state: 'running' },
    { id: 'hal0-memory', name: 'hal0-memory', bundled: true, state: 'running' },
  ],
  count: 2,
}

test.describe('SidebarAgentBlock — populated', () => {
  test.beforeEach(async ({ page }) => {
    await page.route('**/api/agents', (route) => json(route, MOCK_AGENTS_INSTALLED))
    await page.route('**/api/agents/hermes/personas', (route) => json(route, MOCK_PERSONAS))
    // The W9 widget no longer reads approvals/skills/memory/mcp, but other
    // sidebar surfaces may still poll these — keep harmless stubs so no
    // request hits the live proxy and slows the spec.
    await page.route('**/api/agent/approvals', (route) => json(route, MOCK_APPROVALS_PENDING))
    await page.route('**/api/mcp/servers', (route) => json(route, MOCK_MCP_GREEN))
  })

  test('renders agent status row with running dot (keyed off /api/agents status)', async ({
    page,
  }) => {
    await page.goto('/')
    const block = page.locator('[data-testid="sidebar-agent-block"]')
    await expect(block).toBeVisible({ timeout: FIVE_S })
    // First row: key = agent id ("hermes"), value = "running" with the
    // "up" (green dot) class. status:'installed' maps to running per
    // useSidebarAgentRollup.
    const statusRow = block.locator('.row').first()
    await expect(statusRow.locator('.k')).toHaveText('hermes')
    await expect(statusRow.locator('.v')).toContainText('running')
    await expect(statusRow.locator('.v')).toHaveClass(/up/)
    // Health dot is present.
    await expect(statusRow.locator('.v .dot')).toBeVisible()
  })

  test('renders active persona display name', async ({ page }) => {
    await page.goto('/')
    const persona = page.locator('[data-testid="sidebar-agent-persona"]')
    await expect(persona).toBeVisible({ timeout: FIVE_S })
    await expect(persona).toHaveText('Hermes')
  })

  test('does NOT render removed approvals/skills/memory/mcp rows', async ({ page }) => {
    await page.goto('/')
    await expect(page.locator('[data-testid="sidebar-agent-block"]')).toBeVisible({
      timeout: FIVE_S,
    })
    // W9 removed these rows entirely — they must be absent from the DOM.
    await expect(page.locator('[data-testid="sidebar-agent-approvals"]')).toHaveCount(0)
    await expect(page.locator('[data-testid="sidebar-agent-skills"]')).toHaveCount(0)
    await expect(page.locator('[data-testid="sidebar-agent-memory"]')).toHaveCount(0)
    await expect(page.locator('[data-testid="sidebar-agent-mcp"]')).toHaveCount(0)
  })

  test('Memory CTA navigates to #agent + inline TUI hint present', async ({ page }) => {
    // v0.4: web chat is gone — the CTA is now "Memory →" (still onGo("agent"))
    // and an inline `hal0 chat` terminal hint sits below it.
    await page.goto('/')
    const cta = page.locator('[data-testid="sidebar-agent-open-memory"]')
    await expect(cta).toBeVisible({ timeout: FIVE_S })
    await expect(cta).toContainText('Memory')
    const hint = page.locator('[data-testid="sidebar-agent-tui-hint"]')
    await expect(hint).toContainText('hal0 chat')
    await cta.click()
    await expect(page).toHaveURL(/#agent/)
  })
})

test.describe('SidebarAgentBlock — empty state', () => {
  test.beforeEach(async ({ page }) => {
    await page.route('**/api/agents', (route) => json(route, MOCK_AGENTS_EMPTY))
    // The rest can 404 — the empty path doesn't read them.
    await page.route('**/api/agent/approvals', (route) => json(route, MOCK_APPROVALS_EMPTY))
    await page.route('**/api/mcp/servers', (route) => json(route, { servers: [], count: 0 }))
  })

  test('renders Install Hermes CTA when no agent installed', async ({ page }) => {
    await page.goto('/')
    const cta = page.locator('[data-testid="sidebar-agent-install"]')
    await expect(cta).toBeVisible({ timeout: FIVE_S })
    await expect(cta).toHaveText(/Install Hermes/)
    // CTA points at the docs/installer per master plan §4 PR-6.
    await expect(cta).toHaveAttribute('href', /docs\/installer/)
    // Populated block must NOT render in empty state.
    await expect(page.locator('[data-testid="sidebar-agent-block"]')).toHaveCount(0)
  })
})

test.describe('SidebarAgentBlock — status tone mapping', () => {
  test('broken agent renders down (red) dot', async ({ page }) => {
    await page.route('**/api/agents', (route) =>
      json(route, {
        agents: [{ name: 'hermes', installed_at: '2026-05-25T12:00:00Z', status: 'broken' }],
        count: 1,
      }),
    )
    await page.route('**/api/agents/hermes/personas', (route) => json(route, MOCK_PERSONAS))
    await page.goto('/')
    const statusRow = page.locator('[data-testid="sidebar-agent-block"] .row').first()
    await expect(statusRow.locator('.v')).toHaveClass(/down/)
    await expect(statusRow.locator('.v')).toContainText('broken')
  })

  test('unknown status renders warn (amber) dot with em-dash label', async ({ page }) => {
    await page.route('**/api/agents', (route) =>
      json(route, {
        agents: [{ name: 'hermes', installed_at: '2026-05-25T12:00:00Z', status: 'starting' }],
        count: 1,
      }),
    )
    await page.route('**/api/agents/hermes/personas', (route) => json(route, MOCK_PERSONAS))
    await page.goto('/')
    const statusRow = page.locator('[data-testid="sidebar-agent-block"] .row').first()
    await expect(statusRow.locator('.v')).toHaveClass(/warn/)
    await expect(statusRow.locator('.v')).toHaveText('—')
  })
})

test.describe('SidebarAgentBlock — profile row is conditional', () => {
  test('omits the profile row when no active persona', async ({ page }) => {
    await page.route('**/api/agents', (route) => json(route, MOCK_AGENTS_INSTALLED))
    // active=null and no matching persona → personaName resolves to null,
    // so the profile row must NOT render.
    await page.route('**/api/agents/hermes/personas', (route) =>
      json(route, { agent_id: 'hermes', active: null, personas: [] }),
    )
    await page.goto('/')
    const block = page.locator('[data-testid="sidebar-agent-block"]')
    await expect(block).toBeVisible({ timeout: FIVE_S })
    // Status row + Memory CTA still render; profile row absent.
    await expect(page.locator('[data-testid="sidebar-agent-persona"]')).toHaveCount(0)
    await expect(page.locator('[data-testid="sidebar-agent-open-memory"]')).toBeVisible()
  })
})
