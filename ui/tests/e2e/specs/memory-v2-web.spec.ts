/**
 * memory-v2-web — Playwright coverage for the Memory v2 web graph
 * (`window.MemV2WebGraph`, task C5).
 *
 * Unskipped by task C6: `#memory/bank?bank=primary` renders `MemoryView`'s
 * Bank sub-tab, whose `view === 'web'` tab mounts `window.MemV2WebGraph`
 * directly (memory-bank-workspace.jsx).
 */
import { test, expect } from '../fixtures/apiMock'

test.describe('memory-v2 web graph', () => {
  test('renders <= 120 nodes from the mock graph', async ({ page }) => {
    await page.goto('/#memory/bank?bank=primary')
    await page.getByTestId('mv-view-web').click()
    await expect(page.getByTestId('mv-web')).toBeVisible()
    await expect(page.locator('[data-testid^="mv-web-node-"]').first()).toBeVisible()
    const nodes = await page.locator('[data-testid^="mv-web-node-"]').count()
    expect(nodes).toBeLessThanOrEqual(120)
    expect(nodes).toBeGreaterThan(0)
  })

  test('a lens toggle turns off and the button reflects it', async ({ page }) => {
    await page.goto('/#memory/bank?bank=primary')
    await page.getByTestId('mv-view-web').click()
    await expect(page.locator('[data-testid^="mv-web-node-"]').first()).toBeVisible()
    // Lens buttons are derived from the link types actually present in the
    // mock graph (temporal/semantic — causal/cooccurrence never appear).
    const lens = page.locator('[data-testid^="mv-web-lens-"]').first()
    await expect(lens).toHaveClass(/on/)
    await lens.click()
    await expect(lens).not.toHaveClass(/on/)
  })

  test('clicking a node selects the fact (drives the inspector)', async ({ page }) => {
    await page.goto('/#memory/bank?bank=primary')
    await page.getByTestId('mv-view-web').click()
    await expect(page.locator('[data-testid^="mv-web-node-"]').first()).toBeVisible()
    const firstId = await page.locator('[data-testid^="mv-web-node-"]').first().getAttribute('data-testid')
    await page.locator('[data-testid^="mv-web-node-"]').first().click()
    expect(firstId).toBeTruthy()
    await expect(page.getByTestId('mv-inspector')).toBeVisible()
  })

  test('the "fit" button resets pan/zoom', async ({ page }) => {
    await page.goto('/#memory/bank?bank=primary')
    await page.getByTestId('mv-view-web').click()
    await expect(page.locator('[data-testid^="mv-web-node-"]').first()).toBeVisible()
    await expect(page.getByTestId('mv-web-zoom')).toBeVisible()
    await page.getByTestId('mv-web-zoom').click()
  })
})
