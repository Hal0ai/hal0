/**
 * memory-v2-overview — Playwright coverage for the Memory v2 Bank workspace
 * Overview (`window.MemV2Overview`, task C2).
 *
 * NOT ROUTED YET: `#memory` still renders the pre-v2 `MemoryView`
 * (dash/memory.jsx) — wiring the new Overview into that route is task C6's
 * job, not C2's, and the brief explicitly forbids touching memory.jsx /
 * agent-view.jsx here to force a route just for this spec. Per the brief's
 * fallback, this whole spec is `.skip`-ed until C6 lands the route; the
 * component itself is smoke-tested in the meantime by
 * `ui/src/dash/__tests__/memoryOverviewV2.smoke.test.tsx` (mounts
 * `window.MemV2Overview` under a real QueryClientProvider with the real
 * hook globals installed, via `react-dom/server`'s `renderToStaticMarkup`).
 *
 * C6 unskips: once `#memory` (or a `#memory/overview` sub-route) renders
 * `window.MemV2Overview`, remove the `.skip` and this comment block's first
 * paragraph. The assertions below describe the intended contract per the
 * task C2 brief.
 */
import { test, expect } from '../fixtures/apiMock'

test.describe.skip('memory-v2 Overview (C6 unskips — not routed yet)', () => {
  test('engine panel renders stats from the mock engine card', async ({ page }) => {
    await page.goto('/#memory')
    const panel = page.getByTestId('mv-engine-panel')
    await expect(panel).toBeVisible()
    // buildMemoryEngine() mock: reachable: true, engine: 'hindsight'.
    await expect(panel).toContainText('reachable')
    await expect(panel).toContainText('hindsight')
  })

  test('bank table renders a row for each of the six mock banks', async ({ page }) => {
    await page.goto('/#memory')
    // MEM_BANKS in mockFixtures.ts: primary, big, hermes, scratch, ingest, empty.
    for (const bank of ['primary', 'big', 'hermes', 'scratch', 'ingest', 'empty']) {
      await expect(page.getByTestId(`mv-bank-row-${bank}`)).toBeVisible()
    }
  })

  test('growth chart renders bars for the primary bank and range switch works', async ({ page }) => {
    await page.goto('/#memory')
    const growth = page.getByTestId('mv-growth')
    await expect(growth).toBeVisible()
    await expect(growth.locator('svg rect').first()).toBeVisible()

    await page.getByTestId('mv-growth-range-7d').click()
    await expect(page.getByTestId('mv-growth-range-7d')).toHaveClass(/on/)
    await page.getByTestId('mv-growth-range-1d').click()
    await expect(page.getByTestId('mv-growth-range-1d')).toHaveClass(/on/)
    await page.getByTestId('mv-growth-range-30d').click()
    await expect(page.getByTestId('mv-growth-range-30d')).toHaveClass(/on/)
  })

  test('explore button navigates to #memory/bank?bank=primary', async ({ page }) => {
    await page.goto('/#memory')
    await page.getByTestId('mv-bank-explore-primary').click()
    await expect(page).toHaveURL(/#memory\/bank\?bank=primary/)
  })
})
