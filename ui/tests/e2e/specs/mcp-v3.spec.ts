/**
 * mcp-v3 — `#mcp` (alias `#agents/mcp`) renders the MCP servers page
 * with KPI strip, clients ribbon (or empty state), filter bar, and
 * server list with LiveTimeline ticks.
 */
import { test, expect } from '../fixtures/apiMock'

test.describe('MCP v3 (/agents/mcp)', () => {
  test.skip('renders MCP view + KPI strip', async ({ page }) => {
    await page.goto('/#mcp')
    await expect(page.locator('.view .vh h1')).toHaveText('MCP Servers')
    await expect(page.locator('.mcp-kpi')).toBeVisible()
    const cells = page.locator('.mcp-kpi-cell')
    expect(await cells.count()).toBeGreaterThanOrEqual(5)
  })

  test.skip('filter bar tabs render with counts', async ({ page }) => {
    await page.goto('/#mcp')
    await expect(page.locator('.mcp-filterbar')).toBeVisible()
    const tabs = page.locator('.mcp-tabs .mcp-tab')
    expect(await tabs.count()).toBeGreaterThan(0)
  })

  test.skip('server list + at least one LiveTimeline tick render', async ({ page }) => {
    await page.goto('/#mcp')
    await expect(page.locator('.mcp-list')).toBeVisible()
    // LiveTimeline ticks may stream in async — wait briefly for at least one
    await expect(page.locator('.mcp-tl-tick').first()).toBeVisible({ timeout: 10000 })
  })

  test.skip('alias #agents/mcp routes to the same view', async ({ page }) => {
    await page.goto('/#agents/mcp')
    await expect(page.locator('.view .vh h1')).toHaveText('MCP Servers')
  })
})
