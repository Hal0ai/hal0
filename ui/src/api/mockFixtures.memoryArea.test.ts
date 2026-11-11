// The forced mock's Memory-area routes (ADR-0058): every route the new hooks read is
// allow-listed, served by a builder, and shaped so the hooks' own normalizers accept it. The
// builders are called the way `buildMockPayload` calls them: the query-bearing URL, and the
// allow-list row's match.
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ENDPOINTS } from './endpoints'
import {
  knowledgePages,
  normalizeKnowledgeTree,
  normalizeMentalModelHistory,
  summarizeBankOperations,
  type BankOperations,
  type KnowledgePage,
  type MentalModel,
} from './hooks/useHindsight'
import { MOCK_ALLOWLIST } from './mock'
import { MOCK_BUILDERS } from './mockFixtures'

/** Resolve a URL the way mockFetch does: first allow-list row whose pattern matches the path. */
function serve(url: string): { key: string; payload: unknown } {
  const path = url.split('?')[0]
  for (const row of MOCK_ALLOWLIST) {
    const match = path.match(row.re)
    if (match) return { key: row.key, payload: MOCK_BUILDERS[row.key](url, match) }
  }
  throw new Error(`not allow-listed: ${url}`)
}

const operations = (bank: string, query = '') =>
  serve(`${ENDPOINTS.memoryBankOperations(bank)}${query}`).payload as BankOperations

describe('allow-list', () => {
  it.each([
    [ENDPOINTS.memoryBankKnowledgeTree('primary'), 'knowledgeTree'],
    [ENDPOINTS.memoryBankKnowledgePage('primary', 'kb-power-resilience'), 'knowledgePage'],
    [ENDPOINTS.memoryBankMentalModel('primary', 'mm-power-resilience'), 'mentalModel'],
    [ENDPOINTS.memoryBankMentalModelHistory('primary', 'mm-power-resilience'), 'mentalModelHistory'],
    // The list route still wins for the bare collection path.
    [ENDPOINTS.memoryBankMentalModels('primary'), 'bankMentalModels'],
    [ENDPOINTS.memoryBankOperations('primary'), 'bankOperations'],
  ])('%s → %s', (url, key) => {
    expect(serve(url).key).toBe(key)
    expect(MOCK_BUILDERS[key]).toBeTypeOf('function')
  })

  it('matches a bank id that holds ::, a dot and a colon, as the endpoints encode it', () => {
    for (const bank of ['Hal0::Agents', 'coding-agent::ultrareview-1.4', 'hal0:shared']) {
      expect(serve(ENDPOINTS.memoryBankKnowledgeTree(bank)).key).toBe('knowledgeTree')
      expect(serve(ENDPOINTS.memoryBankMentalModelHistory(bank, 'mm-power-resilience')).key).toBe(
        'mentalModelHistory',
      )
    }
  })

  it('has a builder for every row', () => {
    for (const row of MOCK_ALLOWLIST) expect(MOCK_BUILDERS[row.key], row.key).toBeTypeOf('function')
  })
})

describe('mockFetch serves the new routes for GET only', () => {
  const originalFetch = globalThis.fetch
  afterEach(() => {
    globalThis.fetch = originalFetch
    vi.resetModules()
  })

  it('falls back to the fixture for a GET, and never for the PATCH or the refresh POST', async () => {
    globalThis.fetch = vi.fn().mockRejectedValue(new TypeError('network error'))
    const { mockFetch } = await import('./mock')
    const page = ENDPOINTS.memoryBankKnowledgePage('primary', 'kb-power-resilience')

    const res = await mockFetch(page, { method: 'GET' })
    expect(res.status).toBe(200)
    expect(((await res.json()) as KnowledgePage).id).toBe('kb-power-resilience')

    await expect(mockFetch(page, { method: 'PATCH', body: '{}' })).rejects.toThrow('network error')
    await expect(
      mockFetch(ENDPOINTS.memoryBankKnowledgePageRefresh('primary', 'kb-power-resilience'), { method: 'POST' }),
    ).rejects.toThrow('network error')
  })

  it('answers 404 for a page or model that does not exist', async () => {
    globalThis.fetch = vi.fn().mockRejectedValue(new TypeError('network error'))
    const { mockFetch } = await import('./mock')
    expect((await mockFetch(ENDPOINTS.memoryBankKnowledgePage('primary', 'kb-nope'))).status).toBe(404)
    expect((await mockFetch(ENDPOINTS.memoryBankMentalModel('primary', 'mm-nope'))).status).toBe(404)
  })
})

describe('knowledge base fixtures', () => {
  const tree = normalizeKnowledgeTree(serve(ENDPOINTS.memoryBankKnowledgeTree('primary')).payload)
  const pageOf = (id: string) =>
    serve(ENDPOINTS.memoryBankKnowledgePage('primary', id)).payload as KnowledgePage

  it('is a tree of folders and pages in the engine envelope', () => {
    const raw = serve(ENDPOINTS.memoryBankKnowledgeTree('primary')).payload as { roots: unknown[] }
    expect(Array.isArray(raw.roots)).toBe(true)
    expect(tree.roots.map((n) => n.kind)).toEqual(['folder', 'folder'])
    expect(knowledgePages(tree).map((r) => r.node.id)).toEqual([
      'kb-power-resilience',
      'kb-thermal-envelope',
      'kb-operator-style',
    ])
  })

  it('gives every node, folder or page, the thirteen fields the engine sends', () => {
    const raw = serve(ENDPOINTS.memoryBankKnowledgeTree('primary')).payload as {
      roots: Record<string, unknown>[]
    }
    const fields = [
      'children',
      'description',
      'id',
      'is_stale',
      'kind',
      'last_refresh_failed_at',
      'managed',
      'mental_model_id',
      'name',
      'parent_id',
      'tags',
      'timestamp',
      'trigger',
    ]
    const walk = (nodes: Record<string, unknown>[], parent: string | null) => {
      for (const node of nodes) {
        expect(Object.keys(node).sort()).toEqual(fields)
        expect(['page', 'folder']).toContain(node.kind)
        expect(node.parent_id).toBe(parent)
        // When a page was last refreshed is on its mental model, not on the node.
        expect(node).not.toHaveProperty('last_refreshed_at')
        walk(node.children as Record<string, unknown>[], node.id as string)
      }
    }
    walk(raw.roots, null)
    expect(raw.roots.length).toBeGreaterThan(0)
  })

  it('names, on each page node, a mental model the list serves', () => {
    const models = serve(ENDPOINTS.memoryBankMentalModels('primary')).payload as { items: MentalModel[] }
    for (const { node } of knowledgePages(tree)) {
      const model = models.items.find((m) => m.id === node.mental_model_id)
      expect(model, node.id).toBeTruthy()
      expect(model?.last_refreshed_at).toBeTruthy()
    }
  })

  it('marks the stale page, and its failed refresh, on the node itself', () => {
    const node = knowledgePages(tree).find((r) => r.node.id === 'kb-operator-style')!.node
    expect(node.is_stale).toBe(true)
    expect(node.last_refresh_failed_at).toBeTruthy()
    const fresh = knowledgePages(tree).find((r) => r.node.id === 'kb-power-resilience')!.node
    expect(fresh.is_stale).toBe(false)
    expect(fresh.last_refresh_failed_at).toBeNull()
  })

  it('serves every page the tree names', () => {
    for (const { node } of knowledgePages(tree)) {
      const page = pageOf(node.id)
      expect(page.id).toBe(node.id)
      expect(page.name).toBe(node.name)
      expect(page.content).toBeTruthy()
      expect(page.mental_model_id).toBe(node.mental_model_id)
    }
  })

  it('has a fresh page', () => {
    const page = pageOf('kb-power-resilience')
    expect(page.is_stale).toBe(false)
    expect(page.last_refreshed_at).toBeTruthy()
    expect(page.last_refresh_failed_at).toBeNull()
  })

  it('has a stale page whose refresh failed after its last success', () => {
    const page = pageOf('kb-operator-style')
    expect(page.is_stale).toBe(true)
    expect(Date.parse(page.last_refresh_failed_at as string)).toBeGreaterThan(
      Date.parse(page.last_refreshed_at as string),
    )
  })

  it('has a bank with no pages', () => {
    expect(serve(ENDPOINTS.memoryBankKnowledgeTree('empty')).payload).toEqual({ roots: [] })
    expect(serve(ENDPOINTS.memoryBankKnowledgePage('empty', 'kb-power-resilience')).payload).toBeNull()
  })
})

describe('mental model fixtures', () => {
  const list = serve(ENDPOINTS.memoryBankMentalModels('primary')).payload as { items: MentalModel[] }

  it('serves the detail of every listed model, with the list fields unchanged', () => {
    for (const item of list.items) {
      const detail = serve(ENDPOINTS.memoryBankMentalModel('primary', item.id)).payload as MentalModel
      expect(detail).toMatchObject(item)
    }
  })

  it('has a model with history, newest first, as the bare list the engine sends', () => {
    const raw = serve(ENDPOINTS.memoryBankMentalModelHistory('primary', 'mm-power-resilience')).payload
    expect(Array.isArray(raw)).toBe(true)
    const history = normalizeMentalModelHistory(raw)
    expect(history.items).toHaveLength(3)
    const stamps = history.items.map((r) => Date.parse(r.updated_at as string))
    expect([...stamps].sort((a, b) => b - a)).toEqual(stamps)
    for (const revision of history.items) expect(revision.content).toBeTruthy()
  })

  it('has a model with no history, and a model whose refresh failed', () => {
    const none = serve(ENDPOINTS.memoryBankMentalModelHistory('primary', 'mm-thermal-envelope')).payload
    expect(none).toEqual([])
    expect(normalizeMentalModelHistory(none)).toEqual({ items: [] })
    const failed = serve(ENDPOINTS.memoryBankMentalModel('primary', 'mm-operator-style')).payload as MentalModel
    expect(failed.is_stale).toBe(true)
    expect(Date.parse(failed.last_refresh_failed_at as string)).toBeGreaterThan(
      Date.parse(failed.last_refreshed_at as string),
    )
  })
})

// The states the Knowledge tab reads, in the shapes a 1.x engine sends.
describe('knowledge tab fixtures', () => {
  type Model = MentalModel & { is_knowledge_page?: boolean; trigger?: Record<string, unknown> }
  const models = (bank: string) =>
    (serve(ENDPOINTS.memoryBankMentalModels(bank)).payload as { items: Model[]; total: number }).items
  const tree = normalizeKnowledgeTree(serve(ENDPOINTS.memoryBankKnowledgeTree('primary')).payload)

  it('marks which models are knowledge pages, and the tree names exactly those', () => {
    const pageModels = models('primary').filter((m) => m.is_knowledge_page).map((m) => m.id)
    expect(pageModels.sort()).toEqual(knowledgePages(tree).map((r) => r.node.mental_model_id).sort())
    for (const m of models('primary')) expect(typeof m.is_knowledge_page, m.id).toBe('boolean')
  })

  it('has mental models that are not pages: one fresh, one stale', () => {
    const plain = models('primary').filter((m) => !m.is_knowledge_page)
    expect(plain.map((m) => [m.id, m.is_stale])).toEqual([
      ['mm-project-decisions', false],
      ['mm-operator-preferences', true],
    ])
    for (const m of plain) expect(m.trigger, m.id).toBeTruthy()
  })

  it("sends a page's text as `body`, its question as `description`, and the document as `markdown`", () => {
    for (const { node } of knowledgePages(tree)) {
      const page = serve(ENDPOINTS.memoryBankKnowledgePage('primary', node.id)).payload as Record<string, unknown>
      expect(page.type).toBe('knowledge-page')
      expect(page.body, node.id).toBeTruthy()
      expect(page.description).toBe(node.description)
      expect(String(page.markdown).startsWith('---\n')).toBe(true)
      expect(String(page.markdown).endsWith(String(page.body))).toBe(true)
    }
  })

  it('has a history in the engine’s shape: replaced text, a failed refresh, the first write', () => {
    const raw = serve(ENDPOINTS.memoryBankMentalModelHistory('primary', 'mm-project-decisions')).payload as Record<
      string,
      unknown
    >[]
    expect(raw.map((e) => [typeof e.previous_content, e.kind ?? null])).toEqual([
      ['string', null],
      ['object', 'refresh_failed'],
      ['string', null],
      ['object', null],
    ])
    const stamps = raw.map((e) => Date.parse(e.changed_at as string))
    expect([...stamps].sort((a, b) => b - a)).toEqual(stamps)
    expect(raw[1].error_message).toBeTruthy()
  })

  it('says why the stale page’s last refresh failed, at the time the page says it failed', () => {
    const [entry] = serve(ENDPOINTS.memoryBankMentalModelHistory('primary', 'mm-operator-style')).payload as Record<
      string,
      unknown
    >[]
    const node = knowledgePages(tree).find((r) => r.node.id === 'kb-operator-style')!.node
    expect(entry.kind).toBe('refresh_failed')
    expect(entry.changed_at).toBe(node.last_refresh_failed_at)
  })

  it('has a bank with no pages, no mental models and no directives', () => {
    expect(serve(ENDPOINTS.memoryBankKnowledgeTree('empty')).payload).toEqual({ roots: [] })
    expect(serve(ENDPOINTS.memoryBankMentalModels('empty')).payload).toEqual({ items: [], total: 0 })
    expect(serve(ENDPOINTS.memoryBankDirectives('empty')).payload).toEqual({ items: [], total: 0 })
    // Every other bank still has them.
    expect(models('scratch').length).toBeGreaterThan(0)
    expect((serve(ENDPOINTS.memoryBankDirectives('scratch')).payload as { items: unknown[] }).items.length).toBe(4)
  })
})

describe('operations fixtures', () => {
  it('keeps the unparameterised payloads the existing surfaces read', () => {
    expect(operations('primary').operations.map((o) => o.id)).toEqual(['op-7741', 'op-7742', 'op-7710'])
    expect(operations('ingest').operations.map((o) => o.id)).toEqual([
      'op-ing-9012',
      'op-ing-9011',
      'op-ing-8990',
      'op-ing-8975',
    ])
    expect(operations('primary')).toMatchObject({ bank_id: 'primary', total: 3, limit: 50, offset: 0 })
  })

  it('gives every row the sixteen fields the engine sends', () => {
    const fields = [
      'created_at',
      'details',
      'document_id',
      'error_message',
      'filename',
      'id',
      'items_count',
      'mental_model_id',
      'next_retry_at',
      'operation_id',
      'operation_type',
      'progress',
      'retry_count',
      'status',
      'task_type',
      'updated_at',
    ]
    for (const bank of ['primary', 'ingest', 'big']) {
      const rows = operations(bank, '?limit=500').operations as unknown as Record<string, unknown>[]
      expect(rows.length).toBeGreaterThan(0)
      for (const row of rows) {
        expect(Object.keys(row).sort()).toEqual(fields)
        expect(row.operation_id).toBe(row.id)
        expect(row.operation_type).toBe(row.task_type)
        expect(Date.parse(row.updated_at as string)).toBeGreaterThanOrEqual(Date.parse(row.created_at as string))
      }
    }
  })

  it('has waiting, running, failed and done rows, and the failed one says why', () => {
    const ops = operations('ingest')
    expect(summarizeBankOperations(ops)).toMatchObject({ pending: 1, processing: 1, failed: 1, completed: 1 })
    const failed = ops.operations.find((o) => o.status === 'failed')!
    expect(failed.error_message).toBe('embedding backend timed out after 30s')
    for (const op of ops.operations.filter((o) => o.status !== 'failed')) expect(op.error_message).toBeNull()
  })

  it('has an empty list', () => {
    expect(operations('empty')).toEqual({ bank_id: 'empty', total: 0, limit: 50, offset: 0, operations: [] })
  })

  it('filters by status and counts the matches', () => {
    const failed = operations('big', '?status=failed')
    expect(failed.total).toBe(9)
    expect(failed.operations).toHaveLength(9)
    for (const op of failed.operations) {
      expect(op.status).toBe('failed')
      expect(op.error_message).toBeTruthy()
    }
    expect(new Set(failed.operations.map((o) => o.error_message)).size).toBe(2)
    // A failed refresh names the model it was rebuilding.
    const refreshes = failed.operations.filter((o) => o.task_type === 'refresh_mental_model')
    expect(refreshes).toHaveLength(2)
    for (const op of refreshes) expect(op.mental_model_id).toBe('mm-operator-style')

    expect(operations('big', '?status=pending').total).toBe(96)
    expect(operations('big', '?status=processing').total).toBe(2)
    expect(operations('big', '?status=completed').total).toBe(30)
    expect(operations('big', '?status=cancelled')).toMatchObject({ total: 0, operations: [] })
  })

  it('pages with limit and offset, without overlap or gap', () => {
    const all = operations('big', '?limit=500')
    expect(all.total).toBe(137)
    expect(all.operations).toHaveLength(137)

    const first = operations('big', '?limit=50&offset=0')
    const second = operations('big', '?limit=50&offset=50')
    const third = operations('big', '?limit=50&offset=100')
    expect(first).toMatchObject({ total: 137, limit: 50, offset: 0 })
    expect(second).toMatchObject({ total: 137, limit: 50, offset: 50 })
    expect([first, second, third].map((p) => p.operations.length)).toEqual([50, 50, 37])
    expect([...first.operations, ...second.operations, ...third.operations].map((o) => o.id)).toEqual(
      all.operations.map((o) => o.id),
    )
    expect(operations('big', '?limit=50&offset=150').operations).toEqual([])
  })

  it('lists newest first with unique ids', () => {
    const all = operations('big', '?limit=500').operations
    expect(new Set(all.map((o) => o.id)).size).toBe(all.length)
    const stamps = all.map((o) => Date.parse(o.created_at))
    expect([...stamps].sort((a, b) => b - a)).toEqual(stamps)
  })

  it('pages within a status filter', () => {
    const page = operations('big', '?status=pending&limit=40&offset=80')
    expect(page).toMatchObject({ total: 96, limit: 40, offset: 80 })
    expect(page.operations).toHaveLength(16)
  })
})
