/**
 * memory-graph-explorer-v3 — Playwright coverage for the #memory/graph explorer.
 *
 * Payload shapes pinned from live Hindsight 0.7.2 on CT105: nodes/edges are
 * Cytoscape-style {data: {...}} wrappers; memory nodes carry text/date/
 * entities, entity nodes carry label/mentionCount; edges carry linkType.
 *
 * Covers:
 *   - #memory/graph renders the explorer with an SVG force layout
 *   - memory-graph source: nodes + edges drawn (self-loop edges dropped)
 *   - source toggle → entities co-occurrence endpoint hit, nodes redrawn
 *   - type filter forwards ?type= to the bank graph endpoint
 *   - node click opens the detail panel with node text
 *   - bank picker switches the bank in the endpoint path
 */

import { test, expect, json } from '../fixtures/apiMock'

const ENGINE = {
  enabled: true,
  engine: 'hindsight',
  reachable: true,
  version: '0.7.2',
  features: { observations: true },
  banks_total: 2,
}

const BANKS = {
  banks: [
    { bank_id: 'shared', fact_count: 3 },
    { bank_id: 'private__hermes', fact_count: 1 },
  ],
}

const MEM_GRAPH = {
  nodes: [
    {
      data: {
        id: 'n1',
        label: 'HAL 0.5 was re-enabled…',
        text: 'HAL 0.5 was re-enabled with Hindsight memory engine.',
        date: '2026-06-07T09:11:52Z',
        context: '',
        entities: 'HAL 0.5, Hindsight memory engine',
        color: '#42a5f5',
      },
    },
    {
      data: {
        id: 'n2',
        label: 'HAL 0.5 re-enabled its brain…',
        text: 'HAL 0.5 re-enabled its brain using Hindsight memory engine',
        date: '2026-06-07T09:11:52Z',
        context: 'anonymous',
        entities: 'HAL 0.5',
        color: '#42a5f5',
      },
    },
    {
      data: {
        id: 'n3',
        label: 'Observation: platform memory is live',
        text: 'Observation: platform memory is live',
        date: '2026-06-07T09:15:14Z',
        context: '',
        entities: '',
        color: '#66bb6a',
      },
    },
  ],
  edges: [
    // self-loop — must be dropped by the renderer
    { data: { id: 'e0', source: 'n2', target: 'n2', linkType: 'semantic', weight: 1 } },
    { data: { id: 'e1', source: 'n1', target: 'n2', linkType: 'semantic', weight: 1 } },
    { data: { id: 'e2', source: 'n2', target: 'n3', linkType: 'temporal', weight: 0.5 } },
  ],
  table_rows: [],
  total_units: 3,
  limit: 200,
}

const ENT_GRAPH = {
  nodes: [
    { data: { id: 'ent1', label: 'HAL 0.5', mentionCount: 2, color: '#90caf9' } },
    { data: { id: 'ent2', label: 'Hindsight memory engine', mentionCount: 1, color: '#90caf9' } },
  ],
  edges: [
    {
      data: {
        id: 'ent1-ent2',
        source: 'ent1',
        target: 'ent2',
        linkType: 'cooccurrence',
        weight: 1,
        lastCooccurred: '2026-06-07T09:11:52Z',
      },
    },
  ],
  total_entities: 2,
  total_edges: 1,
  limit: 500,
}

async function installGraphMocks(page: any, captured: { graph: any[]; entities: any[] }) {
  await page.route('**/api/memory/engine', (route: any) => json(route, ENGINE))
  await page.route('**/api/memory/banks', (route: any) => json(route, BANKS))
  await page.route('**/api/memory/banks/*/stats**', (route: any) =>
    json(route, { nodes_by_fact_type: {}, pending_operations: 0, failed_operations: 0 }),
  )
  await page.route('**/api/memory/banks/*/graph**', (route: any) => {
    const url = new URL(route.request().url())
    captured.graph.push({ path: url.pathname, params: Object.fromEntries(url.searchParams) })
    return json(route, MEM_GRAPH)
  })
  await page.route('**/api/memory/banks/*/entities/graph**', (route: any) => {
    const url = new URL(route.request().url())
    captured.entities.push({ path: url.pathname, params: Object.fromEntries(url.searchParams) })
    return json(route, ENT_GRAPH)
  })
}

async function gotoGraph(page: any) {
  await page.goto('/#memory/graph')
  await page.waitForFunction(() => typeof (window as any).MemoryView === 'function')
  await page.waitForSelector('[data-testid="mem-graph-explorer"]', { timeout: 10_000 })
}

test.describe('Memory graph explorer', () => {
  test('renders memory-graph nodes and edges as SVG, dropping self-loops', async ({ page }) => {
    const captured = { graph: [] as any[], entities: [] as any[] }
    await installGraphMocks(page, captured)
    await gotoGraph(page)

    const svg = page.locator('[data-testid="mem-graph-svg"]')
    await expect(svg).toBeVisible()
    await expect(svg.locator('circle.mem-gnode')).toHaveCount(3)
    // 3 edges in payload, 1 is a self-loop → 2 rendered
    await expect(svg.locator('line.mem-gedge')).toHaveCount(2)
    // stats strip reflects totals
    await expect(page.locator('[data-testid="mem-graph-meta"]')).toContainText('3 nodes')
  })

  test('source toggle hits the entity co-occurrence endpoint', async ({ page }) => {
    const captured = { graph: [] as any[], entities: [] as any[] }
    await installGraphMocks(page, captured)
    await gotoGraph(page)

    await page.click('[data-testid="mem-graph-source-entities"]')
    await expect(page.locator('[data-testid="mem-graph-svg"] circle.mem-gnode')).toHaveCount(2)
    await expect.poll(() => captured.entities.length).toBeGreaterThan(0)
    expect(captured.entities[0].path).toContain('/entities/graph')
  })

  test('type filter forwards ?type= to the bank graph endpoint', async ({ page }) => {
    const captured = { graph: [] as any[], entities: [] as any[] }
    await installGraphMocks(page, captured)
    await gotoGraph(page)

    await page.selectOption('[data-testid="mem-graph-type"]', 'world')
    await expect
      .poll(() => captured.graph.some((g) => g.params.type === 'world'))
      .toBe(true)
  })

  test('node click opens detail panel with the fact text', async ({ page }) => {
    const captured = { graph: [] as any[], entities: [] as any[] }
    await installGraphMocks(page, captured)
    await gotoGraph(page)

    await page.locator('circle.mem-gnode').first().click()
    const panel = page.locator('[data-testid="mem-graph-detail"]')
    await expect(panel).toBeVisible()
    await expect(panel).toContainText('Hindsight memory engine')
  })

  test('bank picker switches the endpoint path', async ({ page }) => {
    const captured = { graph: [] as any[], entities: [] as any[] }
    await installGraphMocks(page, captured)
    await gotoGraph(page)

    await page.selectOption('[data-testid="mem-graph-bank"]', 'private__hermes')
    await expect
      .poll(() => captured.graph.some((g) => g.path.includes('private__hermes')))
      .toBe(true)
  })
})
