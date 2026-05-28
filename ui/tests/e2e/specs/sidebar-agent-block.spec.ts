/**
 * sidebar-agent-block-v3 — v0.3 PR-6 (master plan §4 PR-6).
 *
 * Pins the SidebarAgentBlock UI contract: a compact agent rollup mounted
 * in the left sidebar above the lemond status block. Replaces the
 * stats card that used to live on the Agents page Overview tab.
 *
 * The block reads from /api/agents (+ /personas + approvals + skills +
 * mcp servers + memory stats) via useSidebarAgentRollup. We mock each
 * endpoint here so the spec is hermetic, then exercise:
 *
 *   - populated state: every metric renders + Open chat navigates
 *   - empty state: no agent installed → "Install Hermes →" CTA
 *   - approvals badge: hides when 0, shows red badge when > 0
 *   - missing endpoints: skills + memory render "—" without crashing
 *   - MCP pip: green/yellow/red colour matches /api/mcp/servers state
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

const MOCK_MCP_DEGRADED = {
  servers: [
    { id: 'hal0-admin', name: 'hal0-admin', bundled: true, state: 'running' },
    { id: 'hal0-memory', name: 'hal0-memory', bundled: true, state: 'stopped' },
  ],
  count: 2,
}

const MOCK_MCP_DOWN = {
  servers: [
    { id: 'hal0-admin', name: 'hal0-admin', bundled: true, state: 'failed' },
    { id: 'hal0-memory', name: 'hal0-memory', bundled: true, state: 'running' },
  ],
  count: 2,
}

test.describe('SidebarAgentBlock — populated', () => {
  test.beforeEach(async ({ page }) => {
    await page.route('**/api/agents', (route) => json(route, MOCK_AGENTS_INSTALLED))
    await page.route('**/api/agents/hermes/personas', (route) => json(route, MOCK_PERSONAS))
    await page.route('**/api/agent/approvals', (route) => json(route, MOCK_APPROVALS_PENDING))
    await page.route('**/api/agents/skills', (route) =>
      json(route, { skills: new Array(12).fill({ name: 'skill', cap: 'read' }), count: 12 }),
    )
    await page.route('**/api/agents/hermes/memory/stats', (route) =>
      json(route, { writes: 847 }),
    )
    await page.route('**/api/mcp/servers', (route) => json(route, MOCK_MCP_GREEN))
  })

  test('renders agent status row with running dot', async ({ page }) => {
    await page.goto('/')
    const block = page.locator('[data-testid="sidebar-agent-block"]')
    await expect(block).toBeVisible({ timeout: FIVE_S })
    // First row labelled "agent" with running state.
    const agentRow = block.locator('.row').filter({ hasText: 'agent' }).first()
    await expect(agentRow.locator('.v')).toContainText('running')
    await expect(agentRow.locator('.v')).toHaveClass(/up/)
  })

  test('renders active persona display name', async ({ page }) => {
    await page.goto('/')
    const persona = page.locator('[data-testid="sidebar-agent-persona"]')
    await expect(persona).toBeVisible({ timeout: FIVE_S })
    await expect(persona).toHaveText('Hermes')
  })

  test('renders approvals badge with pending count', async ({ page }) => {
    await page.goto('/')
    const approvals = page.locator('[data-testid="sidebar-agent-approvals"]')
    await expect(approvals).toBeVisible({ timeout: FIVE_S })
    await expect(approvals.locator('.badge')).toHaveText('3')
  })

  test('renders skills count', async ({ page }) => {
    await page.goto('/')
    const skills = page.locator('[data-testid="sidebar-agent-skills"]')
    await expect(skills).toBeVisible({ timeout: FIVE_S })
    await expect(skills).toContainText('12')
  })

  test('renders memory writes count', async ({ page }) => {
    await page.goto('/')
    const memory = page.locator('[data-testid="sidebar-agent-memory"]')
    await expect(memory).toBeVisible({ timeout: FIVE_S })
    await expect(memory).toContainText('847')
  })

  test('renders MCP pip green when bundled servers running', async ({ page }) => {
    await page.goto('/')
    const mcp = page.locator('[data-testid="sidebar-agent-mcp"]')
    await expect(mcp).toBeVisible({ timeout: FIVE_S })
    await expect(mcp).toContainText('ok')
  })

  test('Open chat button navigates to #agent', async ({ page }) => {
    await page.goto('/')
    const cta = page.locator('[data-testid="sidebar-agent-open-chat"]')
    await expect(cta).toBeVisible({ timeout: FIVE_S })
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

test.describe('SidebarAgentBlock — degraded MCP states', () => {
  async function _seedCommon(page: import('@playwright/test').Page) {
    await page.route('**/api/agents', (route) => json(route, MOCK_AGENTS_INSTALLED))
    await page.route('**/api/agents/hermes/personas', (route) => json(route, MOCK_PERSONAS))
    await page.route('**/api/agent/approvals', (route) => json(route, MOCK_APPROVALS_EMPTY))
    await page.route('**/api/agents/skills', (route) =>
      json(route, { skills: [], count: 0 }),
    )
    await page.route('**/api/agents/hermes/memory/stats', (route) =>
      json(route, { writes: 0 }),
    )
  }

  test('MCP pip shows degraded when one bundled server stopped', async ({ page }) => {
    await _seedCommon(page)
    await page.route('**/api/mcp/servers', (route) => json(route, MOCK_MCP_DEGRADED))
    await page.goto('/')
    const mcp = page.locator('[data-testid="sidebar-agent-mcp"]')
    await expect(mcp).toContainText('degraded', { timeout: FIVE_S })
  })

  test('MCP pip shows down when bundled server failed', async ({ page }) => {
    await _seedCommon(page)
    await page.route('**/api/mcp/servers', (route) => json(route, MOCK_MCP_DOWN))
    await page.goto('/')
    const mcp = page.locator('[data-testid="sidebar-agent-mcp"]')
    await expect(mcp).toContainText('down', { timeout: FIVE_S })
  })
})

test.describe('SidebarAgentBlock — missing endpoints degrade gracefully', () => {
  test('renders em-dash for skills + memory when endpoints 404', async ({ page }) => {
    await page.route('**/api/agents', (route) => json(route, MOCK_AGENTS_INSTALLED))
    await page.route('**/api/agents/hermes/personas', (route) => json(route, MOCK_PERSONAS))
    await page.route('**/api/agent/approvals', (route) => json(route, MOCK_APPROVALS_EMPTY))
    // Skills + memory 404 — sidebar must NOT crash; renders "—".
    await page.route('**/api/agents/skills', (route) =>
      route.fulfill({
        status: 404,
        contentType: 'application/json',
        body: JSON.stringify({
          error: { code: 'route.not_found', message: 'unknown route', details: {} },
        }),
      }),
    )
    await page.route('**/api/agents/hermes/memory/stats', (route) =>
      route.fulfill({
        status: 404,
        contentType: 'application/json',
        body: JSON.stringify({
          error: { code: 'route.not_found', message: 'unknown route', details: {} },
        }),
      }),
    )
    await page.route('**/api/memory/list*', (route) =>
      route.fulfill({
        status: 404,
        contentType: 'application/json',
        body: JSON.stringify({
          error: { code: 'route.not_found', message: 'unknown route', details: {} },
        }),
      }),
    )
    await page.route('**/api/mcp/servers', (route) => json(route, MOCK_MCP_GREEN))

    // Capture console warns so we can assert the one-shot
    // hal0.sidebar.endpoint_missing fires.
    const warnings: string[] = []
    page.on('console', (msg) => {
      if (msg.type() === 'warning') warnings.push(msg.text())
    })

    await page.goto('/')
    const block = page.locator('[data-testid="sidebar-agent-block"]')
    await expect(block).toBeVisible({ timeout: FIVE_S })

    // Skills + memory rows render the em-dash sentinel, NOT a number.
    await expect(page.locator('[data-testid="sidebar-agent-skills"]')).toHaveText('—')
    await expect(page.locator('[data-testid="sidebar-agent-memory"]')).toHaveText('—')

    // At least one endpoint_missing warning emitted. (We don't check
    // exact count because retries + first-call timing varies; the
    // important thing is that SOMETHING surfaces in the console.)
    await page.waitForTimeout(500)
    expect(warnings.some((w) => w.includes('hal0.sidebar.endpoint_missing'))).toBe(true)
  })
})

test.describe('SidebarAgentBlock — approvals badge zero-suppression', () => {
  test.beforeEach(async ({ page }) => {
    await page.route('**/api/agents', (route) => json(route, MOCK_AGENTS_INSTALLED))
    await page.route('**/api/agents/hermes/personas', (route) => json(route, MOCK_PERSONAS))
    await page.route('**/api/agent/approvals', (route) => json(route, MOCK_APPROVALS_EMPTY))
    await page.route('**/api/agents/skills', (route) =>
      json(route, { skills: new Array(4).fill({ name: 's' }), count: 4 }),
    )
    await page.route('**/api/agents/hermes/memory/stats', (route) =>
      json(route, { writes: 12 }),
    )
    await page.route('**/api/mcp/servers', (route) => json(route, MOCK_MCP_GREEN))
  })

  test('renders bare "0" without red badge when no approvals pending', async ({ page }) => {
    await page.goto('/')
    const approvals = page.locator('[data-testid="sidebar-agent-approvals"]')
    await expect(approvals).toBeVisible({ timeout: FIVE_S })
    // No `.badge` chip when count is 0; just a plain "0".
    await expect(approvals.locator('.badge')).toHaveCount(0)
    await expect(approvals).toHaveText('0')
  })
})
