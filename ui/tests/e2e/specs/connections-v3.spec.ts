/**
 * connections-v3 — the dissolved Connections page split into two new homes.
 *
 * v0.5 nav: the standalone #connections page (its `<h1>Connections</h1>` +
 * "Network" eyebrow) is GONE. Its two engine-block panes were repointed:
 *   1. Local endpoints — now the Slots ▸ Endpoints tab (#slots/endpoints),
 *      rendered via window.LocalEndpointsPanel. The OpenAI-compatible API,
 *      one row per slot, each row expands to a cURL builder + a real
 *      health-check "Test" ping.
 *   2. MCP servers — now the Agent ▸ MCP tab (#mcp / #agent/mcp), rendered
 *      via window.McpServersPanel. The bundled FastMCP servers, each
 *      expandable with add-to-client config + a tool manifest
 *      (name · args · gated / destructive / read-only badges).
 *
 * The inner markup (.cpane / .eplist / .eprow / EndpointRow; .mcplist /
 * .mcprow / McpServerRow) is IDENTICAL to the old page — only the page
 * wrapper/heading changed. Data is still wired to useSlots() (/api/slots) +
 * useMcpServers() (/api/mcp/servers) + useConfigUrls() (/api/config/urls).
 * The Test button fires a real request through the gateway; we stub
 * /v1/chat/completions with an SSE body.
 */
import { test, expect, json } from '../fixtures/apiMock'

// ── Mock MCP servers (matches GET /api/mcp/servers shape) ─────────────
const MOCK_MCP_SERVERS = [
  {
    id: 'hal0-admin',
    name: 'hal0-admin',
    bundled: true,
    state: 'running',
    transport: 'streamable-http',
    connect_url: 'http://localhost/mcp/admin',
    pid: null,
    version: '0.4.0',
    tools: 2,
    tool_details: [
      {
        name: 'slot_list',
        description: 'List every slot known to hal0 (local + remote).',
        args: '—',
        read_only: true,
        destructive: false,
        idempotent: true,
        open_world: false,
        gated: false,
      },
      {
        name: 'slot_delete',
        description: 'Delete a slot (gated).',
        args: 'args?: object',
        read_only: false,
        destructive: true,
        idempotent: true,
        open_world: false,
        gated: true,
      },
    ],
    resources: 0,
    prompts: 0,
    activity: { rpm: 0 },
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
    connect_url: 'http://localhost/mcp/memory',
    pid: null,
    version: '0.4.0',
    tools: 1,
    tool_details: [
      {
        name: 'memory_recall',
        description: 'Retrieve the top-k memories for a query.',
        args: 'args?: object',
        read_only: true,
        destructive: false,
        idempotent: true,
        open_world: false,
        gated: false,
      },
    ],
    resources: 0,
    prompts: 0,
    activity: { rpm: 0 },
    connected: [],
    description: 'hal0 bundled memory MCP server.',
    provider: 'hal0',
  },
]

const CHAT_SSE =
  'data: {"choices":[{"delta":{"content":"pong"}}]}\n\n' +
  'data: {"choices":[{"delta":{}}],"usage":{"completion_tokens":1}}\n\n' +
  'data: [DONE]\n\n'

// ── Local endpoints — now the Slots ▸ Endpoints tab (#slots/endpoints) ──
test.describe('Local endpoints (Slots ▸ Endpoints, #slots/endpoints)', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/#slots/endpoints', { waitUntil: 'domcontentloaded' })
    await expect(page.locator('.eprow').first()).toBeVisible({ timeout: 5_000 })
  })

  test('renders the Local endpoints engine pane on the Endpoints tab', async ({ page }) => {
    // v0.5: the standalone "Connections" heading is gone — the Slots page
    // header sits above, and the Endpoints tab hosts only the endpoints pane.
    await expect(page.locator('.view .vh h1')).toHaveText('Slots')
    await expect(page.locator('.cpane.live')).toBeVisible()
    await expect(page.locator('.cpane-title', { hasText: 'Local endpoints' })).toBeVisible()
  })

  test('local endpoints list one row per slot', async ({ page }) => {
    // The default mock state carries 9 slots (one row per useSlots() entry,
    // including the disabled `legacy` slot; the synthetic hal0 endpoint is
    // filtered out).
    await expect(page.locator('.eplist .eprow')).toHaveCount(9)
    const primary = page.locator('.eprow').filter({ hasText: 'primary' }).first()
    await expect(primary.locator('.ep-dot.serving')).toBeVisible()
    await expect(primary.locator('.star')).toBeVisible() // default slot
  })

  test('expanding an endpoint row reveals the gateway-targeted cURL', async ({ page }) => {
    const primary = page.locator('.eprow').filter({ hasText: 'primary' }).first()
    await primary.locator('.eprow-main').click()
    const curl = primary.locator('.curl-code pre')
    await expect(curl).toBeVisible()
    // Targets /v1 on the gateway, model id selects the slot — NOT a slot port.
    await expect(curl).toContainText('/v1/chat/completions')
    await expect(curl).toContainText('qwen3.6-27b-mtp')
    // No auth header — open on the LAN.
    await expect(curl).not.toContainText('Authorization')
  })

  test('Test fires a real ping and renders the result metrics', async ({ page }) => {
    await page.route('**/v1/chat/completions', (route) =>
      route.fulfill({ status: 200, contentType: 'text/event-stream', body: CHAT_SSE }),
    )
    const primary = page.locator('.eprow').filter({ hasText: 'primary' }).first()
    await primary.locator('.eprow-main').click()
    await primary.locator('button', { hasText: 'Test endpoint' }).click()
    const result = primary.locator('.ep-result')
    await expect(result).toBeVisible({ timeout: 5_000 })
    await expect(result.locator('.status')).toContainText('200 OK')
    await expect(result.locator('.ep-metrics .m', { hasText: 'tok/s' })).toBeVisible()
  })

})

// ── MCP servers — now the Agent ▸ MCP tab (#mcp / #agent/mcp) ──
test.describe('MCP servers (Agent ▸ MCP, #mcp)', () => {
  test.beforeEach(async ({ page }) => {
    await page.route('**/api/mcp/servers', (route) =>
      json(route, { servers: MOCK_MCP_SERVERS, count: MOCK_MCP_SERVERS.length }),
    )
    await Promise.all([
      page.waitForResponse('**/api/mcp/servers', { timeout: 15_000 }),
      page.goto('/#mcp', { waitUntil: 'domcontentloaded' }),
    ])
    await expect(page.locator('.mcprow').first()).toBeVisible({ timeout: 5_000 })
  })

  test('renders the MCP servers engine pane inside the Agent shell', async ({ page }) => {
    // v0.5: MCP lives under the Agents tabbed page (header "Agents", eyebrow
    // "Tools"); the "Connections" heading is gone.
    await expect(page.locator('.view .vh h1')).toHaveText('Agents')
    await expect(page.locator('.cpane-title', { hasText: 'MCP servers' })).toBeVisible()
  })

  test('MCP servers fold in as expandable rows with a tool manifest', async ({ page }) => {
    const mcpPane = page.locator('.cpane').filter({ hasText: 'MCP servers' })
    await expect(mcpPane.locator('.mcprow')).toHaveCount(2)
    const admin = page.locator('.mcprow').filter({ hasText: 'hal0-admin' })
    await expect(admin.locator('.path')).toContainText('/mcp/admin')
    await admin.locator('.mcprow-main').click()
    // Tool grid renders name + the gated / destructive blast-radius badges.
    await expect(admin.locator('.mcp-tool').filter({ hasText: 'slot_list' })).toBeVisible()
    const del = admin.locator('.mcp-tool').filter({ hasText: 'slot_delete' })
    await expect(del.locator('.mt-badge.gated')).toBeVisible()
    await expect(del.locator('.mt-badge.destructive')).toBeVisible()
  })

  test('MCP server exposes add-to-client config quick links', async ({ page }) => {
    const admin = page.locator('.mcprow').filter({ hasText: 'hal0-admin' })
    await admin.locator('.mcprow-main').click()
    await expect(admin.locator('.clientchip', { hasText: 'Claude Desktop' })).toBeVisible()
    await expect(admin.locator('.clientchip', { hasText: 'Codex' })).toBeVisible()
    await expect(admin.locator('.clientchip', { hasText: 'Cursor' })).toBeVisible()
  })
})
