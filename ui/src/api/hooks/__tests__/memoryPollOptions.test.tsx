// @vitest-environment happy-dom
//
// Polling options of the Memory hooks: the graph-status cadence and its single shared query, and
// the documents read that is only enabled when asked. Hidden-tab pausing is TanStack Query's
// default (`refetchIntervalInBackground` is off) and the client does not override it.
import React, { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'

;(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const { calls } = vi.hoisted(() => ({ calls: [] as string[] }))

vi.mock('@/api/client', async () => {
  const actual = await vi.importActual<typeof import('@/api/client')>('@/api/client')
  return {
    ...actual,
    apiGet: (url: string) => {
      calls.push(url)
      return Promise.resolve({ in_flight: 2, items: [], total: 0 })
    },
  }
})

const memory = await import('@/api/hooks/useMemory')
const hindsight = await import('@/api/hooks/useHindsight')
const { queryClient } = await import('@/lib/queryClient')

let root: Root | null = null
afterEach(() => {
  act(() => root?.unmount())
  root = null
  calls.length = 0
})

function mount(qc: QueryClient, ui: () => React.ReactElement) {
  const host = document.createElement('div')
  root = createRoot(host)
  act(() => {
    root!.render(React.createElement(QueryClientProvider, { client: qc }, ui()))
  })
}

describe('memory poll options', () => {
  it('polls graph status every 5 s while extracting, from one shared query', async () => {
    const qc = new QueryClient()
    function Probe() {
      memory.useMemoryGraphStatus()
      memory.useMemoryGraphStatus()
      return null
    }
    mount(qc, () => React.createElement(Probe))
    await act(async () => {
      await Promise.resolve()
    })
    const queries = qc.getQueryCache().findAll({ queryKey: ['memory', 'graph', 'status'] })
    expect(queries).toHaveLength(1)
    expect(calls.filter((u) => u.includes('graph'))).toHaveLength(1)
    const interval = queries[0].observers[0].options.refetchInterval as (q: unknown) => number
    const withState = (in_flight: number) => ({ state: { data: { in_flight } } })
    expect(interval(withState(3))).toBe(memory.GRAPH_IN_FLIGHT_POLL_MS)
    expect(memory.GRAPH_IN_FLIGHT_POLL_MS).toBe(5_000)
    expect(interval(withState(0))).toBe(15_000)
  })

  it('does not poll in a hidden tab', () => {
    expect(queryClient.getDefaultOptions().queries?.refetchIntervalInBackground).toBeFalsy()
  })

  it('reads documents with a limit and not at all while disabled', async () => {
    const qc = new QueryClient()
    function Probe() {
      hindsight.useBankDocuments('b', { limit: 50, enabled: false })
      return null
    }
    mount(qc, () => React.createElement(Probe))
    await act(async () => {
      await Promise.resolve()
    })
    expect(calls).toEqual([])
    act(() => root?.unmount())
    function On() {
      hindsight.useBankDocuments('b', { limit: 50, enabled: true })
      return null
    }
    mount(qc, () => React.createElement(On))
    await act(async () => {
      await Promise.resolve()
    })
    expect(calls).toHaveLength(1)
    expect(calls[0]).toContain('limit=50')
  })
})
