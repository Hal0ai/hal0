// @vitest-environment happy-dom
//
// OverviewTab component tests: the "LLM requests by operation" card must ask
// the engine for the bank-scoped stats route (the only one Hindsight 1.2.0
// exposes, and the only one hal0's engine passthrough allowlists) and render
// the per-operation rows the upstream buckets/series payload projects to.
//
// Same harness as AgentsModelsTabs.test.tsx: mount with createRoot under a
// QueryClientProvider, mock apiGet at the client boundary.
import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

;(globalThis as unknown as { window: typeof globalThis }).window = globalThis
;(globalThis as unknown as { React: typeof React }).React = React
;(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

vi.mock('@/api/client', async () => {
  const actual = await vi.importActual<typeof import('@/api/client')>('@/api/client')
  return { ...actual, apiGet: vi.fn(), apiPost: vi.fn(), apiPatch: vi.fn(), apiDelete: vi.fn() }
})

const client = await import('@/api/client')
const { OverviewTab } = await import('../OverviewTab')

async function mount(node: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const host = document.createElement('div')
  document.body.appendChild(host)
  const root = createRoot(host)
  await act(async () => {
    root.render(React.createElement(QueryClientProvider, { client: qc }, node))
  })
  for (let i = 0; i < 5; i++) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0))
    })
  }
  return { host, root, qc }
}

const LANES = { decision: { health: 'ok', device: 'npu0', requests: 1, busy_s: 0 }, llm: {}, banks: [], npu: null }

const STATS_PAYLOAD = {
  bank_id: 'shared',
  period: '7d',
  trunc: 'day',
  start: '2026-11-01T00:00:00',
  buckets: [
    { time: '2026-11-01T00:00:00', series: { retain: { calls: 2, tokens: 30 }, reflect: { calls: 1, tokens: 5 } } },
    { time: '2026-11-02T00:00:00', series: { retain: { calls: 3, tokens: 40 } } },
  ],
  retention_days: 7,
}

describe('OverviewTab — LLM requests by operation', () => {
  beforeEach(() => {
    vi.mocked(client.apiGet).mockReset()
  })
  afterEach(() => {
    document.body.innerHTML = ''
  })

  it('requests the bank-scoped llm-requests/stats route with split_by=operation', async () => {
    vi.mocked(client.apiGet).mockImplementation(async (url: string) => {
      if (url.includes('/lanes')) return LANES
      if (url.includes('/llm-requests/stats')) return STATS_PAYLOAD
      return {}
    })
    await mount(React.createElement(OverviewTab, { bank: 'shared' }))
    const urls = vi.mocked(client.apiGet).mock.calls.map((c) => c[0])
    expect(urls).toContain(
      '/api/memory/engine/v1/default/banks/shared/llm-requests/stats?split_by=operation',
    )
    // The non-bank route does not exist upstream and is not allowlisted.
    expect(urls.some((u) => String(u).includes('/v1/default/llm-requests/stats'))).toBe(false)
  })

  it('renders one row per operation with the calls summed across buckets', async () => {
    vi.mocked(client.apiGet).mockImplementation(async (url: string) => {
      if (url.includes('/lanes')) return LANES
      if (url.includes('/llm-requests/stats')) return STATS_PAYLOAD
      return {}
    })
    const { host } = await mount(React.createElement(OverviewTab, { bank: 'shared' }))
    expect(host.querySelector('[data-testid="memws-error"]')).toBeNull()
    const retain = host.querySelector('[data-testid="memws-llm-op-row-retain"]')
    expect(retain?.textContent).toContain('retain')
    expect(retain?.textContent).toContain('5')
    const reflect = host.querySelector('[data-testid="memws-llm-op-row-reflect"]')
    expect(reflect?.textContent).toContain('reflect')
    expect(reflect?.textContent).toContain('1')
  })

  it('shows an engine error when the stats call fails', async () => {
    vi.mocked(client.apiGet).mockImplementation(async (url: string) => {
      if (url.includes('/lanes')) return LANES
      throw new Error('boom')
    })
    const { host } = await mount(React.createElement(OverviewTab, { bank: 'shared' }))
    expect(host.querySelector('[data-testid="memws-error"]')).toBeTruthy()
  })
})
