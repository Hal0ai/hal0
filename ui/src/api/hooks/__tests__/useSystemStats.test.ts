// @vitest-environment happy-dom
//
// useSystemStats() -> GET /api/system-stats (5 s poll, no retry). Harness mirrors bridgeHooks.test.tsx.
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
const { useSystemStats } = await import('@/api/hooks/useSystemStats')

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

describe('useSystemStats', () => {
  it('reads /api/system-stats and exposes the per-slot rows', async () => {
    vi.mocked(client.apiGet).mockResolvedValue({
      ts: 't',
      fleet: { gpu_util: 0 },
      slots: [{ slot_id: 'utility', state: 'ready', inflight: 0, kv_used: 83801 }],
    })
    const qc = new QueryClient()
    const probe = mountHook(() => useSystemStats(), qc)
    await flush()
    expect(client.apiGet).toHaveBeenCalledWith('/api/system-stats')
    expect(probe.get().data?.slots[0].kv_used).toBe(83801)
    const query = qc.getQueryCache().find({ queryKey: ['system-stats'] })!
    expect((query.options as { refetchInterval?: unknown }).refetchInterval).toBe(5_000)
    expect(query.options.retry).toBe(false)
    act(() => probe.root.unmount())
  })
})
