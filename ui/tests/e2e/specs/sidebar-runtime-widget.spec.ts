/**
 * sidebar-runtime-widget — consolidated runtime rollup (2026-06-05).
 *
 * The three former stacked sidebar blocks (SidebarAgentBlock /
 * SidebarEndpointBlock / SidebarStatusBlock) are merged into ONE card so
 * hermes, hal0, runtime and openwebui read as a single runtime rollup:
 *
 *   - hermes    — bundled agent health (/api/agents). Row key deep-links to
 *                 the Hermes dashboard ONLY when /api/config/urls advertises
 *                 one (hermes_enabled); otherwise it's plain text (the dash
 *                 binds loopback-only, so there's no host:port fallback).
 *   - hal0      — the composite /v1 endpoint (synthetic /api/slots entry,
 *                 served from HAL0_DATA in forced-mock) + model count.
 *   - openwebui — external chat UI link derived from /api/config/urls.
 *
 * Health indicators live in the footer runtime chip: slot readiness from
 * useRuntimeRollup plus service dots from /api/services/health.
 *
 * Mock seams: /api/agents and /api/config/urls are NOT in the forced-mock
 * allowlist, so page.route drives them. /api/slots IS allowlisted, so the
 * hal0 + runtime rows render from HAL0_DATA (data.jsx).
 */
import { test, expect, json } from '../fixtures/apiMock'

const FIVE_S = 5_500

const HERMES_URL = 'https://hermes.example.com'
const OPENWEBUI_URL = 'http://hal0.local:3001'

const AGENTS_RUNNING = {
  agents: [{ name: 'hermes', installed_at: '2026-05-25T12:00:00Z', status: 'installed' }],
  count: 1,
}
const AGENTS_EMPTY = { agents: [], count: 0 }

// Both services advertised + reachable (LAN-direct / public-URL deploy).
const URLS_ALL = {
  api: 'http://hal0.local:8080',
  openwebui: OPENWEBUI_URL,
  openwebui_enabled: true,
  hermes: HERMES_URL,
  hermes_enabled: true,
}
// Neither service reachably linkable (stock install: OWUI down, no hermes URL).
const URLS_NONE = {
  api: 'http://hal0.local:8080',
  openwebui: '',
  openwebui_enabled: false,
  hermes: '',
  hermes_enabled: false,
}
const SERVICES_HEALTH_UP = {
  services: [
    { id: 'comfyui', name: 'ComfyUI', up: false, detail: 'unreachable', url: null, stat: null },
    { id: 'hermes', name: 'Hermes', up: true, detail: 'systemd unit active', url: null, stat: null },
    {
      id: 'openwebui',
      name: 'OpenWebUI',
      up: true,
      detail: 'reachable — /health ok',
      url: null,
      stat: null,
    },
    { id: 'n8n', name: 'n8n', up: false, detail: 'unmonitored', url: null, stat: null },
  ],
}
const SERVICES_HEALTH_DOWN = {
  services: [
    { id: 'comfyui', name: 'ComfyUI', up: false, detail: 'unreachable', url: null, stat: null },
    {
      id: 'hermes',
      name: 'Hermes',
      up: false,
      detail: 'systemd unit inactive or absent',
      url: null,
      stat: null,
    },
    {
      id: 'openwebui',
      name: 'OpenWebUI',
      up: false,
      detail: 'unreachable (ConnectError)',
      url: null,
      stat: null,
    },
    { id: 'n8n', name: 'n8n', up: false, detail: 'unmonitored', url: null, stat: null },
  ],
}

test.describe('Sidebar Runtime widget — populated', () => {
  test.beforeEach(async ({ page }) => {
    await page.route('**/api/agents', (route) => json(route, AGENTS_RUNNING))
    await page.route('**/api/config/urls', (route) => json(route, URLS_ALL))
    await page.route('**/api/services/health', (route) => json(route, SERVICES_HEALTH_UP))
  })

  test('renders one widget with hermes / hal0 / runtime / openwebui rows', async ({ page }) => {
    await page.goto('/')
    const widget = page.locator('[data-testid="sidebar-runtime-widget"]')
    await expect(widget).toBeVisible({ timeout: FIVE_S })
    await expect(widget.locator('.sb-runtime-h')).toHaveText('Runtime')
    await expect(page.locator('[data-testid="runtime-row-hermes"]')).toBeVisible()
    await expect(page.locator('[data-testid="runtime-row-hal0"]')).toBeVisible()
    await expect(page.locator('[data-testid="runtime-row-openwebui"]')).toBeVisible()
    // The old standalone block is gone.
    await expect(page.locator('[data-testid="sidebar-agent-block"]')).toHaveCount(0)
  })

  test('hermes row deep-links to the backend-advertised dashboard', async ({
    page,
  }) => {
    await page.goto('/')
    const row = page.locator('[data-testid="runtime-row-hermes"]')
    await expect(row).toBeVisible({ timeout: FIVE_S })
    const link = row.locator('a.rt-link')
    await expect(link).toContainText('hermes')
    await expect(link).toHaveAttribute('href', HERMES_URL)
    await expect(link).toHaveAttribute('target', '_blank')
    await expect(row.locator('.v')).toContainText('agent')
    await expect(row.locator('.v .dot')).toHaveCount(0)
  })

  test('openwebui row deep-links to the backend-advertised URL', async ({
    page,
  }) => {
    await page.goto('/')
    const row = page.locator('[data-testid="runtime-row-openwebui"]')
    await expect(row).toBeVisible({ timeout: FIVE_S })
    const link = row.locator('a.rt-link')
    await expect(link).toContainText('openwebui')
    await expect(link).toHaveAttribute('href', OPENWEBUI_URL)
    await expect(link).toHaveAttribute('target', '_blank')
    await expect(row.locator('.v')).toContainText('chat')
    await expect(row.locator('.v .dot')).toHaveCount(0)
  })

  test('hal0 row shows the advertised model count', async ({ page }) => {
    await page.goto('/')
    const row = page.locator('[data-testid="runtime-row-hal0"]')
    await expect(row).toBeVisible({ timeout: FIVE_S })
    // model count reflects HAL0_DATA's synthetic endpoint (2 chat).
    await expect(row.locator('.v b')).toHaveText('2')
  })

  test('footer ribbon shows runtimes + services health groups', async ({ page }) => {
    await page.goto('/')
    // HAL0_DATA seeds 8 enabled slots (legacy is disabled); all are ready.
    const runtimes = page.locator('[data-testid="foot-health-runtimes"]')
    await expect(runtimes.locator('.lbl .v')).toContainText('8 / 8 ready', { timeout: FIVE_S })
    await expect(runtimes.locator('.pip.ok')).toHaveCount(8)
    // services group — one LED pip per backing service, all up here.
    const services = page.locator('[data-testid="foot-health-services"]')
    await expect(services.locator('.lbl .v')).toContainText('3 / 3 ready')
    await expect(services.locator('.pip.ok')).toHaveCount(3)
  })
})

test.describe('Sidebar Runtime widget — no advertised service links', () => {
  test.beforeEach(async ({ page }) => {
    await page.route('**/api/agents', (route) => json(route, AGENTS_RUNNING))
    await page.route('**/api/config/urls', (route) => json(route, URLS_NONE))
    await page.route('**/api/services/health', (route) => json(route, SERVICES_HEALTH_UP))
  })

  test('hermes row stays plain text when no dashboard URL is advertised', async ({ page }) => {
    await page.goto('/')
    const row = page.locator('[data-testid="runtime-row-hermes"]')
    await expect(row).toBeVisible({ timeout: FIVE_S })
    // No anchor — just the bare key — while health still renders.
    await expect(row.locator('a.rt-link')).toHaveCount(0)
    await expect(row.locator('.k')).toHaveText('hermes')
    await expect(row.locator('.v')).toContainText('agent')
  })

  test('openwebui health can be up even when no link is advertised', async ({ page }) => {
    await page.goto('/')
    const row = page.locator('[data-testid="runtime-row-openwebui"]')
    await expect(row.locator('.v')).toContainText('chat', { timeout: FIVE_S })
    await expect(row.locator('a.rt-link')).toHaveCount(0)
    await expect(row.locator('.k')).toHaveText('openwebui')
    // footer services pip still reads the openwebui health as up.
    const services = page.locator('[data-testid="foot-health-services"]')
    await expect(services.locator('.pip[aria-label="openwebui: up"]')).toHaveCount(1)
  })
})

test.describe('Sidebar Runtime widget — hermes tone mapping', () => {
  test('service health renders a down (red) hermes dot', async ({ page }) => {
    await page.route('**/api/agents', (route) =>
      json(route, {
        agents: [{ name: 'hermes', installed_at: '2026-05-25T12:00:00Z', status: 'broken' }],
        count: 1,
      }),
    )
    await page.route('**/api/config/urls', (route) => json(route, URLS_ALL))
    await page.route('**/api/services/health', (route) => json(route, SERVICES_HEALTH_DOWN))
    await page.goto('/')
    const pip = page.locator('[data-testid="foot-health-services"] .pip[aria-label^="hermes:"]')
    await expect(pip).toHaveClass(/\berr\b/, { timeout: FIVE_S })
  })

  test('no agent installed renders sidebar copy without a health dot', async ({ page }) => {
    await page.route('**/api/agents', (route) => json(route, AGENTS_EMPTY))
    await page.route('**/api/config/urls', (route) => json(route, URLS_ALL))
    await page.route('**/api/services/health', (route) => json(route, SERVICES_HEALTH_UP))
    await page.goto('/')
    const v = page.locator('[data-testid="runtime-row-hermes"] .v')
    await expect(v).toContainText('not installed', { timeout: FIVE_S })
    // The widget itself still renders (hermes never hides the whole card).
    await expect(page.locator('[data-testid="sidebar-runtime-widget"]')).toBeVisible()
  })
})
