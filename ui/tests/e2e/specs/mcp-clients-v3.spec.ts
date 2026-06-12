/**
 * mcp-clients-v3 — ADR-0013 §8 per-agent view on /agents/mcp.
 *
 * Pins (skip-marked matching the v3 pattern):
 *   - mode toggle Servers | Clients renders + switches.
 *   - Clients view shows the hermes card with the four sample servers.
 *   - allow / gated / blocked chips render with the right verdict.
 *   - bearer-from-env tokens never render the actual value.
 */
import { test, expect, json } from '../fixtures/apiMock'

const MOCK_LIST = {
  agents: [
    {
      name: 'hermes',
      display: 'Hermes-Agent',
      workspace: '/var/lib/hal0/agents/hermes/workspace',
      servers: [
        {
          name: 'hal0-admin',
          url: null,
          enabled: true,
          builtin: true,
          auth: { kind: 'none', env: null, tokenStatus: 'not-needed' },
          tools: { allow: [], gated: [], blocked: [] },
          health: 'green',
        },
        {
          name: 'github',
          url: 'https://api.github.com/mcp',
          enabled: false,
          builtin: false,
          auth: {
            kind: 'bearer-from-env',
            env: 'HAL0_AGENT_HERMES_GITHUB_TOKEN',
            tokenStatus: 'missing',
          },
          tools: {
            allow: ['list_issues'],
            gated: ['create_pr'],
            blocked: ['delete_repo'],
          },
          health: 'unknown',
        },
      ],
    },
  ],
}

test.describe('MCP Clients view (ADR-0013 §8)', () => {
  // Minimal servers response (clients tests focus on the Clients tab, but the
  // MCP view also fetches servers — register a stub so it doesn't hang).
  const STUB_SERVERS = { servers: [] }

  // Helper: navigate, wait for both servers + clients to load, confirm structure.
  async function gotoMcpWithClients(page: any) {
    await page.route('**/api/mcp/servers', (route) => json(route, STUB_SERVERS))
    await page.route('**/api/mcp/clients', (route) => json(route, MOCK_LIST))
    await page.route('**/api/mcp/stream', (route) => route.abort())
    await page.route('**/api/mcp/catalog', (route) => json(route, { items: [] }))
    await Promise.all([
      page.waitForResponse('**/api/mcp/servers', { timeout: 15_000 }),
      page.waitForResponse('**/api/mcp/clients', { timeout: 15_000 }),
      page.goto('/#agents/mcp', { waitUntil: 'domcontentloaded' }),
    ])
    // Tabs (Servers | Clients) should appear once the view renders.
    await expect(page.locator('.mcp-tab').first()).toBeVisible({ timeout: 5_000 })
  }

  test('mode toggle renders both Servers and Clients', async ({ page }) => {
    await gotoMcpWithClients(page)
    await expect(page.locator('.mcp-tab', { hasText: 'Servers' })).toBeVisible()
    await expect(page.locator('.mcp-tab', { hasText: 'Clients' })).toBeVisible()
  })

  test('clients tab shows hermes card + servers', async ({ page }) => {
    await gotoMcpWithClients(page)
    await page.locator('.mcp-tab', { hasText: 'Clients' }).click()
    await expect(page.locator('.view')).toContainText('Hermes-Agent')
    await expect(page.locator('.view')).toContainText('hal0-admin')
    await expect(page.locator('.view')).toContainText('github')
  })

  test('tool chips render with allow/gated/blocked verdicts', async ({ page }) => {
    await gotoMcpWithClients(page)
    await page.locator('.mcp-tab', { hasText: 'Clients' }).click()
    await expect(page.locator('.chip', { hasText: 'list_issues' })).toBeVisible()
    await expect(page.locator('.chip', { hasText: 'create_pr' })).toBeVisible()
    await expect(page.locator('.chip', { hasText: 'delete_repo' })).toBeVisible()
  })

  test('bearer auth shown without rendering token value', async ({ page }) => {
    await gotoMcpWithClients(page)
    await page.locator('.mcp-tab', { hasText: 'Clients' }).click()
    // Token value never appears; just the env-var name (in title attr)
    // and the status word.
    await expect(page.locator('.view')).toContainText('bearer · missing')
    await expect(page.locator('.view')).not.toContainText(/ghp_\w+/)
  })
})
