/**
 * sidebar-runtime-widget — consolidated runtime rollup (2026-06-05).
 *
 * The three former stacked sidebar blocks (SidebarAgentBlock /
 * SidebarEndpointBlock / SidebarStatusBlock) are merged into ONE card so
 * hermes, hal0, lemond and openwebui read as a single runtime rollup:
 *
 *   - hermes    — bundled agent health (/api/agents). Row key deep-links
 *                 to the standalone Hermes dashboard (new tab).
 *   - hal0      — the composite /v1 endpoint (synthetic /api/slots entry,
 *                 served from HAL0_DATA in forced-mock) + model count.
 *   - lemond    — inference runtime (/v1/health) status + version, with an
 *                 npu "coresident" row when reported.
 *   - openwebui — external chat UI unit (/api/install/state.openwebui_running).
 *                 Row key deep-links to the OpenWebUI app (new tab).
 *
 * Mock seams: /api/agents and /api/install/state are NOT in the forced-mock
 * allowlist, so page.route drives them. /api/slots + /v1/health ARE
 * allowlisted, so those rows render from HAL0_DATA (data.jsx).
 */
import { test, expect, json } from '../fixtures/apiMock'

const FIVE_S = 5_500

const HERMES_URL = 'https://hermes.thinmint.dev'
const OPENWEBUI_URL = 'https://hal0-chat.thinmint.dev'

const AGENTS_RUNNING = {
  agents: [{ name: 'hermes', installed_at: '2026-05-25T12:00:00Z', status: 'installed' }],
  count: 1,
}
const AGENTS_EMPTY = { agents: [], count: 0 }
const INSTALL_OWUI_UP = { first_run: false, openwebui_running: true, bundle: null }
const INSTALL_OWUI_DOWN = { first_run: false, openwebui_running: false, bundle: null }

test.describe('Sidebar Runtime widget — populated', () => {
  test.beforeEach(async ({ page }) => {
    await page.route('**/api/agents', (route) => json(route, AGENTS_RUNNING))
    await page.route('**/api/install/state', (route) => json(route, INSTALL_OWUI_UP))
  })

  test('renders one widget with hermes / hal0 / lemond / openwebui rows', async ({ page }) => {
    await page.goto('/')
    const widget = page.locator('[data-testid="sidebar-runtime-widget"]')
    await expect(widget).toBeVisible({ timeout: FIVE_S })
    await expect(widget.locator('.sb-runtime-h')).toHaveText('Runtime')
    await expect(page.locator('[data-testid="runtime-row-hermes"]')).toBeVisible()
    await expect(page.locator('[data-testid="runtime-row-hal0"]')).toBeVisible()
    await expect(page.locator('[data-testid="runtime-row-lemond"]')).toBeVisible()
    await expect(page.locator('[data-testid="runtime-row-openwebui"]')).toBeVisible()
    // The old standalone block is gone.
    await expect(page.locator('[data-testid="sidebar-agent-block"]')).toHaveCount(0)
  })

  test('hermes row deep-links to the Hermes dashboard and shows running', async ({ page }) => {
    await page.goto('/')
    const row = page.locator('[data-testid="runtime-row-hermes"]')
    await expect(row).toBeVisible({ timeout: FIVE_S })
    const link = row.locator('a.rt-link')
    await expect(link).toContainText('hermes')
    await expect(link).toHaveAttribute('href', HERMES_URL)
    await expect(link).toHaveAttribute('target', '_blank')
    await expect(row.locator('.v')).toContainText('running')
    await expect(row.locator('.v')).toHaveClass(/up/)
    await expect(row.locator('.v .dot')).toBeVisible()
  })

  test('openwebui row deep-links to the chat UI and shows running', async ({ page }) => {
    await page.goto('/')
    const row = page.locator('[data-testid="runtime-row-openwebui"]')
    await expect(row).toBeVisible({ timeout: FIVE_S })
    const link = row.locator('a.rt-link')
    await expect(link).toContainText('openwebui')
    await expect(link).toHaveAttribute('href', OPENWEBUI_URL)
    await expect(link).toHaveAttribute('target', '_blank')
    await expect(row.locator('.v')).toContainText('running')
    await expect(row.locator('.v')).toHaveClass(/up/)
  })

  test('hal0 row shows serving + the advertised model count', async ({ page }) => {
    await page.goto('/')
    const row = page.locator('[data-testid="runtime-row-hal0"]')
    await expect(row).toBeVisible({ timeout: FIVE_S })
    await expect(row.locator('.v')).toContainText('serving')
    await expect(row.locator('.v')).toHaveClass(/up/)
    // model count sub-row reflects HAL0_DATA's synthetic endpoint (2 chat).
    const sub = page.locator('[data-testid="sidebar-runtime-widget"] .row.rt-sub')
    await expect(sub.locator('.k')).toHaveText('models')
    await expect(sub.locator('.v b')).toHaveText('2')
  })

  test('lemond row shows status + version inline', async ({ page }) => {
    await page.goto('/')
    const row = page.locator('[data-testid="runtime-row-lemond"]')
    await expect(row.locator('.v')).toContainText('up', { timeout: FIVE_S })
    await expect(row.locator('.v')).toContainText(/v\d/)
  })
})

test.describe('Sidebar Runtime widget — hermes tone mapping', () => {
  test('broken agent renders a down (red) dot', async ({ page }) => {
    await page.route('**/api/agents', (route) =>
      json(route, {
        agents: [{ name: 'hermes', installed_at: '2026-05-25T12:00:00Z', status: 'broken' }],
        count: 1,
      }),
    )
    await page.route('**/api/install/state', (route) => json(route, INSTALL_OWUI_UP))
    await page.goto('/')
    const v = page.locator('[data-testid="runtime-row-hermes"] .v')
    await expect(v).toHaveClass(/down/, { timeout: FIVE_S })
    await expect(v).toContainText('broken')
  })

  test('no agent installed renders "off" (amber)', async ({ page }) => {
    await page.route('**/api/agents', (route) => json(route, AGENTS_EMPTY))
    await page.route('**/api/install/state', (route) => json(route, INSTALL_OWUI_UP))
    await page.goto('/')
    const v = page.locator('[data-testid="runtime-row-hermes"] .v')
    await expect(v).toHaveClass(/warn/, { timeout: FIVE_S })
    await expect(v).toContainText('off')
    // The widget itself still renders (hermes never hides the whole card).
    await expect(page.locator('[data-testid="sidebar-runtime-widget"]')).toBeVisible()
  })
})

test.describe('Sidebar Runtime widget — openwebui state', () => {
  test('openwebui row shows "off" (red) when the unit is not running', async ({ page }) => {
    await page.route('**/api/agents', (route) => json(route, AGENTS_RUNNING))
    await page.route('**/api/install/state', (route) => json(route, INSTALL_OWUI_DOWN))
    await page.goto('/')
    const v = page.locator('[data-testid="runtime-row-openwebui"] .v')
    await expect(v).toContainText('off', { timeout: FIVE_S })
    await expect(v).toHaveClass(/down/)
  })
})
