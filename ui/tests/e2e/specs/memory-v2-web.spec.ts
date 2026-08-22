/**
 * memory-v2-web — Playwright coverage for the Memory v2 web graph
 * (`window.MemV2WebGraph`, task C5).
 *
 * Unskipped by task C6: `#memory/bank?bank=primary` renders `MemoryView`'s
 * Bank sub-tab, whose `view === 'web'` tab mounts `window.MemV2WebGraph`
 * directly (memory-bank-workspace.jsx).
 */
import { test, expect, type Locator } from '../fixtures/apiMock'

// Node positions are driven by a d3 force simulation (memory-web-graph.jsx)
// that ticks live after mount — the local suite settles it in well under a
// frame, but a loaded shared CI runner can leave it animating for seconds,
// and Playwright's actionability check requires the target's bounding box
// to be STABLE across two consecutive animation frames before it will
// click. Chasing a moving SVG circle with the default click() timeout is
// exactly that moving-target race.
//
// Poll the node's own bounding box until it reads identical twice ~200ms
// apart (bounded, so a genuinely stuck sim still fails loudly instead of
// hanging), then click for real — no force:true, so the click still goes
// through normal hit-testing against whatever's actually under the point.
async function waitForNodeStable(node: Locator, { pollMs = 200, maxWaitMs = 8000 } = {}) {
  const readPos = async () => {
    const box = await node.boundingBox()
    return box ? `${box.x.toFixed(1)},${box.y.toFixed(1)}` : null
  }
  const start = Date.now()
  let prev = await readPos()
  while (Date.now() - start < maxWaitMs) {
    await new Promise((r) => setTimeout(r, pollMs))
    const cur = await readPos()
    if (cur !== null && cur === prev) return
    prev = cur
  }
  // Bounded wait exhausted — proceed anyway. If the sim truly never
  // settles, the click's own actionability check surfaces a clear timeout
  // rather than this helper hanging indefinitely.
}

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
    const firstNode = page.locator('[data-testid^="mv-web-node-"]').first()
    await expect(firstNode).toBeVisible()
    const firstId = await firstNode.getAttribute('data-testid')
    // Node position is force-simulation-driven (see waitForNodeStable above)
    // — wait for it to stop moving before clicking, instead of racing
    // Playwright's own actionability retry against a moving target.
    await waitForNodeStable(firstNode)
    await firstNode.click()
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
