/**
 * memory-gate-v3 — the gate on the memory subsystem.
 *
 * The memory engine (Hindsight), its MCP server, the REST surface and the
 * dashboard's Memory area ship ENABLED by default. The backend gates them
 * behind [memory].enabled in hal0.toml and reports the resulting state via
 * /api/status `memory_enabled`. This spec pins the (still-supported) OFF
 * state.
 *
 * Memory is a top-level area (#memory/<tab>) with its own sidebar row, which
 * follows the gate. The Agents row and its sub-links never did and still do
 * not. This spec pins the UI half of that contract for the OFF state:
 *
 *   - the sidebar has no Memory row; the Agents row and its Overview and MCP
 *     sub-links stay
 *   - a deep link into the area goes to the dashboard once the status check
 *     has answered (the redirect cases are in memory-redirects-v3)
 *   - the command palette offers no Memory route, and the Overview page has
 *     no Memory card
 *   - the removed sidebar Runtime widget does not reappear under the gate
 *
 * The ON state is covered by the default mock (memory_enabled: true) across
 * memory-area-v3 / memory-health-v3 / memory-v2-workspace.
 */
import { test, expect } from '../fixtures/apiMock'
import { memoryOff } from '../fixtures/memoryArea'

const FIVE_S = 5_500

test.describe('memory gate OFF ([memory].enabled=false)', () => {
  test.beforeEach(async ({ page }) => {
    // The γ-suite runs under forced-mock (VITE_MOCK_HAL0), which
    // short-circuits page.route for allowlisted URLs like /api/status. The
    // mock's buildStatus honours a window flag so we can drive the disabled
    // path; it must be set before any page script evaluates.
    await memoryOff(page)
  })

  test('sidebar drops both Memory rows and keeps Agents with its sub-links', async ({ page }) => {
    await page.goto('/#dashboard')
    const navList = page.locator('.sidebar .sb-list')
    await expect(navList).toBeVisible({ timeout: FIVE_S })
    // Control: sibling nav items are still present...
    await expect(navList.getByText('Models', { exact: true })).toBeVisible()
    await expect(navList.locator('[data-testid="nav-services"]')).toBeVisible()
    // ...the Agents row ALWAYS renders (it is not gated)...
    await expect(navList.locator('[data-testid="nav-agent"]')).toBeVisible()
    await navList.locator('[data-testid="nav-agent-toggle"]').click()
    await expect(navList.locator('[data-testid="nav-agent-overview"]')).toBeVisible()
    await expect(navList.locator('[data-testid="nav-mcp"]')).toBeVisible()
    // ...but there is no Memory row.
    await expect(navList.locator('[data-testid="nav-memory"]')).toHaveCount(0)
    await expect(navList.locator('.sb-row .lbl').filter({ hasText: /Memory/ })).toHaveCount(0)
  })

  test('deep link into the Memory area goes to the dashboard', async ({ page }) => {
    await page.goto('/#memory')
    await expect(page).toHaveURL(/#dashboard$/, { timeout: FIVE_S })
    await expect(page.locator('.ov-now')).toBeVisible()
    // Neither the area nor the old Agents ▸ Memory surface renders under the gate.
    await expect(page.locator('[data-testid="memory-area"]')).toHaveCount(0)
    await expect(page.locator('[data-testid="agent-tab-memory"]')).toHaveCount(0)
    await expect(page.locator('[data-testid="mem-provider-card-hindsight"]')).toHaveCount(0)
  })

  test('the redirect does not leave the dead address behind it in the history', async ({ page }) => {
    await page.goto('/#slots')
    await expect(page.locator('.sidebar .sb-list')).toBeVisible({ timeout: FIVE_S })
    await page.evaluate(() => {
      window.location.hash = '#memory/health'
    })
    await expect(page).toHaveURL(/#dashboard$/, { timeout: FIVE_S })
    // Back returns to where the operator was, not to #memory/health (which
    // would bounce forward again).
    await page.goBack()
    await expect(page).toHaveURL(/#slots$/)
  })

  test('no Memory route in the command palette, no Memory card on Overview', async ({ page }) => {
    await page.goto('/#dashboard')
    await expect(page.locator('.ov-now')).toBeVisible({ timeout: FIVE_S })
    await expect(page.getByRole('button', { name: /Open memory/ })).toHaveCount(0)

    await page.keyboard.press('Control+k')
    const palette = page.locator('.cp-shell')
    await expect(palette).toBeVisible()
    await page.keyboard.type('Memory')
    // Settings ▸ Memory is still listed (the engine's settings exist with
    // the engine off); the Memory area's own route is not.
    await expect(palette.locator('.cp-item').filter({ hasText: 'engine, reranker client, graph' })).toHaveCount(1)
    await expect(palette.locator('.cp-item').filter({ hasText: 'health, facts, knowledge' })).toHaveCount(0)
  })

  test('removed Runtime widget does not reappear', async ({ page }) => {
    await page.goto('/#dashboard')
    await expect(page.locator('.sb-list').first()).toBeVisible({ timeout: FIVE_S })
    await expect(page.locator('[data-testid="sidebar-runtime-widget"]')).toHaveCount(0)
    await expect(page.locator('[data-testid="sidebar-agent-open-memory"]')).toHaveCount(0)
  })
})
