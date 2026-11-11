// @vitest-environment happy-dom
//
// The Memory area's data layer (ADR-0058): the classified knowledge-base and mental-model routes,
// paged operations, and what a mutation invalidates.
//
// The invalidation tests mount the real query hooks, so the cache entries are the ones the hooks
// themselves create, and then check that a mutation makes those hooks fetch again. A test that
// seeded the cache with hand-written keys would pass even when a mutation invalidates a prefix
// no query uses, which is the bug the old workspace had: its mutations invalidated
// `['memory', 'engine', sub]` entries whose `sub` started with `knowledge-base`, while every
// real `sub` started with `v1/default/banks/`, so nothing was ever refetched.
//
// Harness as in pullInvalidatesCapabilities.test.tsx: no testing library, a probe component
// under a QueryClient, the api client mocked at its boundary.
import React, { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

;(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

type Call = { method: string; url: string; body?: unknown }

const { calls, responses } = vi.hoisted(() => ({
  calls: [] as { method: string; url: string; body?: unknown }[],
  responses: { get: (_url: string): unknown => ({}) },
}))

vi.mock('@/api/client', async () => {
  const actual = await vi.importActual<typeof import('@/api/client')>('@/api/client')
  const record = (method: string) => (url: string, body?: unknown) => {
    calls.push({ method, url, body })
    return Promise.resolve(method === 'GET' ? responses.get(url) : { operation_id: 'op-1' })
  }
  return {
    ...actual,
    apiGet: record('GET'),
    apiPost: record('POST'),
    apiPatch: record('PATCH'),
  }
})

const { ENDPOINTS } = await import('@/api/endpoints')
const hindsight = await import('@/api/hooks/useHindsight')
const memory = await import('@/api/hooks/useMemory')
const barrel = await import('@/api/hooks')

// A bank id as the coding-agent plugin writes it: `::`, a dot and a dash.
const BANK = 'coding-agent::ultrareview-1.4'
const BANK_PATH = '/api/memory/banks/coding-agent%3A%3Aultrareview-1.4'
const OTHER = 'Hal0::Agents'

let root: Root | null = null
let qc: QueryClient

/** Mount a component that calls `useHooks` on every render, and hand back its latest result. */
function mountHooks<T>(useHooks: () => T) {
  let latest!: T
  function Probe() {
    latest = useHooks()
    return null
  }
  const host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  act(() => {
    root!.render(React.createElement(QueryClientProvider, { client: qc }, React.createElement(Probe)))
  })
  return () => latest
}

/** Let queued fetches resolve and their renders commit. */
async function settle() {
  for (let i = 0; i < 3; i++) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0))
    })
  }
}

const gets = () => calls.filter((c: Call) => c.method === 'GET').map((c: Call) => c.url)
const countGets = (url: string) => gets().filter((u: string) => u === url).length

beforeEach(() => {
  calls.length = 0
  responses.get = () => ({})
  qc = new QueryClient({ defaultOptions: { queries: { retry: false, refetchInterval: false } } })
})

afterEach(() => {
  act(() => root?.unmount())
  root = null
  qc.clear()
  document.body.innerHTML = ''
})

describe('endpoints: classified bank routes, path segments encoded', () => {
  it.each([
    ['memoryBankKnowledgeTree', [BANK], `${BANK_PATH}/knowledge-base/tree`],
    ['memoryBankKnowledgePage', [BANK, 'kb-1'], `${BANK_PATH}/knowledge-base/pages/kb-1`],
    ['memoryBankMentalModel', [BANK, 'mm-1'], `${BANK_PATH}/mental-models/mm-1`],
    ['memoryBankMentalModelHistory', [BANK, 'mm-1'], `${BANK_PATH}/mental-models/mm-1/history`],
  ] as const)('%s', (name, args, expected) => {
    const build = ENDPOINTS[name] as (...a: string[]) => string
    expect(build(...args)).toBe(expected)
  })

  it('never routes through the engine passthrough', () => {
    for (const url of [
      ENDPOINTS.memoryBankKnowledgeTree(BANK),
      ENDPOINTS.memoryBankKnowledgePage(BANK, 'p'),
      ENDPOINTS.memoryBankMentalModel(BANK, 'm'),
      ENDPOINTS.memoryBankMentalModelHistory(BANK, 'm'),
    ]) {
      expect(url.startsWith('/api/memory/banks/')).toBe(true)
      expect(url).not.toContain('/api/memory/engine')
      expect(url).not.toContain('v1/default')
    }
  })

  it.each([
    ['Hal0::Agents', 'Hal0%3A%3AAgents'],
    ['hal0:shared', 'hal0%3Ashared'],
    ['coding-agent::repo.name-1.4', 'coding-agent%3A%3Arepo.name-1.4'],
    ['plain', 'plain'],
  ])('encodes bank id %s as one segment that decodes back', (bank, segment) => {
    const url = ENDPOINTS.memoryBankKnowledgeTree(bank)
    expect(url).toBe(`/api/memory/banks/${segment}/knowledge-base/tree`)
    expect(decodeURIComponent(url.split('/')[4])).toBe(bank)
    // Same number of path segments whatever the id holds.
    expect(url.split('/')).toHaveLength(7)
  })

  it('cannot be steered to another path by an id', () => {
    const url = ENDPOINTS.memoryBankKnowledgePage('a/b', '../c?x=1#y')
    expect(url).toBe('/api/memory/banks/a%2Fb/knowledge-base/pages/..%2Fc%3Fx%3D1%23y')
    expect(url.split('/')).toHaveLength(8)
    expect(url).not.toContain('?')
    expect(url).not.toContain('#')
  })

  it('encodes the page and model ids too', () => {
    expect(ENDPOINTS.memoryBankKnowledgePage(BANK, 'ns:page.1')).toBe(
      `${BANK_PATH}/knowledge-base/pages/ns%3Apage.1`,
    )
    expect(ENDPOINTS.memoryBankMentalModelHistory(BANK, 'ns:model.1')).toBe(
      `${BANK_PATH}/mental-models/ns%3Amodel.1/history`,
    )
  })
})

describe('query keys', () => {
  it('keeps the raw bank id in the key and nests everything under the bank', () => {
    expect(hindsight.knowledgeKey(BANK)).toEqual(['memory', 'banks', BANK, 'knowledge'])
    expect(hindsight.knowledgeTreeKey(BANK)).toEqual(['memory', 'banks', BANK, 'knowledge', 'tree'])
    expect(hindsight.knowledgePageKey(BANK, 'kb-1')).toEqual(['memory', 'banks', BANK, 'knowledge', 'page', 'kb-1'])
    expect(hindsight.mentalModelKey(BANK, 'mm-1')).toEqual(['memory', 'banks', BANK, 'mental-models', 'mm-1'])
    expect(hindsight.mentalModelHistoryKey(BANK, 'mm-1')).toEqual([
      'memory', 'banks', BANK, 'mental-models', 'mm-1', 'history',
    ])
  })

  it('leaves the operations key of an unpaged read as it always was', () => {
    expect(hindsight.bankOperationsKey(BANK)).toEqual(['memory', 'banks', BANK, 'operations', 'all'])
    expect(hindsight.bankOperationsKey(BANK, { status: 'failed' })).toEqual([
      'memory', 'banks', BANK, 'operations', 'failed',
    ])
  })

  it('gives each page of operations its own key under the same prefix', () => {
    const first = hindsight.bankOperationsKey(BANK, { status: 'failed', limit: 50, offset: 0 })
    const second = hindsight.bankOperationsKey(BANK, { status: 'failed', limit: 50, offset: 50 })
    expect(first).toEqual(['memory', 'banks', BANK, 'operations', 'failed', { limit: 50, offset: 0 }])
    expect(second).not.toEqual(first)
    expect(first.slice(0, 4)).toEqual(['memory', 'banks', BANK, 'operations'])
  })
})

describe('query hooks call the classified routes', () => {
  it('useKnowledgeTree, useKnowledgePage, useMentalModel, useMentalModelHistory', async () => {
    responses.get = (url) =>
      url.endsWith('/tree')
        ? { roots: [{ id: 'kb-1', kind: 'page', name: 'One', children: [] }] }
        : url.endsWith('/history')
          ? [{ updated_at: '2026-06-10T08:00:00Z', content: 'v2' }]
          : { id: 'x', name: 'X' }
    const get = mountHooks(() => ({
      tree: hindsight.useKnowledgeTree(BANK),
      page: hindsight.useKnowledgePage(BANK, 'kb-1'),
      model: hindsight.useMentalModel(BANK, 'mm-1'),
      history: hindsight.useMentalModelHistory(BANK, 'mm-1'),
    }))
    await settle()
    expect(gets().sort()).toEqual(
      [
        `${BANK_PATH}/knowledge-base/tree`,
        `${BANK_PATH}/knowledge-base/pages/kb-1`,
        `${BANK_PATH}/mental-models/mm-1`,
        `${BANK_PATH}/mental-models/mm-1/history`,
      ].sort(),
    )
    expect(get().tree.data).toEqual({
      roots: [
        {
          id: 'kb-1',
          kind: 'page',
          name: 'One',
          parent_id: null,
          children: [],
          description: null,
          tags: [],
          managed: false,
          mental_model_id: null,
          timestamp: null,
          trigger: null,
          is_stale: false,
          last_refresh_failed_at: null,
        },
      ],
    })
    expect(get().page.data).toEqual({ id: 'x', name: 'X' })
    // The engine's bare list is folded into {items}.
    expect(get().history.data).toEqual({ items: [{ updated_at: '2026-06-10T08:00:00Z', content: 'v2' }] })
    // The cache holds exactly the keys the key helpers name.
    expect(qc.getQueryState(hindsight.knowledgeTreeKey(BANK))?.status).toBe('success')
    expect(qc.getQueryState(hindsight.knowledgePageKey(BANK, 'kb-1'))?.status).toBe('success')
    expect(qc.getQueryState(hindsight.mentalModelKey(BANK, 'mm-1'))?.status).toBe('success')
    expect(qc.getQueryState(hindsight.mentalModelHistoryKey(BANK, 'mm-1'))?.status).toBe('success')
  })

  it('fetch nothing until they have a bank and an id', async () => {
    mountHooks(() => ({
      tree: hindsight.useKnowledgeTree(null),
      page: hindsight.useKnowledgePage(BANK, null),
      model: hindsight.useMentalModel(null, 'mm-1'),
      history: hindsight.useMentalModelHistory(BANK, null),
      off: hindsight.useKnowledgeTree(BANK, { enabled: false }),
    }))
    await settle()
    expect(calls).toEqual([])
  })
})

describe('useBankOperations', () => {
  it.each([
    [undefined, ''],
    [{ status: 'failed' }, '?status=failed'],
    [{ limit: 50 }, '?limit=50'],
    [{ status: 'failed', limit: 50, offset: 100 }, '?status=failed&limit=50&offset=100'],
    // A first page is offset 0, and 0 is sent.
    [{ status: 'pending', limit: 25, offset: 0 }, '?status=pending&limit=25&offset=0'],
  ])('opts %j → query string "%s"', async (opts, search) => {
    responses.get = () => ({ total: 0, operations: [] })
    mountHooks(() => hindsight.useBankOperations(BANK, opts))
    await settle()
    expect(gets()).toEqual([`${BANK_PATH}/operations${search}`])
    expect(qc.getQueryState(hindsight.bankOperationsKey(BANK, opts))?.status).toBe('success')
  })

  it('shares one cache entry between two unpaged callers', async () => {
    responses.get = () => ({ total: 0, operations: [] })
    mountHooks(() => [hindsight.useBankOperations(BANK), hindsight.useBankOperations(BANK)])
    await settle()
    expect(gets()).toEqual([`${BANK_PATH}/operations`])
  })

  it('still honours enabled: false', async () => {
    mountHooks(() => hindsight.useBankOperations(BANK, { limit: 50, enabled: false }))
    await settle()
    expect(calls).toEqual([])
  })
})

describe('mutations invalidate the queries that exist', () => {
  const TREE = `${BANK_PATH}/knowledge-base/tree`
  const PAGE = `${BANK_PATH}/knowledge-base/pages/kb-1`
  const OTHER_TREE = `/api/memory/banks/${encodeURIComponent(OTHER)}/knowledge-base/tree`
  const OPS = `${BANK_PATH}/operations`
  const MODELS = `${BANK_PATH}/mental-models?detail=content`

  function mountAll() {
    responses.get = (url) =>
      url.includes('/operations') ? { total: 0, operations: [] } : url.includes('mental-models') ? { items: [], total: 0 } : { roots: [] }
    return mountHooks(() => ({
      tree: hindsight.useKnowledgeTree(BANK),
      page: hindsight.useKnowledgePage(BANK, 'kb-1'),
      otherTree: hindsight.useKnowledgeTree(OTHER),
      ops: hindsight.useBankOperations(BANK),
      models: hindsight.useMentalModels(BANK),
      refreshModel: hindsight.useMentalModelRefresh(),
    }))
  }

  it('no hook or endpoint calls a page refresh or page PATCH route (#3564)', () => {
    // A 1.x engine serves neither; a page is refreshed and edited through its mental model.
    const exported = hindsight as Record<string, unknown>
    expect(exported.useKnowledgePageRefresh).toBeUndefined()
    expect(exported.useKnowledgePageUpdate).toBeUndefined()
    expect((ENDPOINTS as Record<string, unknown>).memoryBankKnowledgePageRefresh).toBeUndefined()
  })

  it('a page is refreshed through its mental model, not a page route', async () => {
    const get = mountAll()
    await settle()
    expect(countGets(MODELS)).toBe(1)
    await act(async () => {
      await get().refreshModel.mutateAsync({ bank: BANK, id: 'mm-1' })
    })
    await settle()
    expect(calls.filter((c: Call) => c.method !== 'GET')).toEqual([
      { method: 'POST', url: `${BANK_PATH}/mental-models/mm-1/refresh`, body: {} },
    ])
    expect(countGets(MODELS)).toBe(2)
  })

  it('a mental-model refresh refetches the detail and history of that model', async () => {
    responses.get = () => ({ items: [], total: 0 })
    const get = mountHooks(() => ({
      model: hindsight.useMentalModel(BANK, 'mm-1'),
      history: hindsight.useMentalModelHistory(BANK, 'mm-1'),
      refresh: hindsight.useMentalModelRefresh(),
    }))
    await settle()
    await act(async () => {
      await get().refresh.mutateAsync({ bank: BANK, id: 'mm-1' })
    })
    await settle()
    expect(countGets(`${BANK_PATH}/mental-models/mm-1`)).toBe(2)
    expect(countGets(`${BANK_PATH}/mental-models/mm-1/history`)).toBe(2)
  })

  it('an operation retry refetches a paged operations list too', async () => {
    responses.get = () => ({ total: 0, operations: [] })
    const get = mountHooks(() => ({
      paged: hindsight.useBankOperations(BANK, { status: 'failed', limit: 50, offset: 50 }),
      retry: hindsight.useOperationRetry(),
    }))
    await settle()
    const url = `${OPS}?status=failed&limit=50&offset=50`
    expect(countGets(url)).toBe(1)
    await act(async () => {
      await get().retry.mutateAsync({ bank: BANK, id: 'op-9' })
    })
    await settle()
    expect(countGets(url)).toBe(2)
  })
})

describe('normalizeKnowledgeTree', () => {
  const page = (id: string, extra: Record<string, unknown> = {}) => ({ id, kind: 'page', name: id, children: [], ...extra })
  // What a node holds for each field the answer leaves out.
  const NODE_DEFAULTS = {
    parent_id: null,
    description: null,
    tags: [],
    managed: false,
    mental_model_id: null,
    timestamp: null,
    trigger: null,
    is_stale: false,
    last_refresh_failed_at: null,
  }

  it('keeps every field of a node as the engine sends it', () => {
    const node = {
      id: 'kb-operator-style',
      kind: 'page',
      name: 'Operator interaction style',
      parent_id: 'kbf-operator',
      children: [],
      description: 'How to talk to the operator',
      tags: ['operator'],
      managed: true,
      mental_model_id: 'mm-operator-style',
      timestamp: '2026-05-28T23:00:00.000Z',
      trigger: 'after_consolidation',
      is_stale: true,
      last_refresh_failed_at: '2026-06-11T08:00:00.000Z',
    }
    expect(hindsight.normalizeKnowledgeTree({ roots: [node] })).toEqual({ roots: [node] })
  })

  it('has no last_refreshed_at on a node: that is read from the mental-models list', () => {
    const tree = hindsight.normalizeKnowledgeTree({
      roots: [page('a', { mental_model_id: 'mm-1', last_refreshed_at: '2026-06-10T08:00:00.000Z' })],
    })
    expect(tree.roots[0]).not.toHaveProperty('last_refreshed_at')
    expect(tree.roots[0].mental_model_id).toBe('mm-1')
  })

  it('reads a wrong-typed field as its empty value', () => {
    const tree = hindsight.normalizeKnowledgeTree({
      roots: [page('a', { tags: ['x', 7, null], managed: 'yes', is_stale: 1, parent_id: 4, trigger: '' })],
    })
    expect(tree.roots[0]).toMatchObject({ tags: ['x'], managed: false, is_stale: false, parent_id: null, trigger: null })
  })

  it.each([
    ['the engine envelope', { roots: [page('a')] }],
    ['nodes', { nodes: [page('a')] }],
    ['children', { children: [page('a')] }],
    ['items', { items: [page('a')] }],
    ['a bare array', [page('a')]],
  ])('reads %s', (_name, raw) => {
    expect(hindsight.normalizeKnowledgeTree(raw).roots.map((n) => n.id)).toEqual(['a'])
  })

  it('folds type into kind and page_id into id, and keeps nesting', () => {
    const tree = hindsight.normalizeKnowledgeTree({
      roots: [
        {
          id: 'f1',
          type: 'folder',
          name: 'Folder',
          children: [{ page_id: 'p1', type: 'page', name: 'Page', mental_model_id: 'mm-1' }, { name: 'no id' }, null],
        },
      ],
    })
    expect(tree).toEqual({
      roots: [
        {
          ...NODE_DEFAULTS,
          id: 'f1',
          kind: 'folder',
          name: 'Folder',
          children: [
            { ...NODE_DEFAULTS, id: 'p1', kind: 'page', name: 'Page', children: [], mental_model_id: 'mm-1' },
          ],
        },
      ],
    })
  })

  it.each([[null], [undefined], ['nope'], [42], [{}], [{ roots: 'nope' }]])('answers an empty tree for %j', (raw) => {
    expect(hindsight.normalizeKnowledgeTree(raw)).toEqual({ roots: [] })
  })

  it('lists the pages in reading order with their depth', () => {
    const tree = hindsight.normalizeKnowledgeTree({
      roots: [
        { id: 'f1', kind: 'folder', name: 'F1', children: [page('a'), { id: 'f2', kind: 'folder', name: 'F2', children: [page('b')] }] },
        page('c'),
      ],
    })
    expect(hindsight.knowledgePages(tree).map((r) => [r.node.id, r.depth])).toEqual([
      ['a', 1],
      ['b', 2],
      ['c', 0],
    ])
    expect(hindsight.knowledgePages(null)).toEqual([])
  })
})

describe('normalizeMentalModelHistory', () => {
  it.each([
    [[{ content: 'v1' }], [{ content: 'v1' }]],
    [{ items: [{ content: 'v1' }] }, [{ content: 'v1' }]],
    [{ items: 'nope' }, []],
    [{}, []],
    [null, []],
    ['nope', []],
  ])('%j', (raw, items) => {
    expect(hindsight.normalizeMentalModelHistory(raw)).toEqual({ items })
  })
})

describe('the agents and models hooks', () => {
  it('are one function each, reachable from useMemory and the barrel', () => {
    expect(barrel.useMemoryModels).toBe(memory.useMemoryModels)
    expect(barrel.useMemoryWorkspaceAgents).toBe(memory.useMemoryWorkspaceAgents)
  })

  it('keep their routes and query keys', async () => {
    mountHooks(() => ({ models: memory.useMemoryModels(), agents: memory.useMemoryWorkspaceAgents() }))
    await settle()
    expect(gets().sort()).toEqual(['/api/memory/agents', '/api/memory/models'])
    expect(qc.getQueryState(['memory', 'models'])?.status).toBe('success')
    expect(qc.getQueryState(['memory', 'agents'])?.status).toBe('success')
  })
})
