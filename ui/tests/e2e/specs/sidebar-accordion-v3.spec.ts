/**
 * sidebar-accordion-v3 — sidebar nav accordion + bottom Services launch zone.
 *
 * Two behaviours pinned here:
 *
 *  1. Accordion — parent rows with sub-links (Slots ▸ Endpoints/Profiles,
 *     Agents ▸ Memory/MCP) start COLLAPSED. The sub-links don't render until
 *     the parent is opened (via its caret), and the section you navigate into
 *     auto-expands. This is the "endpoints/profiles don't show until Slots is
 *     clicked" contract.
 *
 *  2. Services zone — a separate group pinned to the sidebar bottom, fenced off
 *     from the app/config nav above. Holds Kanban (internal #board) plus the
 *     external OpenWebUI + Hermes shortcuts. The external links are resolved by
 *     GET /api/config/urls, whose backend derives the hostname from the request
 *     so they work on every install; each row is gated by its *_enabled flag.
 */
import { test, expect, json } from '../fixtures/apiMock'

const FIVE_S = 5_500

// Mock /api/config/urls with both external services enabled so all three
// Services rows render with install-derived hrefs.
async function mockConfigUrls(page: import('@playwright/test').Page) {
  await page.route('**/api/config/urls', (route) =>
    json(route, {
      api: 'http://hal0.example:8080',
      openwebui: 'http://hal0.example:3001',
      openwebui_enabled: true,
      hermes: 'http://hal0.example:9119',
      hermes_enabled: true,
      comfyui: 'http://hal0.example:8188',
    }),
  )
}

test.describe('sidebar accordion', () => {
  test('Slots/Agents sub-links are hidden until the parent is expanded', async ({ page }) => {
    await page.goto('/#dashboard')
    const sb = page.locator('.sidebar')
    await expect(sb.locator('[data-testid="nav-slots"]')).toBeVisible({ timeout: FIVE_S })

    // Collapsed by default on an unrelated route — sub-links absent.
    await expect(sb.locator('[data-testid="nav-slots-endpoints"]')).toHaveCount(0)
    await expect(sb.locator('[data-testid="nav-slots-profiles"]')).toHaveCount(0)
    await expect(sb.locator('[data-testid="nav-mcp"]')).toHaveCount(0)

    // Expand Slots via its caret → its sub-links appear; Agents stays closed.
    await sb.locator('[data-testid="nav-slots-toggle"]').click()
    await expect(sb.locator('[data-testid="nav-slots-endpoints"]')).toBeVisible()
    await expect(sb.locator('[data-testid="nav-slots-profiles"]')).toBeVisible()
    await expect(sb.locator('[data-testid="nav-mcp"]')).toHaveCount(0)

    // Collapse again — sub-links go away.
    await sb.locator('[data-testid="nav-slots-toggle"]').click()
    await expect(sb.locator('[data-testid="nav-slots-endpoints"]')).toHaveCount(0)
  })

  test('navigating into a section auto-expands it', async ({ page }) => {
    // Deep-link straight to a Slots sub-route: the parent must auto-open so the
    // active sub-link is visible without a manual toggle.
    await page.goto('/#slots/endpoints')
    const sb = page.locator('.sidebar')
    await expect(sb.locator('[data-testid="nav-slots-endpoints"]')).toBeVisible({ timeout: FIVE_S })
    await expect(sb.locator('[data-testid="nav-slots-profiles"]')).toBeVisible()
  })
})

test.describe('sidebar Services zone', () => {
  test('renders install-derived OpenWebUI/Hermes links (no Kanban — moved to topbar)', async ({ page }) => {
    await mockConfigUrls(page)
    await page.goto('/#dashboard')
    const svc = page.locator('.sb-services')
    await expect(svc).toBeVisible({ timeout: FIVE_S })

    // Kanban moved to the topbar launcher; Services now holds only the external
    // OpenWebUI/Hermes <a>s, hrefs straight from the backend-resolved config.
    await expect(svc.locator('[data-testid="svc-kanban"]')).toHaveCount(0)
    await expect(svc.locator('[data-testid="svc-openwebui"]')).toHaveAttribute(
      'href',
      'http://hal0.example:3001',
    )
    await expect(svc.locator('[data-testid="svc-hermes"]')).toHaveAttribute(
      'href',
      'http://hal0.example:9119',
    )
    // External links open in a new tab safely.
    await expect(svc.locator('[data-testid="svc-openwebui"]')).toHaveAttribute('target', '_blank')
    await expect(svc.locator('[data-testid="svc-openwebui"]')).toHaveAttribute('rel', /noopener/)
  })

  test('topbar Kanban launcher routes to the Operator Board', async ({ page }) => {
    await page.goto('/#dashboard')
    await page.locator('[data-testid="tb-launch-board"]').click()
    await expect(page).toHaveURL(/#board/)
    await expect(page.locator('[data-testid="board-view"]')).toBeVisible({ timeout: FIVE_S })
  })

  test('disabled services are omitted (no host:port fallback leaks)', async ({ page }) => {
    // hermes disabled (loopback-only, no public URL) → its row must not render;
    // OpenWebUI enabled → its row stays.
    await page.route('**/api/config/urls', (route) =>
      json(route, {
        api: 'http://hal0.example:8080',
        openwebui: 'http://hal0.example:3001',
        openwebui_enabled: true,
        hermes: '',
        hermes_enabled: false,
        comfyui: 'http://hal0.example:8188',
      }),
    )
    await page.goto('/#dashboard')
    const svc = page.locator('.sb-services')
    await expect(svc).toBeVisible({ timeout: FIVE_S })
    await expect(svc.locator('[data-testid="svc-openwebui"]')).toBeVisible()
    await expect(svc.locator('[data-testid="svc-hermes"]')).toHaveCount(0)
  })
})
