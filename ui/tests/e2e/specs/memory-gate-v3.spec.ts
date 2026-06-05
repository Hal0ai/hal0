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
 *   - a deep link to #agent shows a "disabled" notice, not the Memory tab
 *   - the SidebarAgentBlock omits its "Memory →" CTA (no dead-end link)
 *
 * The ON state is covered by the default mock (memory_enabled: true) across
 * agent-view-v3 / memory-graph-v3 / sidebar-agent-block.
 */
import { test, expect, json } from '../fixtures/apiMock'

const FIVE_S = 5_500

test.describe('memory gate OFF (HAL0_MEMORY_ENABLED unset)', () => {
  test.beforeEach(async ({ page }) => {
    // The γ-suite runs under forced-mock (VITE_MOCK_LEMONADE), which
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

  test('deep link to #agent shows the disabled notice, not the Memory tab', async ({ page }) => {
    await page.goto('/#agent')
    await expect(page.getByText('The memory surface is disabled in this release.')).toBeVisible({
      timeout: FIVE_S,
    })
    await expect(page.locator('[data-testid="memory-tab"]')).toHaveCount(0)
    await expect(page.locator('[data-testid="agent-tab-nav"]')).toHaveCount(0)
  })

  test('SidebarAgentBlock omits the Memory CTA', async ({ page }) => {
    await page.goto('/#dashboard')
    await expect(page.locator('.sb-list')).toBeVisible({ timeout: FIVE_S })
    await expect(page.locator('[data-testid="sidebar-agent-open-memory"]')).toHaveCount(0)
  })
})
