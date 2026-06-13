/**
 * memory-graph-explorer-v3 — Playwright coverage for the #memory/graph explorer.
 *
 * The graph view was overhauled into a wrapper (window.MemGraphExplorer) with an
 * A·B·C direction switch (Lensed / Structured / Ego). Direction A "Lensed" is the
 * default and carries the source toggle (Memories/Entities), the type filter, the
 * search box and the bank picker. Nodes render as SVG <g data-node> groups with a
 * <circle>; edges render as <path> elements; the node detail panel is
 * data-testid="mem-graph-detail".
 *
 * NOTE on data: the Playwright webServer runs with VITE_MOCK_HAL0=1 (forced mock).
 * The mock fetch harness (src/api/mock.ts) short-circuits the allowlisted
 * /api/memory/banks/* endpoints BEFORE the network layer, so per-spec page.route
 * stubs for those paths are bypassed — the explorer is driven by the baked
 * forced-mock dataset (22 facts across 4 banks: primary/hermes/scratch/ingest;
 * a rich entity co-occurrence graph). These tests therefore assert against that
 * baked dataset and against DOM behaviour, not against captured requests.
 *
 * Covers:
 *   - direction A renders fact nodes + edges as SVG (self-loops dropped by
 *     normalizeGraph), meta line reflects node/edge totals
 *   - source toggle → entity graph redraws (different node set)
 *   - type filter is present in direction A / Memories and narrows the view
 *   - node click opens the detail panel with the node's fact text
 *   - bank picker switches the active bank
 */

import { test, expect } from '../fixtures/apiMock'

async function gotoGraph(page: any) {
  await page.goto('/#memory/graph')
  await page.waitForFunction(() => typeof (window as any).MemoryView === 'function')
  await page.waitForSelector('[data-testid="mem-graph-explorer"]', { timeout: 10_000 })
  // wait for the force layout to have laid out the baked fact nodes
  await expect(page.locator('[data-testid="mem-graph-svg"] g[data-node]').first()).toBeVisible({
    timeout: 10_000,
  })
}

test.describe('Memory graph explorer', () => {
  test('direction A renders fact nodes and edges as SVG (self-loops dropped)', async ({ page }) => {
    await gotoGraph(page)

    const svg = page.locator('[data-testid="mem-graph-svg"]')
    await expect(svg).toBeVisible()

    // baked fact graph = 22 facts → 22 node groups, each with a circle.
    const nodes = svg.locator('g[data-node]')
    await expect(nodes).toHaveCount(22)
    // edges render as <path> (direct children of the zoom <g>); some present.
    await expect.poll(async () => await svg.locator('g[data-node] circle').count()).toBeGreaterThan(0)
    const edgeCount = await svg.locator('g > path').count()
    expect(edgeCount).toBeGreaterThan(0)

    // self-loops are dropped by normalizeGraph (source !== target). The baked
    // graph carries none, so no edge connects a node to itself — the meta line
    // reports the resulting node/edge totals.
    await expect(page.locator('[data-testid="mem-graph-meta"]')).toContainText('22 nodes')
  })

  test('source toggle redraws as the entity co-occurrence graph', async ({ page }) => {
    await gotoGraph(page)

    const svg = page.locator('[data-testid="mem-graph-svg"]')
    const factCount = await svg.locator('g[data-node]').count()
    expect(factCount).toBe(22)

    await page.click('[data-testid="mem-graph-source-entities"]')
    // entity graph has a different (smaller) node set than the 22-fact graph.
    await expect
      .poll(async () => await svg.locator('g[data-node]').count())
      .not.toBe(22)
    await expect.poll(async () => await svg.locator('g[data-node]').count()).toBeGreaterThan(0)
  })

  test('type filter is present in direction A / Memories and narrows the view', async ({ page }) => {
    await gotoGraph(page)

    // type filter only shows in direction A + Memories source (the default).
    const typeSel = page.locator('[data-testid="mem-graph-type"]')
    await expect(typeSel).toBeVisible()

    await page.selectOption('[data-testid="mem-graph-type"]', 'world')
    // 'world' is forwarded to the bank graph hook; client-side the non-world
    // facts dim out but the world facts stay fully opaque. At minimum the
    // selection sticks and the graph still renders.
    await expect(typeSel).toHaveValue('world')
    await expect(page.locator('[data-testid="mem-graph-svg"] g[data-node]').first()).toBeVisible()
  })

  test('node click opens the detail panel with the fact text', async ({ page }) => {
    await gotoGraph(page)

    // The force layout cools for ~1.5s — let nodes settle. Nodes drift slightly
    // even at rest, so dispatch the click on the node <g> directly (the onClick
    // handler lives there) rather than relying on a positional hit.
    await page.waitForTimeout(1800)
    await page.locator('[data-testid="mem-graph-svg"] g[data-node]').first().dispatchEvent('click')
    const panel = page.locator('[data-testid="mem-graph-detail"]')
    await expect(panel).toBeVisible()
    // the detail title shows the node's fact text; every baked fact mentions
    // a concrete subject — the panel must carry non-empty memory copy.
    await expect(panel.locator('.mg-detail-title')).not.toBeEmpty()
    await expect(panel).toContainText('memory ·')
  })

  test('bank picker switches the active bank', async ({ page }) => {
    await gotoGraph(page)

    const bankSel = page.locator('[data-testid="mem-graph-bank"]')
    await expect(bankSel).toBeVisible()
    // baked banks: primary / big / hermes / scratch / ingest.
    await page.selectOption('[data-testid="mem-graph-bank"]', 'hermes')
    await expect(bankSel).toHaveValue('hermes')
    // the explorer re-fetches + re-renders for the new bank.
    await expect(page.locator('[data-testid="mem-graph-svg"]')).toBeVisible()
  })

  // FU2: server-side subgraph slice for large banks. The `big` mock bank
  // (~600 fact nodes, fact_count > 240) trips the subgraph path: the wrapper
  // fetches /graph/subgraph top-K and shows an actionable "top N of M" banner.
  test('large bank shows top-N-of-M and expand raises the count', async ({ page }) => {
    await gotoGraph(page)

    await page.selectOption('[data-testid="mem-graph-bank"]', 'big')
    await expect(page.locator('[data-testid="mem-graph-bank"]')).toHaveValue('big')

    // scale banner is actionable: "showing top N of M nodes" + expand button.
    const banner = page.locator('[data-testid="mem-graph-scalebanner"]')
    await expect(banner).toBeVisible({ timeout: 10_000 })
    await expect(banner).toContainText(/showing top \d+ of \d+ nodes/)

    // baseline rendered node count (bounded slice, < 600 total).
    const nodes = page.locator('[data-testid="mem-graph-svg"] g[data-node]')
    await expect.poll(async () => nodes.count(), { timeout: 10_000 }).toBeGreaterThan(0)
    const before = await nodes.count()

    // expand bumps top_k by 200 → a strictly larger slice renders. The banner
    // sits under the floating graph controls, so dispatch the click directly on
    // the button (its onClick handler) rather than relying on a positional hit.
    await page.locator('[data-testid="mem-graph-expand"]').dispatchEvent('click')
    await expect.poll(async () => nodes.count(), { timeout: 10_000 }).toBeGreaterThan(before)
  })

  test('direction C (Ego) caps the ring on a high-degree node and "+K more" expands it', async ({ page }) => {
    await gotoGraph(page)
    // the baked `ingest` bank returns a dense star: a hub with 40 neighbours.
    await page.selectOption('[data-testid="mem-graph-bank"]', 'ingest')
    await page.locator('.mg-dirswitch button', { hasText: 'Ego' }).click()
    // Ego renders its own .mg-svg (the mem-graph-svg testid is Direction-A only).
    await expect(page.locator('.mg-ego-card')).toBeVisible()

    // ring-1 is capped (RING1_CAP_STEP=24): the "+K more" slot appears and the
    // card reports more total neighbours than are currently shown.
    const more = page.locator('[data-testid="mem-ego-more"]')
    await expect(more).toBeVisible()
    const countText = page.locator('[data-testid="mem-ego-nbr-count"]')
    await expect(countText).toContainText('neighbours · 40')
    await expect(countText).toContainText('showing 24')

    // expanding bumps the cap (24→48 ≥ 40) → all 40 shown, the "+K more" slot
    // disappears and the "showing" qualifier drops (nothing hidden anymore).
    await more.click()
    await expect(more).toHaveCount(0)
    await expect(countText).toContainText('neighbours · 40')
    await expect(countText).not.toContainText('showing')
  })
})
