/**
 * memory-v2-web — Playwright coverage for the Memory v2 web graph
 * (`window.MemV2WebGraph`, task C5).
 *
 * NOT ROUTED YET: same situation as C2–C4 — `#memory` still renders the
 * pre-v2 `MemoryView`, and the web graph has no route/mount point of its
 * own yet (its intended home is the Bank workspace's `view === 'web'` tab,
 * reachable via `#memory/bank?bank=<id>`, task C6's job). The brief
 * forbids touching memory.jsx/agent-view.jsx here, and there is no
 * standalone component-mount harness in this e2e suite. Per the same
 * fallback used in C2–C4, this whole spec is `.skip`-ed; the component is
 * smoke-tested in the meantime by
 * `ui/src/dash/__tests__/memoryWebGraph.smoke.test.tsx` (mounts
 * `window.MemV2WebGraph` under a real QueryClientProvider with the real
 * hook globals installed, via `react-dom/server`'s `renderToStaticMarkup`)
 * and the salience-cap math is unit-tested directly in
 * `ui/src/dash/__tests__/memoryWebGraphSalience.test.ts`.
 *
 * C6 unskips: once the web graph is mounted somewhere reachable, remove
 * the `.skip` and this comment block's first two paragraphs. The
 * assertions below describe the intended contract per the task C5 brief.
 */
import { test, expect } from '../fixtures/apiMock'

test.describe.skip('memory-v2 web graph (C6 unskips — not routed yet)', () => {
  test('renders <= 120 nodes from the mock graph', async ({ page }) => {
    // await page.goto('/#memory/bank?bank=primary')
    // await page.getByTestId('mv-view-web').click()
    // await expect(page.getByTestId('mv-web')).toBeVisible()
    // const nodes = await page.locator('[data-testid^="mv-web-node-"]').count()
    // expect(nodes).toBeLessThanOrEqual(120)
  })

  test('a lens toggle dims non-matching link types', async ({ page }) => {
    // await page.getByTestId('mv-web-lens-temporal').click()
    // temporal-typed edges/lines should drop to the dimmed opacity while
    // semantic/entity links stay lit
  })

  test('clicking a node selects the fact (drives the inspector)', async ({ page }) => {
    // await page.getByTestId('mv-web-node-f6').click()
    // await expect(page.getByTestId('mv-inspector')).toBeVisible()
  })
})
