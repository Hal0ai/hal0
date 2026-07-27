/**
 * memory-gate-v3 — 0.4 release gate for the memory subsystem.
 *
 * The memory engine (Cognee), its MCP server, the REST surface, and the
 * dashboard's Agent ▸ Memory tab ship DISABLED by default and return in a
 * later release. The backend gates them behind HAL0_MEMORY_ENABLED and
 * reports the resulting state via /api/status `memory_enabled`.
 *
 * v0.5 nav: Memory is no longer a top-level page — it is a tab inside the
 * Agent page, alongside MCP. The Agent nav item itself ALWAYS renders (the
 * MCP sub-link is ungated); only the Memory surface follows the gate. This
 * spec pins the UI half of that contract for the OFF state:
 *
 *   - the sidebar keeps the Agent nav item but drops its "Memory" sub-link
 *     (nav-memory absent); the MCP sub-link stays
 *   - the Agent page renders an MCP-only tab bar (the Memory tab is hidden),
 *     and a deep link to #memory falls back to the MCP tab
 *   - the removed sidebar Runtime widget does not reappear under the gate
 *
 * The ON state is covered by the default mock (memory_enabled: true) across
 * agent-view-v3 / memory-graph-v3.
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

  test('sidebar keeps Agent but drops its Memory sub-link', async ({ page }) => {
    await page.goto('/#dashboard')
    const navList = page.locator('.sb-list')
    await expect(navList).toBeVisible({ timeout: FIVE_S })
    // Control: sibling nav items are still present...
    await expect(navList.getByText('Models', { exact: true })).toBeVisible()
    // v0.5: the Agent nav item ALWAYS renders (the MCP sub-link is ungated).
    await expect(navList.locator('[data-testid="nav-agent"]')).toBeVisible()
    // Accordion: sub-links are collapsed until the parent is expanded. Open the
    // Agent section so its sub-links render.
    await navList.locator('[data-testid="nav-agent-toggle"]').click()
    await expect(navList.locator('[data-testid="nav-mcp"]')).toBeVisible()
    // ...but its gated Memory sub-link is gone (absent whether collapsed or not).
    await expect(navList.locator('[data-testid="nav-memory"]')).toHaveCount(0)
  })

  test('deep link to #memory falls back to the MCP-only Agent tab bar', async ({ page }) => {
    await page.goto('/#memory')
    // v0.5: Memory is a gated tab inside the Agent page. With memory disabled,
    // #memory resolves to AgentView with the MCP tab active — the Agent tab bar
    // renders, but the Memory tab button is absent.
    await expect(page.locator('[data-testid="agent-tab-nav"]')).toBeVisible({ timeout: FIVE_S })
    await expect(page.locator('[data-testid="agent-tab-mcp"]')).toBeVisible()
    await expect(page.locator('[data-testid="agent-tab-memory"]')).toHaveCount(0)
    // The Memory surface (engine card) never renders under the gate.
    await expect(page.locator('[data-testid="mem-engine-card"]')).toHaveCount(0)
  })

  test('removed Runtime widget does not reappear', async ({ page }) => {
    await page.goto('/#dashboard')
    await expect(page.locator('.sb-list')).toBeVisible({ timeout: FIVE_S })
    await expect(page.locator('[data-testid="sidebar-runtime-widget"]')).toHaveCount(0)
    await expect(page.locator('[data-testid="sidebar-agent-open-memory"]')).toHaveCount(0)
  })
})
