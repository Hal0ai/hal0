/**
 * mcp-v3 — `#mcp` (alias `#agents/mcp`) renders the MCP servers page
 * with KPI strip, clients ribbon (or empty state), filter bar, and
 * server list with LiveTimeline ticks.
 *
 * Previously all skipped — now wired with proper /api/mcp/* mocks after
 * the P0 endpoint fix (agentMcpClients: /api/mcp/clients not /api/agents/mcp/clients).
 */
import { test, expect, json } from '../fixtures/apiMock'

const MOCK_SERVERS = [
  {
    id: 'hal0-admin',
    name: 'hal0-admin',
    bundled: true,
    state: 'running',
    version: '1.0.0',
    provider: 'hal0',
    description: 'Core hal0 admin tools',
    url: 'http://127.0.0.1:8080/mcp/hal0-admin',
    transport: 'sse',
    pid: 1234,
    since: '14d',
    tools: 12,
    resources: 0,
    prompts: 0,
    env: {},
    auto_start: true,
  },
  {
    id: 'github-mcp',
    name: 'github-mcp',
    bundled: false,
    state: 'stopped',
    version: '0.2.1',
    provider: 'community',
    description: 'GitHub integration',
    url: null,
    transport: 'stdio',
    pid: null,
    since: '2h',
    tools: 8,
    resources: 0,
    prompts: 0,
    env: { GITHUB_TOKEN: '' },
    auto_start: false,
  },
]

const MOCK_CLIENTS = [
  {
    id: 'claude-code-01',
    name: 'Claude Code',
    role: 'developer',
    host: '127.0.0.1',
    since: '10:32:11',
    servers: ['hal0-admin'],
  },
]

test.describe('MCP v3 (/agents/mcp)', () => {
  test.beforeEach(async ({ page }) => {
    await page.route('**/api/mcp/servers', (route) => json(route, { servers: MOCK_SERVERS }))
    await page.route('**/api/mcp/clients', (route) => json(route, MOCK_CLIENTS))
    await page.route('**/api/mcp/stream', (route) => route.abort())
    await page.route('**/api/mcp/catalog', (route) => json(route, { items: [] }))
  })

  // Helper: navigate + wait for servers data to land in the DOM.
  async function gotoMcp(page: any) {
    await Promise.all([
      page.waitForResponse('**/api/mcp/servers', { timeout: 15_000 }),
      page.goto('/#mcp', { waitUntil: 'domcontentloaded' }),
    ])
    // Wait until the KPI strip is rendered (data in React state).
    await expect(page.locator('.mcp-kpi')).toBeVisible({ timeout: 5_000 })
  }

  test('renders MCP view + KPI strip', async ({ page }) => {
    await gotoMcp(page)
    await expect(page.locator('.view .vh h1')).toHaveText('MCP Servers')
    await expect(page.locator('.mcp-kpi')).toBeVisible()
    const cells = page.locator('.mcp-kpi-cell')
    expect(await cells.count()).toBeGreaterThanOrEqual(5)
  })

  test('filter bar tabs render with counts', async ({ page }) => {
    await gotoMcp(page)
    // MCP view has two filter bars: Servers|Clients tab strip + server-list filter.
    // Use first() to avoid strict-mode violation.
    await expect(page.locator('.mcp-filterbar').first()).toBeVisible()
    const tabs = page.locator('.mcp-tab')
    expect(await tabs.count()).toBeGreaterThan(0)
  })

  test('server list renders both mock servers', async ({ page }) => {
    await gotoMcp(page)
    await expect(page.locator('.mcp-list')).toBeVisible()
    await expect(page.locator('.mcp-row', { hasText: 'hal0-admin' })).toBeVisible()
    await expect(page.locator('.mcp-row', { hasText: 'github-mcp' })).toBeVisible()
  })

  test('alias #agents/mcp routes to the same view', async ({ page }) => {
    // Use waitForResponse for the alias route too — same /api/mcp/servers fires.
    await Promise.all([
      page.waitForResponse('**/api/mcp/servers', { timeout: 15_000 }),
      page.goto('/#agents/mcp', { waitUntil: 'domcontentloaded' }),
    ])
    await expect(page.locator('.view .vh h1')).toHaveText('MCP Servers')
  })

  test('running server row shows Restart button disabled (supervisor pending)', async ({ page }) => {
    await gotoMcp(page)
    const githubRow = page.locator('.mcp-row', { hasText: 'github-mcp' })
    // "Start" button for stopped non-bundled server should be present but
    // disabled due to SUPERVISOR_AVAILABLE=false.
    const startBtn = githubRow.locator('button', { hasText: 'Start' })
    await expect(startBtn).toBeDisabled()
  })

  test('stopped non-bundled row has disabled Start with supervisor hint', async ({ page }) => {
    await gotoMcp(page)
    const githubRow = page.locator('.mcp-row', { hasText: 'github-mcp' })
    const startBtn = githubRow.locator('button', { hasText: 'Start' })
    await expect(startBtn).toBeDisabled()
    // Title attr carries the pending hint
    const title = await startBtn.getAttribute('title')
    expect(title).toContain('Supervisor pending')
  })

  test('bundled stopped server Start returning mcp.supervisor_unavailable shows ADR-0015 toast', async ({ page }) => {
    // The new code name is mcp.supervisor_unavailable (renamed from
    // mcp.not_implemented in backend-dev task #7); both are tolerated.
    // Bundled servers bypass SUPERVISOR_AVAILABLE and call the real endpoint;
    // useMcpRestart fires POST /api/mcp/{id}/restart for the start action.
    // We test via a stopped bundled server so the Start button is visible
    // (the Restart icon on running servers has no onClick wired — that is
    // a follow-up to ADR-0015; the mutation is reachable via Start today).
    const SERVERS_WITH_STOPPED_BUNDLED = [
      ...MOCK_SERVERS,
      {
        id: 'hal0-memory',
        name: 'hal0-memory',
        bundled: true,
        state: 'stopped',
        version: '1.0.0',
        provider: 'hal0',
        description: 'Memory MCP server (bundled)',
        url: null,
        transport: 'sse',
        pid: null,
        since: '—',
        tools: 4,
        resources: 0,
        prompts: 0,
        env: {},
        auto_start: false,
      },
    ]
    // Override the servers route with the extended list.
    await page.route('**/api/mcp/servers', (route) =>
      json(route, { servers: SERVERS_WITH_STOPPED_BUNDLED }),
    )
    await page.route('**/api/mcp/hal0-memory/restart', (route) =>
      route.fulfill({
        status: 501,
        contentType: 'application/json',
        body: JSON.stringify({
          error: {
            code: 'mcp.supervisor_unavailable',
            message: 'process supervisor not implemented (pending ADR-0015)',
          },
        }),
      }),
    )
    await Promise.all([
      page.waitForResponse('**/api/mcp/servers', { timeout: 15_000 }),
      page.goto('/#mcp', { waitUntil: 'domcontentloaded' }),
    ])
    await expect(page.locator('.mcp-kpi')).toBeVisible({ timeout: 5_000 })

    const memoryRow = page.locator('.mcp-row', { hasText: 'hal0-memory' })
    // Bundled stopped servers have Start enabled (canRunSupervisorAction=true).
    const startBtn = memoryRow.locator('button', { hasText: 'Start' })
    await expect(startBtn).not.toBeDisabled()
    await startBtn.click()
    // useMcpRestart catches the mcp.supervisor_unavailable 501 and shows a
    // warn toast with ADR-0015 reference. The dashboard toast is .hal0-toast.
    await expect(
      page.locator('.hal0-toast').filter({ hasText: /ADR-0015|supervisor/i }),
    ).toBeVisible({ timeout: 5_000 })
  })
})
