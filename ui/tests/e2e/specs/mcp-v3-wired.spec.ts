/**
 * mcp-v3-wired — issue #206 — the MCP page renders against the live
 * /api/mcp/* routes (mocked here via page.route) and surfaces the
 * ADR-0013 toast when the 501-stub install/uninstall routes reject.
 *
 * The existing `mcp-v3.spec.ts` covers the HAL0_DATA-mock fallback;
 * this spec pins the live-backend wiring so a regression that drops
 * the hook fails loudly.
 */
import { test, expect, json } from '../fixtures/apiMock'

const MOCK_SERVERS = {
  servers: [
    {
      id: 'hal0-admin',
      name: 'hal0-admin',
      bundled: true,
      state: 'running',
      transport: 'streamable-http',
      connect_url: 'http://localhost:8080/mcp/admin',
      pid: 31204,
      version: '0.3.0',
      tools: 11,
      resources: 4,
      prompts: 2,
      activity: { rpm: 7 },
      connected: ['claude-code'],
      description: 'hal0 bundled admin MCP server.',
      provider: 'hal0',
    },
    {
      id: 'hal0-memory',
      name: 'hal0-memory',
      bundled: true,
      state: 'running',
      transport: 'streamable-http',
      connect_url: 'http://localhost:8080/mcp/memory',
      pid: 31218,
      version: '0.3.0',
      tools: 4,
      resources: 0,
      prompts: 1,
      activity: { rpm: 3 },
      connected: ['claude-code', 'cursor'],
      description: 'hal0 bundled memory MCP server.',
      provider: 'hal0',
    },
  ],
  count: 2,
}

const MOCK_CLIENTS = {
  clients: [
    {
      id: 'claude-code',
      name: 'Claude Code',
      role: 'CLI',
      host: 'ramekin.lan',
      since: Date.now() / 1000 - 300,
      connected_to: ['hal0-admin', 'hal0-memory'],
    },
    {
      id: 'cursor',
      name: 'Cursor',
      role: 'IDE',
      host: 'tritium.lan',
      since: Date.now() / 1000 - 600,
      connected_to: ['hal0-memory'],
    },
  ],
  count: 2,
}

const MOCK_CATALOG = {
  items: [
    {
      name: 'filesystem',
      author: 'modelcontextprotocol',
      verified: true,
      description: 'Read, write, and search files inside an allowlisted root.',
      tools: 5,
      stars: 12000,
      category: 'files',
    },
    {
      name: 'github',
      author: 'modelcontextprotocol',
      verified: true,
      description: 'GitHub repo ops.',
      tools: 27,
      stars: 8800,
      category: 'issues',
    },
  ],
  categories: ['Files', 'Issues'],
}

test.describe('MCP v3 wired (#206)', () => {
  test.beforeEach(async ({ page }) => {
    await page.route('**/api/mcp/servers', (route) => json(route, MOCK_SERVERS))
    await page.route('**/api/mcp/clients', (route) => json(route, MOCK_CLIENTS))
    await page.route('**/api/mcp/catalog', (route) => json(route, MOCK_CATALOG))
    // SSE stream — fulfil with empty body so the EventSource doesn't
    // hang on a real connection. The LiveTimeline still ticks via the
    // periodic redraw loop in useMcpCallStream.
    await page.route('**/api/mcp/stream', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: '',
      }),
    )
  })

  test('renders MCP view with live server names from /api/mcp/servers', async ({ page }) => {
    await page.goto('/#mcp')
    await expect(page.locator('.view .vh h1')).toHaveText('MCP Servers')
    // Both bundled servers from the mock appear by name. The KPI strip
    // also reads the live count, so checking the row text proves the
    // hook was the source rather than the HAL0_DATA fallback.
    await expect(page.locator('.mcp-row-name', { hasText: 'hal0-admin' })).toBeVisible()
    await expect(page.locator('.mcp-row-name', { hasText: 'hal0-memory' })).toBeVisible()
  })

  test('clients ribbon renders live clients from /api/mcp/clients', async ({ page }) => {
    await page.goto('/#mcp')
    // Wait for query to settle.
    await expect(page.locator('.mcp-client-name', { hasText: 'Claude Code' })).toBeVisible()
    await expect(page.locator('.mcp-client-name', { hasText: 'Cursor' })).toBeVisible()
  })

  test('KPI strip reflects live server count', async ({ page }) => {
    await page.goto('/#mcp')
    // First KPI cell is "running N/total"; with two running servers
    // out of two we should see "2" + "/2".
    const firstCell = page.locator('.mcp-kpi-cell').first()
    await expect(firstCell).toContainText('2')
  })

  test('install drawer catalog reads from /api/mcp/catalog', async ({ page }) => {
    await page.goto('/#mcp')
    await page.locator('button', { hasText: 'Install' }).first().click()
    // Drawer renders both catalog rows.
    await expect(page.locator('.mcp-install-name', { hasText: 'filesystem' })).toBeVisible()
    await expect(page.locator('.mcp-install-name', { hasText: 'github' })).toBeVisible()
  })

  test('install button surfaces 501 toast (ADR-0013 stub)', async ({ page }) => {
    // Capture toast calls. The dashboard installs its own
    // window.__hal0Toast inside a useEffect at mount, so an
    // addInitScript override gets clobbered. Instead define a property
    // setter that captures every assignment + wraps the real handler.
    await page.addInitScript(() => {
      ;(window as any).__hal0ToastCalls = []
      let _real: any = null
      Object.defineProperty(window, '__hal0Toast', {
        configurable: true,
        get() {
          return (msg: string, tone: string) => {
            ;(window as any).__hal0ToastCalls.push({ msg, tone })
            if (_real) _real(msg, tone)
          }
        },
        set(v) {
          _real = v
        },
      })
    })

    // Override the install POST to return the actual 501 envelope. The
    // override registers AFTER the beforeEach so Playwright (reverse
    // registration order) matches this one first.
    await page.route('**/api/mcp/install', (route) =>
      route.fulfill({
        status: 501,
        contentType: 'application/json',
        body: JSON.stringify({
          error: {
            code: 'mcp.not_implemented',
            message: 'MCP install pending ADR-0013 follow-up (#206)',
            details: {},
          },
        }),
      }),
    )

    const installRequest = page.waitForRequest('**/api/mcp/install', { timeout: 8_000 })

    await page.goto('/#mcp')
    // Wait for the page's "Install" header button (opens the drawer).
    await page.locator('.vh button', { hasText: 'Install' }).first().click()
    // Wait for catalog to populate then click the per-item Install.
    await expect(page.locator('.mcp-install-name', { hasText: 'filesystem' })).toBeVisible()
    await page
      .locator('.mcp-install-item', { has: page.locator('.mcp-install-name', { hasText: 'filesystem' }) })
      .locator('button', { hasText: 'Install' })
      .click()

    // Confirm the install POST actually fires (proves the wiring).
    await installRequest

    // Mutation hook fires; the toast lands asynchronously after the
    // 501 response, so poll the captured array.
    await expect
      .poll(
        async () => {
          return await page.evaluate(() => (window as any).__hal0ToastCalls || [])
        },
        { timeout: 10_000 },
      )
      .toEqual(
        expect.arrayContaining([
          expect.objectContaining({ msg: expect.stringContaining('ADR-0013') }),
        ]),
      )
  })
})
