// @vitest-environment happy-dom
//
// Models → detail panel: "Load now" with several compatible slots opens an inline slot picker,
// and that picker belongs to the model it was opened for. ModelDetail is a window-globals module.
import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

;(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
;(globalThis as unknown as { React: typeof React }).React = React
;(globalThis as unknown as { Icons: unknown }).Icons = new Proxy({}, { get: () => null })
for (const name of ['UsedByPanel', 'OnDiskPanel', 'AddByHfModal', 'AddByPathModal', 'ScanDirectoryModal', 'ModelDrawer', 'DeleteModelDialog']) {
  ;(globalThis as Record<string, unknown>)[name] = () => null
}

const { ModelDetail } = (await import('../models.jsx').then(() => window)) as unknown as {
  ModelDetail: React.ComponentType<{ model: unknown; state?: string }>
}

const llmA = { id: 'llm-a', type: 'llm', installed: true }
const llmB = { id: 'llm-b', type: 'llm', installed: true }
const embed = { id: 'embed-1', type: 'embedding', installed: true }

const slots = [
  { name: 'agent', type: 'llm' },
  { name: 'brain', type: 'llm', model_id: 'other' },
  { name: 'embedder', type: 'embedding' },
  { name: 'embedder2', type: 'embedding' },
]

let swaps: unknown[]
const toast = vi.fn()

beforeEach(() => {
  swaps = []
  toast.mockClear()
  ;(window as unknown as { __hal0Toast: unknown }).__hal0Toast = toast
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(typeof input === 'string' ? input : input instanceof URL ? input.href : input.url, 'http://x')
      let body: unknown = {}
      if (init?.method === 'POST' || init?.method === 'PUT') swaps.push([url.pathname, init.body])
      else if (url.pathname === '/api/slots') body = { slots }
      else if (url.pathname === '/api/models') body = { models: [], count: 0 }
      return new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } })
    }),
  )
})

let live: { unmount: () => void; host: HTMLElement } | null = null
afterEach(() => {
  act(() => live?.unmount())
  live?.host.remove()
  live = null
})

async function mount(model: unknown) {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const root = createRoot(host)
  const ui = (m: unknown) =>
    React.createElement(QueryClientProvider, { client: qc }, React.createElement(ModelDetail, { model: m }))
  await act(async () => root.render(ui(model)))
  await act(async () => { await new Promise((r) => setTimeout(r, 50)) })
  live = { unmount: () => root.unmount(), host }
  return { host, rerender: (m: unknown) => act(async () => root.render(ui(m))) }
}

const loadNow = (host: HTMLElement) =>
  Array.from(host.querySelectorAll('button')).find((b) => b.textContent === 'Load now') as HTMLElement
const picker = (host: HTMLElement) => host.querySelector('[data-testid="mdl-load-picker"]')

describe('Load now slot picker', () => {
  it('opens a picker preselecting the first empty slot', async () => {
    const { host } = await mount(llmA)
    await act(async () => loadNow(host).click())
    expect(picker(host)).not.toBeNull()
    const sel = host.querySelector('[data-testid="mdl-load-picker-select"]') as HTMLSelectElement
    expect(sel.value).toBe('agent')
    expect(toast).not.toHaveBeenCalled()
  })

  it('drops the picker when the selected model changes', async () => {
    const { host, rerender } = await mount(llmA)
    await act(async () => loadNow(host).click())
    expect(picker(host)).not.toBeNull()
    await rerender(embed)
    expect(picker(host)).toBeNull()
    // back on the first model the stale picker does not come back either
    await rerender(llmA)
    expect(picker(host)).toBeNull()
  })

  it('does not carry a picker from one model onto another of the same type', async () => {
    const { host, rerender } = await mount(llmA)
    await act(async () => loadNow(host).click())
    await rerender(llmB)
    expect(picker(host)).toBeNull()
  })
})
