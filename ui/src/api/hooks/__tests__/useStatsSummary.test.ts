// @vitest-environment happy-dom
//
// useStatsSummary(window) -> GET /api/stats?window=<w> (30 s poll, no retry).
import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

;(globalThis as unknown as { React: typeof React }).React = React
;(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

vi.mock('@/api/client', async () => {
  const actual = await vi.importActual<typeof import('@/api/client')>('@/api/client')
  return { ...actual, apiGet: vi.fn() }
})

const client = await import('@/api/client')
const { useStatsSummary } = await import('@/api/hooks/useStatsSummary')

function mountHook<T>(useHook: () => T, qc: QueryClient) {
  let latest!: T
  function Probe() {
    latest = useHook()
    return null
  }
  const host = document.createElement('div')
  document.body.appendChild(host)
  const root = createRoot(host)
  act(() => {
    root.render(React.createElement(QueryClientProvider, { client: qc }, React.createElement(Probe)))
  })
  return { root, get: () => latest }
}

async function flush() {
  for (let i = 0; i < 5; i++) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0))
    })
  }
}

beforeEach(() => vi.mocked(client.apiGet).mockReset())
afterEach(() => {
  document.body.innerHTML = ''
})

describe('useStatsSummary', () => {
  it('reads /api/stats for the window and passes the data through', async () => {
    vi.mocked(client.apiGet).mockResolvedValue({
      window: { from: 'a', to: 'b', bucket: '1h' },
      totals: { requests: 6, ok: 6, errors: 0, tokens_completed: 1854 },
      by_model: [],
    })
    const qc = new QueryClient()
    const probe = mountHook(() => useStatsSummary('24h'), qc)
    await flush()
    expect(client.apiGet).toHaveBeenCalledWith('/api/stats?window=24h')
    expect(probe.get().data?.totals.tokens_completed).toBe(1854)
    const query = qc.getQueryCache().find({ queryKey: ['stats-summary', '24h'] })!
    expect((query.options as { refetchInterval?: unknown }).refetchInterval).toBe(30_000)
    expect(query.options.retry).toBe(false)
    act(() => probe.root.unmount())
  })
})
