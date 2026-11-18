// @vitest-environment happy-dom
//
// Per-model provider hooks:
//   useUpstreamModels(name)    -> GET   /api/upstreams/{name}/models      ['upstream-models', name]
//   useUpstreamModelPatch()    -> PATCH /api/upstreams/{name}/models/{id} optimistic, rolls back on error
//   useUpstreamRefresh()       -> POST  /api/upstreams/{name}/refresh     writes ['upstream-models', name]
//   useProviderProbe()         -> POST  /api/providers/probe              never cached; reset() clears the key
import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { UpstreamListing, UpstreamModelRow } from '@/api/hooks/useUpstreams'

;(globalThis as unknown as { window: typeof globalThis }).window = globalThis
;(globalThis as unknown as { React: typeof React }).React = React
;(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

vi.mock('@/api/client', async () => {
  const actual = await vi.importActual<typeof import('@/api/client')>('@/api/client')
  return { ...actual, apiGet: vi.fn(), apiPost: vi.fn(), apiPatch: vi.fn(), apiDelete: vi.fn() }
})

const client = await import('@/api/client')
const { ENDPOINTS } = await import('@/api/endpoints')
const hooks = await import('@/api/hooks/useUpstreams')

const row = (id: string, enabled = false): UpstreamModelRow => ({
  id,
  canonical_id: `p/${id}`,
  enabled,
  type: 'chat',
  context: null,
  price_in: null,
  price_out: null,
  price_unit: 'Mtok',
  discovered: true,
  recommended: false,
  in_use_by: [],
})
const LISTING: UpstreamListing = {
  name: 'p',
  curated: true,
  protected: false,
  fetched_at: 1,
  stale: false,
  warning: null,
  models: [row('qwen/qwen3-coder'), row('b', true)],
}

// react-query batches observer notifications on a timer; wait one macrotask for a render.
const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 5)) })

let qc: QueryClient
let root: ReturnType<typeof createRoot> | null = null

function mount<T>(useHook: () => T): { current: () => T } {
  let value!: T
  function Probe() {
    value = useHook()
    return null
  }
  const host = document.createElement('div')
  document.body.appendChild(host)
  const r = createRoot(host)
  root = r
  act(() => {
    r.render(React.createElement(QueryClientProvider, { client: qc }, React.createElement(Probe)))
  })
  return { current: () => value }
}

beforeEach(() => {
  qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  vi.mocked(client.apiGet).mockReset()
  vi.mocked(client.apiPost).mockReset()
  vi.mocked(client.apiPatch).mockReset()
})
afterEach(() => {
  if (root) act(() => root!.unmount())
  root = null
  document.body.innerHTML = ''
})

describe('endpoint builders', () => {
  it('builds the per-model routes', () => {
    expect(ENDPOINTS.providerProbe).toBe('/api/providers/probe')
    expect(ENDPOINTS.upstreamModels('open router')).toBe('/api/upstreams/open%20router/models')
    expect(ENDPOINTS.upstreamModel('p', 'qwen/qwen3-coder')).toBe('/api/upstreams/p/models/qwen/qwen3-coder')
    expect(ENDPOINTS.upstreamModel('p', 'a b/c?d')).toBe('/api/upstreams/p/models/a%20b/c%3Fd')
    expect(ENDPOINTS.upstreamRefresh('p')).toBe('/api/upstreams/p/refresh')
  })
})

describe('useUpstreamModels', () => {
  it('reads the listing under its own key', async () => {
    vi.mocked(client.apiGet).mockResolvedValue(LISTING)
    const h = mount(() => hooks.useUpstreamModels('p'))
    await flush()
    expect(client.apiGet).toHaveBeenCalledWith('/api/upstreams/p/models')
    expect(h.current().data).toEqual(LISTING)
    expect(qc.getQueryData(['upstream-models', 'p'])).toEqual(LISTING)
  })
  it('does not fetch without a name', async () => {
    mount(() => hooks.useUpstreamModels(null))
    await flush()
    expect(client.apiGet).not.toHaveBeenCalled()
  })
})

describe('useUpstreamModelPatch', () => {
  it('patches the cached row at once and keeps it on success', async () => {
    qc.setQueryData(['upstream-models', 'p'], LISTING)
    let resolve!: (r: UpstreamModelRow) => void
    vi.mocked(client.apiPatch).mockReturnValue(new Promise((r) => (resolve = r)))
    vi.mocked(client.apiGet).mockResolvedValue({
      ...LISTING,
      models: [row('qwen/qwen3-coder', true), row('b', true)],
    })
    const h = mount(() => hooks.useUpstreamModelPatch())
    act(() => {
      h.current().mutate({ name: 'p', modelId: 'qwen/qwen3-coder', patch: { enabled: true } })
    })
    await flush()
    expect(client.apiPatch).toHaveBeenCalledWith('/api/upstreams/p/models/qwen/qwen3-coder', { enabled: true })
    const during = qc.getQueryData<UpstreamListing>(['upstream-models', 'p'])!
    expect(during.models.find((m) => m.id === 'qwen/qwen3-coder')!.enabled).toBe(true)
    await act(async () => resolve(row('qwen/qwen3-coder', true)))
    expect(qc.getQueryData<UpstreamListing>(['upstream-models', 'p'])!.models[0].enabled).toBe(true)
  })

  it('rolls the row back when the request is rejected', async () => {
    qc.setQueryData(['upstream-models', 'p'], LISTING)
    // The refetch after settle must not mask the rollback: keep it pending.
    vi.mocked(client.apiGet).mockReturnValue(new Promise(() => {}))
    let reject!: (e: Error) => void
    vi.mocked(client.apiPatch).mockReturnValue(new Promise((_, rej) => (reject = rej)))
    const h = mount(() => hooks.useUpstreamModelPatch())
    act(() => {
      h.current().mutate({ name: 'p', modelId: 'qwen/qwen3-coder', patch: { enabled: true } })
    })
    await flush()
    expect(qc.getQueryData<UpstreamListing>(['upstream-models', 'p'])!.models[0].enabled).toBe(true)
    await act(async () => reject(new Error('conflict')))
    await flush()
    expect(qc.getQueryData<UpstreamListing>(['upstream-models', 'p'])!.models[0].enabled).toBe(false)
    expect(h.current().isError).toBe(true)
  })

  it('invalidates the provider list when it settles', async () => {
    qc.setQueryData(['upstreams'], [])
    qc.setQueryData(['upstream-models', 'p'], LISTING)
    vi.mocked(client.apiGet).mockReturnValue(new Promise(() => {}))
    vi.mocked(client.apiPatch).mockResolvedValue(row('b'))
    const h = mount(() => hooks.useUpstreamModelPatch())
    await act(async () => {
      await h.current().mutateAsync({ name: 'p', modelId: 'b', patch: { enabled: false } })
    })
    expect(qc.getQueryState(['upstreams'])?.isInvalidated).toBe(true)
  })
})

describe('useUpstreamRefresh', () => {
  it('writes the returned listing into the cache', async () => {
    const fresh = { ...LISTING, fetched_at: 2, warning: 'x' }
    vi.mocked(client.apiPost).mockResolvedValue(fresh)
    const h = mount(() => hooks.useUpstreamRefresh())
    await act(async () => {
      await h.current().mutateAsync('p')
    })
    expect(client.apiPost).toHaveBeenCalledWith('/api/upstreams/p/refresh')
    expect(qc.getQueryData(['upstream-models', 'p'])).toEqual(fresh)
  })
})

describe('useProviderProbe', () => {
  it('posts the body and drops the key from mutation state on reset', async () => {
    vi.mocked(client.apiPost).mockResolvedValue({ ok: true, status: 200, latency_ms: 1, models: [], rate_limit: {}, error: null })
    const h = mount(() => hooks.useProviderProbe())
    await act(async () => {
      await h.current().mutateAsync({ url: 'https://x/v1', key: 'sk-secret' })
    })
    expect(client.apiPost).toHaveBeenCalledWith('/api/providers/probe', { url: 'https://x/v1', key: 'sk-secret' })
    await flush()
    expect(h.current().variables).toEqual({ url: 'https://x/v1', key: 'sk-secret' })
    act(() => h.current().reset())
    await flush()
    expect(h.current().variables).toBeUndefined()
    expect(h.current().data).toBeUndefined()
    const cached = qc.getMutationCache().getAll()
    expect(JSON.stringify(cached.map((m) => m.state.variables))).not.toContain('sk-secret')
  })
})
