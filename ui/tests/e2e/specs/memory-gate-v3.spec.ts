/**
 * memory-gate-v3 — 0.4 release gate for the memory subsystem.
 *
 * The memory engine (Cognee), its MCP server, the REST surface, and the
 * dashboard's Agent → Memory tab ship DISABLED by default and return in a
 * later release. The backend gates them behind HAL0_MEMORY_ENABLED and
 * reports the resulting state via /api/status `memory_enabled`. This spec
 * pins the UI half of that contract for the OFF state:
 *
 *   - the sidebar drops the "Agent" nav item entirely
 *   - a deep link to #agent redirects to #dashboard (stale deep links bounce,
 *     no dead-text "disabled" notice)
 *   - the sidebar Runtime widget carries no dead-end "Memory →" link
 *     (the widget consolidated the old SidebarAgentBlock; its agent row now
 *     deep-links to the Hermes dashboard, never the gated Memory route)
 *
 * The ON state is covered by the default mock (memory_enabled: true) across
 * agent-view-v3 / memory-graph-v3 / sidebar-runtime-widget.
 */
import { test, expect, json } from '../fixtures/apiMock'

const FIVE_S = 5_500

test.describe('memory gate OFF (HAL0_MEMORY_ENABLED unset)', () => {
  test.beforeEach(async ({ page }) => {
    // The γ-suite runs under forced-mock (VITE_MOCK_HAL0), which
    // short-circuits page.route for allowlisted URLs like /api/status. The
    // mock's buildStatus honours this window flag so we can drive the
    // disabled path; it must be set before any page script evaluates.
    await page.addInitScript(() => {
      ;(window as unknown as { __hal0MockMemoryEnabled?: boolean }).__hal0MockMemoryEnabled = false
    })
  })

  test('sidebar omits the Agent nav item', async ({ page }) => {
    await page.goto('/#dashboard')
    const navList = page.locator('.sb-list')
    await expect(navList).toBeVisible({ timeout: FIVE_S })
    // Control: a sibling nav item is still present...
    await expect(navList.getByText('Models', { exact: true })).toBeVisible()
    await expect(navList.getByText('MCP', { exact: true })).toBeVisible()
    // ...but the Agent (Memory) item is gone.
    await expect(navList.getByText('Agent', { exact: true })).toHaveCount(0)
  })

  test('deep link to #agent redirects to #dashboard when memory is disabled', async ({ page }) => {
    await page.goto('/#agent')
    // 0.4: stale deep links bounce to #dashboard instead of showing a
    // dead-text disabled notice. Wait for the hash to leave #agent.
    await page.waitForFunction(
      () => !window.location.hash.startsWith('#agent'),
      { timeout: FIVE_S }
    )
    // Neither memory surface element should ever appear
    await expect(page.locator('[data-testid="memory-tab"]')).toHaveCount(0)
    await expect(page.locator('[data-testid="agent-tab-nav"]')).toHaveCount(0)
  })

  test('Runtime widget renders with no dead-end Memory link', async ({ page }) => {
    await page.goto('/#dashboard')
    await expect(page.locator('.sb-list')).toBeVisible({ timeout: FIVE_S })
    // The consolidated Runtime widget still renders under the memory gate...
    await expect(page.locator('[data-testid="sidebar-runtime-widget"]')).toBeVisible()
    // ...and never offers the old dead-end "Memory →" CTA into the gated route.
    await expect(page.locator('[data-testid="sidebar-agent-open-memory"]')).toHaveCount(0)
  })
})
