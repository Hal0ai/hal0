// @vitest-environment happy-dom
//
// The two hooks the Knowledge tab adds: the mental-model edit a knowledge page is changed
// through, and the polling that follows a refresh to its end. Both are checked against the real
// query hooks, so what is refetched is what would be on screen.
//
// Harness as in memoryAreaHooks.test.tsx: a probe component under a QueryClient, the api client
// mocked at its boundary.
import React, { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

;(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

type Call = { method: string; url: string; body?: unknown }

const { calls } = vi.hoisted(() => ({ calls: [] as { method: string; url: string; body?: unknown }[] }))

vi.mock('@/api/client', async () => {
  const actual = await vi.importActual<typeof import('@/api/client')>('@/api/client')
  const record = (method: string) => (url: string, body?: unknown) => {
    calls.push({ method, url, body })
    if (method !== 'GET') return Promise.resolve({})
    return Promise.resolve(url.includes('mental-models') ? { items: [], total: 0 } : { roots: [] })
  }
  return { ...actual, apiGet: record('GET'), apiPost: record('POST'), apiPatch: record('PATCH') }
})

const hindsight = await import('@/api/hooks/useHindsight')

const BANK = 'coding-agent::ultrareview-1.4'
const BANK_PATH = '/api/memory/banks/coding-agent%3A%3Aultrareview-1.4'
const OTHER = 'Hal0::Agents'
const TREE = `${BANK_PATH}/knowledge-base/tree`
const PAGE = `${BANK_PATH}/knowledge-base/pages/kb-1`
const MODELS = `${BANK_PATH}/mental-models?detail=content`
const HISTORY = `${BANK_PATH}/mental-models/mm-1/history`
const OTHER_TREE = `/api/memory/banks/${encodeURIComponent(OTHER)}/knowledge-base/tree`

let root: Root | null = null
let qc: QueryClient

function mountHooks<T, P>(useHooks: (props: P) => T, initial: P) {
  let latest!: T
  function Probe(props: { value: P }) {
    latest = useHooks(props.value)
    return null
  }
  const host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  const render = (value: P) =>
    act(() => {
      root!.render(React.createElement(QueryClientProvider, { client: qc }, React.createElement(Probe, { value })))
    })
  render(initial)
  return { get: () => latest, render }
}

async function wait(ms = 0) {
  for (let i = 0; i < 3; i++) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, ms))
    })
  }
}

const countGets = (url: string) => calls.filter((c: Call) => c.method === 'GET' && c.url === url).length

beforeEach(() => {
  calls.length = 0
  qc = new QueryClient({ defaultOptions: { queries: { retry: false, refetchInterval: false } } })
})
afterEach(() => {
  act(() => root?.unmount())
  root = null
  qc.clear()
  document.body.innerHTML = ''
})

const useKnowledge = () => ({
  tree: hindsight.useKnowledgeTree(BANK),
  page: hindsight.useKnowledgePage(BANK, 'kb-1'),
  models: hindsight.useMentalModels(BANK),
  history: hindsight.useMentalModelHistory(BANK, 'mm-1'),
  otherTree: hindsight.useKnowledgeTree(OTHER),
})

describe('useMentalModelUpdate', () => {
  it('sends a PATCH to the model, and refetches the models and the knowledge of that bank', async () => {
    const { get } = mountHooks(() => ({ ...useKnowledge(), update: hindsight.useMentalModelUpdate() }), null)
    await wait()
    expect([TREE, PAGE, MODELS, HISTORY, OTHER_TREE].map(countGets)).toEqual([1, 1, 1, 1, 1])

    await act(async () => {
      await get().update.mutateAsync({ bank: BANK, id: 'mm-1', body: { source_query: 'what changed?', tags: ['a'] } })
    })
    await wait()

    expect(calls.filter((c: Call) => c.method !== 'GET')).toEqual([
      { method: 'PATCH', url: `${BANK_PATH}/mental-models/mm-1`, body: { source_query: 'what changed?', tags: ['a'] } },
    ])
    // The page is written from the model, so the tree and the open page follow the model list.
    expect([TREE, PAGE, MODELS, HISTORY].map(countGets)).toEqual([2, 2, 2, 2])
    expect(countGets(OTHER_TREE)).toBe(1)
  })
})

describe('useKnowledgePolling', () => {
  const usePolled = (active: boolean) => ({ ...useKnowledge(), round: hindsight.useKnowledgePolling(BANK, active, 25) })

  it('does nothing while it is not active', async () => {
    const { get } = mountHooks(usePolled, false)
    await wait(40)
    expect([TREE, PAGE, MODELS].map(countGets)).toEqual([1, 1, 1])
    expect(get().round).toBe(0)
  })

  it('refetches the tree, the open page, the models and their history while active, for that bank only', async () => {
    const { get } = mountHooks(usePolled, true)
    await wait(40)
    for (const url of [TREE, PAGE, MODELS, HISTORY]) expect(countGets(url), url).toBeGreaterThan(1)
    expect(countGets(OTHER_TREE)).toBe(1)
    expect(get().round).toBeGreaterThan(0)
  })

  it('runs one last round when it stops, then nothing more', async () => {
    const { render } = mountHooks<ReturnType<typeof usePolled>, boolean>(usePolled, true)
    await wait(40)
    const before = [TREE, PAGE, MODELS].map(countGets)
    render(false)
    await wait(5)
    // The caller stops on the first list that shows the refresh ended; the rest catch up here.
    const after = [TREE, PAGE, MODELS].map(countGets)
    expect(after).toEqual(before.map((n) => n + 1))
    await wait(40)
    expect([TREE, PAGE, MODELS].map(countGets)).toEqual(after)
  })

  it('does nothing without a bank', async () => {
    mountHooks(() => hindsight.useKnowledgePolling(null, true, 10), null)
    await wait(30)
    expect(calls).toEqual([])
  })
})
