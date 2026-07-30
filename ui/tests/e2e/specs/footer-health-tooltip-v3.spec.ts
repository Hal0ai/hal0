/**
 * footer-health-tooltip-v3 — #1461.
 *
 * The footer's "degraded" tooltip is built by `failingChecks()`
 * (src/api/hooks/useRuntime.ts) from GET /api/health/system. The backend
 * (src/hal0/api/routes/health.py) emits a boolean `ok` per check and NEVER a
 * per-check `status`, so a `c.status !== 'ok'` filter classifies every check
 * as failing — the operator sees all five checks listed on a box where only
 * one is actually broken, and the real reason (slot_manager's `errored` list)
 * never makes it into the tooltip.
 *
 * This spec pins the honest contract:
 *   1. only checks with `ok:false` appear in the tooltip,
 *   2. slot_manager's `errored` slot names are surfaced (the live failure
 *      path carries its reason there, not in `detail`),
 *   3. `detail` is surfaced on the paths that do emit it (mcp_mount).
 *
 * /api/health/system is answered by the fixture's `/api/` catch-all with `{}`;
 * a page.route registered afterwards wins.
 */
import { test, expect, type Page } from '../fixtures/apiMock'

/** The real /api/health/system payload shape (health.py health_system). */
function healthSystem(overrides: Record<string, unknown> = {}) {
  return {
    status: 'degraded',
    checks: {
      disk_state: { ok: true, free_mb: 402_000, floor_mb: 2048, path: '/var/lib/hal0' },
      disk_config: { ok: true, free_mb: 402_000, floor_mb: 2048, path: '/etc/hal0' },
      slot_manager: { ok: false, slots: 3, errored: ['flm'] },
      event_bus: { ok: true },
      mcp_mount: { ok: true, servers: ['admin', 'memory'] },
      ...overrides,
    },
  }
}

async function mockHealth(page: Page, body: unknown) {
  await page.route('**/api/health/system', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(body),
    }),
  )
}

const runtimeChip = (page: Page) => page.locator('[data-testid="foot-health-runtimes"]')

test.describe('Footer degraded tooltip (#1461)', () => {
  test('lists ONLY the failing check, with its errored slot names', async ({ page }) => {
    await mockHealth(page, healthSystem())
    await page.goto('/')
    await expect(page.locator('.footer')).toBeVisible()

    const chip = runtimeChip(page)
    await expect(chip).toBeVisible()
    await expect(chip).toHaveAttribute('title', /degraded/, { timeout: 10_000 })

    const title = (await chip.getAttribute('title')) ?? ''
    // The one genuinely failing check, with its real reason.
    expect(title).toContain('slot_manager')
    expect(title).toContain('flm')
    // The four healthy checks must NOT be listed as failing.
    expect(title).not.toContain('disk_state')
    expect(title).not.toContain('disk_config')
    expect(title).not.toContain('event_bus')
    expect(title).not.toContain('mcp_mount')
  })

  test("surfaces a failing check's `detail` when the backend emits one", async ({ page }) => {
    await mockHealth(
      page,
      healthSystem({
        slot_manager: { ok: true, slots: 3, errored: [] },
        mcp_mount: { ok: false, servers: [], detail: 'no MCP servers mounted' },
      }),
    )
    await page.goto('/')
    await expect(page.locator('.footer')).toBeVisible()

    const chip = runtimeChip(page)
    await expect(chip).toHaveAttribute('title', /degraded/, { timeout: 10_000 })

    const title = (await chip.getAttribute('title')) ?? ''
    expect(title).toContain('mcp_mount: no MCP servers mounted')
    expect(title).not.toContain('slot_manager')
    expect(title).not.toContain('disk_state')
  })
})
