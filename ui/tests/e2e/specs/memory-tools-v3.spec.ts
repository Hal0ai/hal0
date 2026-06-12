/**
 * memory-tools-v3 — Playwright coverage for the #memory/tools surface.
 *
 * Covers:
 *   - Recall console: query + budget POSTed to .../recall, results render
 *   - Reflect playground: answer text + based_on counts render
 *   - Documents: list renders, Delete fires DELETE .../documents/{id}
 *   - Mental models: stale badge renders, Refresh POSTs .../refresh
 *   - Directives: create form POSTs .../directives
 */

import { test, expect, json } from '../fixtures/apiMock'

const ENGINE = {
  enabled: true,
  engine: 'hindsight',
  reachable: true,
  version: '0.7.2',
  features: { observations: true },
  banks_total: 1,
}

const BANKS = { banks: [{ bank_id: 'shared', fact_count: 3 }] }

const RECALL_RESULT = {
  results: [
    {
      id: 'r1',
      text: 'HAL 0.5 was re-enabled with Hindsight memory engine.',
      type: 'world',
      entities: ['HAL 0.5'],
      occurred_start: '2026-06-07T09:11:52Z',
      tags: ['platform'],
    },
    {
      id: 'r2',
      text: 'Observation: platform memory is live',
      type: 'observation',
      entities: [],
      occurred_start: null,
      tags: [],
    },
  ],
}

const REFLECT_RESULT = {
  text: 'The platform brain runs on Hindsight; memory was re-enabled in 0.5.',
  based_on: { memories: 4, mental_models: 1, directives: 0 },
}

const DOCUMENTS = {
  items: [
    {
      id: 'doc-1',
      created_at: '2026-06-07T09:11:52Z',
      memory_unit_count: 2,
      tags: ['platform'],
      original_text: 'HAL 0.5 was re-enabled with Hindsight memory engine.',
    },
  ],
  total: 1,
}

const MENTAL_MODELS = {
  items: [
    {
      id: 'mm-1',
      name: 'platform-state',
      source_query: 'what is the current platform state?',
      content: 'Memory engine: Hindsight 0.7.2 …',
      tags: [],
      is_stale: true,
      last_refreshed_at: '2026-06-07T00:00:00Z',
    },
  ],
  total: 1,
}

const DIRECTIVES = { items: [], total: 0 }

async function installToolsMocks(page: any, captured: Record<string, any[]>) {
  await page.route('**/api/memory/engine', (route: any) => json(route, ENGINE))
  await page.route('**/api/memory/banks', (route: any) => json(route, BANKS))
  await page.route('**/api/memory/banks/shared/stats**', (route: any) =>
    json(route, { nodes_by_fact_type: {}, pending_operations: 0, failed_operations: 0 }),
  )
  await page.route('**/api/memory/banks/shared/recall', (route: any) => {
    captured.recall.push(JSON.parse(route.request().postData() || '{}'))
    return json(route, RECALL_RESULT)
  })
  await page.route('**/api/memory/banks/shared/reflect', (route: any) => {
    captured.reflect.push(JSON.parse(route.request().postData() || '{}'))
    return json(route, REFLECT_RESULT)
  })
  await page.route('**/api/memory/banks/shared/documents**', (route: any) => {
    if (route.request().method() === 'DELETE') {
      captured.docDeletes.push(route.request().url())
      return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
    }
    return json(route, DOCUMENTS)
  })
  await page.route('**/api/memory/banks/shared/mental-models**', (route: any) => {
    if (route.request().method() === 'POST' && route.request().url().includes('/refresh')) {
      captured.mmRefresh.push(route.request().url())
      return json(route, { operation_id: 'op-9', status: 'pending' })
    }
    return json(route, MENTAL_MODELS)
  })
  await page.route('**/api/memory/banks/shared/directives**', (route: any) => {
    if (route.request().method() === 'POST') {
      captured.directives.push(JSON.parse(route.request().postData() || '{}'))
      return json(route, { id: 'd-1', name: 'tone', content: 'be terse', is_active: true })
    }
    return json(route, DIRECTIVES)
  })
}

async function gotoTools(page: any) {
  await page.goto('/#memory/tools')
  await page.waitForFunction(() => typeof (window as any).MemoryView === 'function')
  await page.waitForSelector('[data-testid="mem-tools"]', { timeout: 10_000 })
}

function freshCaptured() {
  return { recall: [], reflect: [], docDeletes: [], mmRefresh: [], directives: [] } as Record<
    string,
    any[]
  >
}

test.describe('Memory tools', () => {
  test('recall console POSTs query+budget and renders results', async ({ page }) => {
    const captured = freshCaptured()
    await installToolsMocks(page, captured)
    await gotoTools(page)

    await page.fill('[data-testid="mem-recall-q"]', 'what changed recently')
    await page.selectOption('[data-testid="mem-recall-budget"]', 'high')
    await page.click('[data-testid="mem-recall-run"]')

    const results = page.locator('[data-testid="mem-recall-results"]')
    await expect(results).toContainText('HAL 0.5 was re-enabled')
    await expect(results.locator('.mem-recall-row')).toHaveCount(2)
    expect(captured.recall[0].query).toBe('what changed recently')
    expect(captured.recall[0].budget).toBe('high')
  })

  test('reflect playground renders answer and based_on counts', async ({ page }) => {
    const captured = freshCaptured()
    await installToolsMocks(page, captured)
    await gotoTools(page)

    await page.fill('[data-testid="mem-reflect-q"]', 'summarize the platform state')
    await page.click('[data-testid="mem-reflect-run"]')

    const out = page.locator('[data-testid="mem-reflect-out"]')
    await expect(out).toContainText('platform brain runs on Hindsight')
    await expect(out).toContainText('4 memories')
  })

  test('documents list renders and Delete fires DELETE', async ({ page }) => {
    const captured = freshCaptured()
    await installToolsMocks(page, captured)
    await gotoTools(page)

    const row = page.locator('[data-testid="mem-doc-doc-1"]')
    await expect(row).toBeVisible()
    await row.locator('[data-testid="mem-doc-delete"]').click()
    // confirm step
    await page.click('[data-testid="mem-doc-delete-confirm"]')
    await expect.poll(() => captured.docDeletes.length).toBeGreaterThan(0)
    expect(captured.docDeletes[0]).toContain('/documents/doc-1')
  })

  test('mental model stale badge + Refresh POST', async ({ page }) => {
    const captured = freshCaptured()
    await installToolsMocks(page, captured)
    await gotoTools(page)

    const row = page.locator('[data-testid="mem-mm-mm-1"]')
    await expect(row).toBeVisible()
    await expect(row.locator('.mem-badge.warn')).toContainText('stale')
    await row.locator('[data-testid="mem-mm-refresh"]').click()
    await expect.poll(() => captured.mmRefresh.length).toBeGreaterThan(0)
    expect(captured.mmRefresh[0]).toContain('/mental-models/mm-1/refresh')
  })

  test('directive create POSTs name/content', async ({ page }) => {
    const captured = freshCaptured()
    await installToolsMocks(page, captured)
    await gotoTools(page)

    await page.click('[data-testid="mem-dir-new"]')
    await page.fill('[data-testid="mem-dir-name"]', 'tone')
    await page.fill('[data-testid="mem-dir-content"]', 'be terse')
    await page.click('[data-testid="mem-dir-submit"]')

    await expect.poll(() => captured.directives.length).toBeGreaterThan(0)
    expect(captured.directives[0].name).toBe('tone')
    expect(captured.directives[0].content).toBe('be terse')
  })
})
