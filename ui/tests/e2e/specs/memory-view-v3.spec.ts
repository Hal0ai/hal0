/**
 * memory-view-v3 — Playwright coverage for the Hindsight Memory view (#memory).
 *
 * READ path: default mocks (memory_enabled: true via /api/status) + per-spec
 * page.route overrides for the /api/memory/engine + /api/memory/banks surface.
 *
 * Covers:
 *   - Nav: "Memory" item present when memory_enabled, routes to #memory
 *   - Engine card: version + reachable chip + bank count from /api/memory/engine
 *   - Engine card: unreachable state renders a degraded (not crashed) card
 *   - Bank cards: fact-type counts, pending/failed op badges from per-bank stats
 *   - Timeseries: SVG chart renders from stats/timeseries buckets
 *   - Operations panel: failed op row exposes Retry → POST .../operations/{id}/retry
 *   - Create bank: form PUTs /api/memory/banks/{name}
 */

import { test, expect, json } from '../fixtures/apiMock'

const ENGINE = {
  enabled: true,
  engine: 'hindsight',
  reachable: true,
  version: '0.7.2',
  features: { observations: true, mcp: true, worker: true, bank_config_api: true, file_upload_api: true },
  banks_total: 2,
}

const BANKS = {
  banks: [
    {
      bank_id: 'shared',
      name: 'shared',
      mission: 'Platform-wide shared memory',
      created_at: '2026-06-01T00:00:00Z',
      updated_at: '2026-06-07T09:15:14Z',
      fact_count: 42,
      last_document_at: '2026-06-07T09:11:52Z',
    },
    {
      bank_id: 'private__hermes',
      name: 'private__hermes',
      mission: null,
      created_at: '2026-06-02T00:00:00Z',
      updated_at: '2026-06-07T10:00:00Z',
      fact_count: 7,
      last_document_at: null,
    },
  ],
}

const SHARED_STATS = {
  bank_id: 'shared',
  total_nodes: 42,
  total_links: 18,
  total_documents: 6,
  nodes_by_fact_type: { world: 25, experience: 12, observation: 5 },
  links_by_link_type: { semantic: 10, temporal: 8 },
  pending_operations: 1,
  failed_operations: 2,
  operations_by_status: { completed: 31, pending: 1, failed: 2 },
  last_consolidated_at: '2026-06-07T09:15:14Z',
  pending_consolidation: 0,
  failed_consolidation: 0,
  total_observations: 5,
}

const HERMES_STATS = {
  bank_id: 'private__hermes',
  total_nodes: 7,
  total_links: 2,
  total_documents: 2,
  nodes_by_fact_type: { world: 4, experience: 3 },
  links_by_link_type: { semantic: 2 },
  pending_operations: 0,
  failed_operations: 0,
  operations_by_status: { completed: 9 },
  last_consolidated_at: null,
  pending_consolidation: 0,
  failed_consolidation: 0,
  total_observations: 0,
}

const TIMESERIES = {
  bucket_size: '1d',
  buckets: [
    { time: '2026-06-05T00:00:00Z', world: 3, experience: 1, observation: 0 },
    { time: '2026-06-06T00:00:00Z', world: 10, experience: 4, observation: 2 },
    { time: '2026-06-07T00:00:00Z', world: 12, experience: 7, observation: 3 },
  ],
}

const OPERATIONS = {
  items: [
    {
      operation_id: 'op-fail-1',
      operation_type: 'consolidation',
      status: 'failed',
      created_at: '2026-06-07T09:00:00Z',
      error_message: 'LLM timeout',
      retry_count: 3,
    },
    {
      operation_id: 'op-pend-1',
      operation_type: 'retain',
      status: 'pending',
      created_at: '2026-06-07T09:10:00Z',
      error_message: null,
      retry_count: 0,
    },
  ],
  total: 2,
}

async function installMemoryMocks(page: any) {
  await page.route('**/api/memory/engine', (route: any) => json(route, ENGINE))
  await page.route('**/api/memory/banks', (route: any) => json(route, BANKS))
  await page.route('**/api/memory/banks/shared/stats', (route: any) => json(route, SHARED_STATS))
  await page.route('**/api/memory/banks/private__hermes/stats', (route: any) =>
    json(route, HERMES_STATS),
  )
  await page.route('**/api/memory/banks/shared/stats/timeseries**', (route: any) =>
    json(route, TIMESERIES),
  )
  await page.route('**/api/memory/banks/private__hermes/stats/timeseries**', (route: any) =>
    json(route, TIMESERIES),
  )
  await page.route('**/api/memory/banks/shared/operations**', (route: any) =>
    json(route, OPERATIONS),
  )
}

async function gotoMemory(page: any) {
  await page.goto('/#memory')
  await page.waitForFunction(() => typeof (window as any).MemoryView === 'function')
  await page.waitForSelector('[data-testid="mem-engine-card"]', { timeout: 10_000 })
}

test.describe('Memory view — Hindsight surface', () => {
  test.beforeEach(async ({ page }) => {
    await installMemoryMocks(page)
  })

  test('nav shows Memory item and routes to #memory', async ({ page }) => {
    await page.goto('/#dashboard')
    const nav = page.locator('[data-testid="nav-memory"]')
    await expect(nav).toBeVisible()
    await nav.click()
    await expect(page.locator('[data-testid="mem-engine-card"]')).toBeVisible()
  })

  test('engine card renders version, reachable chip, bank count', async ({ page }) => {
    await gotoMemory(page)
    const card = page.locator('[data-testid="mem-engine-card"]')
    await expect(card).toContainText('hindsight')
    await expect(card).toContainText('0.7.2')
    await expect(card.locator('.chip.ok')).toBeVisible()
    await expect(card).toContainText('2 banks')
  })

  test('engine unreachable renders degraded card, not a crash', async ({ page }) => {
    await page.route('**/api/memory/engine', (route: any) =>
      json(route, { ...ENGINE, reachable: false, version: null, banks_total: null }),
    )
    await gotoMemory(page)
    const card = page.locator('[data-testid="mem-engine-card"]')
    await expect(card).toContainText(/unreachable/i)
  })

  test('bank cards show fact-type counts and op badges', async ({ page }) => {
    await gotoMemory(page)
    const shared = page.locator('[data-testid="mem-bank-shared"]')
    await expect(shared).toBeVisible()
    await expect(shared).toContainText('world')
    await expect(shared).toContainText('25')
    await expect(shared).toContainText('observation')
    // failed ops badge from stats
    await expect(shared.locator('.mem-badge.err')).toContainText('2')

    const hermes = page.locator('[data-testid="mem-bank-private__hermes"]')
    await expect(hermes).toBeVisible()
  })

  test('timeseries SVG chart renders buckets', async ({ page }) => {
    await gotoMemory(page)
    const chart = page.locator('[data-testid="mem-timeseries"] svg')
    await expect(chart).toBeVisible()
    // three fact-type series rendered as paths
    await expect(chart.locator('path.mem-series')).toHaveCount(3)
  })

  test('failed operation exposes Retry that POSTs the retry endpoint', async ({ page }) => {
    const retries: string[] = []
    await page.route('**/api/memory/banks/shared/operations/op-fail-1/retry', (route: any) => {
      retries.push(route.request().url())
      return json(route, { operation_id: 'op-fail-1', status: 'pending' })
    })

    await gotoMemory(page)
    // open the shared bank's detail (operations live there)
    await page.click('[data-testid="mem-bank-shared"]')
    const opRow = page.locator('[data-testid="mem-op-op-fail-1"]')
    await expect(opRow).toBeVisible()
    await opRow.locator('[data-testid="mem-op-retry"]').click()
    await expect.poll(() => retries.length).toBeGreaterThan(0)
  })

  test('create bank PUTs /api/memory/banks/{name}', async ({ page }) => {
    const puts: { url: string; body: any }[] = []
    await page.route('**/api/memory/banks/scratch-pad', (route: any) => {
      if (route.request().method() === 'PUT') {
        let body = {}
        try {
          body = JSON.parse(route.request().postData() || '{}')
        } catch {}
        puts.push({ url: route.request().url(), body })
        return json(route, { bank_id: 'scratch-pad' })
      }
      return json(route, {})
    })

    await gotoMemory(page)
    await page.click('[data-testid="mem-btn-new-bank"]')
    await page.fill('[data-testid="mem-input-bank-id"]', 'scratch-pad')
    await page.click('[data-testid="mem-btn-bank-submit"]')
    await expect.poll(() => puts.length).toBeGreaterThan(0)
    expect(puts[0].url).toContain('/api/memory/banks/scratch-pad')
  })
})
