// @vitest-environment happy-dom
//
// Models page wiring for the Providers tab: the tab id and its old address, the count on
// the tab, the header actions, and what "Refresh model lists" asks of which providers.
// Same harness as models-image-count-resilience.test.tsx (models.jsx is a window-globals module).
import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

;(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
;(globalThis as unknown as { React: typeof React }).React = React
;(globalThis as unknown as { Icons: unknown }).Icons = new Proxy({}, { get: () => null })
for (const name of ['AddByHfModal', 'AddByPathModal', 'ScanDirectoryModal', 'ModelDrawer', 'DeleteModelDialog']) {
  ;(globalThis as Record<string, unknown>)[name] = () => null
}

const { ModelsView } = (await import('../models.jsx').then(() => window)) as unknown as {
  ModelsView: React.ComponentType<{ modelParam?: string | null }>
}
const { MOCK_BUILDERS } = await import('@/api/mockFixtures')

const posts: string[] = []
let upstreamOverride: unknown = null
let refreshAnswer: (name: string) => Record<string, unknown> = (name) => ({ name, curated: true, stale: false, warning: null, models: [] })
const toast = vi.fn()

function stubFetch() {
  posts.length = 0
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(typeof input === 'string' ? input : input instanceof URL ? input.href : input.url, 'http://x')
      let body: unknown = {}
      if (init?.method === 'POST') {
        posts.push(url.pathname)
        const name = url.pathname.split('/')[3]
        body = refreshAnswer(name)
      } else if (url.pathname === '/api/upstreams' && upstreamOverride) body = upstreamOverride
      else if (url.pathname === '/api/upstreams') body = MOCK_BUILDERS.upstreams(url.pathname, [] as unknown as RegExpMatchArray)
      else if (url.pathname === '/api/models') body = { models: [], count: 0 }
      else if (url.pathname === '/api/slots') body = { slots: [] }
      return new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } })
    }),
  )
}

async function mount(modelParam: string) {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const root = createRoot(host)
  await act(async () => {
    root.render(React.createElement(QueryClientProvider, { client: qc }, React.createElement(ModelsView, { modelParam })))
  })
  await act(async () => {
    await new Promise((r) => setTimeout(r, 50))
  })
  return { host, unmount: () => act(() => root.unmount()) }
}

const byTestId = (host: HTMLElement, id: string) => host.querySelector(`[data-testid="${id}"]`) as HTMLElement | null

describe('Models → Providers wiring', () => {
  beforeEach(() => {
    upstreamOverride = null
    refreshAnswer = (name) => ({ name, curated: true, stale: false, warning: null, models: [] })
    stubFetch()
    ;(window as unknown as { __hal0Toast: unknown }).__hal0Toast = toast
    toast.mockClear()
  })
  afterEach(() => {
    vi.unstubAllGlobals()
    document.body.innerHTML = ''
  })

  it('shows the tab with the list and the detail panel, and counts the providers', async () => {
    const { host, unmount } = await mount('providers')
    expect(byTestId(host, 'providers-tab')).not.toBeNull()
    expect(byTestId(host, 'provider-detail')).not.toBeNull()
    expect(byTestId(host, 'models-tab-providers')?.getAttribute('aria-selected')).toBe('true')
    expect(byTestId(host, 'models-tab-providers')?.textContent).toContain('Providers')
    // The mock list has five remote providers (operator-made, one unconverted, one agent-managed).
    expect(byTestId(host, 'models-tab-providers')?.querySelector('.slot-tab-ct')?.textContent).toBe('5')
    expect(host.textContent).not.toContain('Upstream Models')
    await unmount()
  })

  it('maps the old upstream address onto Providers', async () => {
    const { host, unmount } = await mount('upstream')
    expect(byTestId(host, 'providers-tab')).not.toBeNull()
    await unmount()
  })

  it('offers Refresh model lists and + Add provider, and the latter opens the modal', async () => {
    const { host, unmount } = await mount('providers')
    expect(byTestId(host, 'mdl-check-providers')?.textContent).toBe('Refresh model lists')
    expect(byTestId(host, 'add-provider-modal')).toBeNull()
    const add = byTestId(host, 'mdl-add')
    expect(add?.textContent).toBe('+ Add provider')
    await act(async () => add!.click())
    expect(byTestId(host, 'add-provider-modal')).not.toBeNull()
    await unmount()
  })

  it('refreshes only per-model providers that have a key or need none, in sequence', async () => {
    const { host, unmount } = await mount('providers')
    await act(async () => byTestId(host, 'mdl-check-providers')!.click())
    await act(async () => {
      await new Promise((r) => setTimeout(r, 50))
    })
    // minimax and openrouter are per-model with a key; openai has no key, corp is unconverted,
    // hermes.coder is managed.
    expect(posts).toEqual(['/api/upstreams/minimax/refresh', '/api/upstreams/openrouter/refresh'])
    expect(toast).toHaveBeenCalledWith('Refreshed 2 model lists', 'info')
    await unmount()
  })

  it('counts only clean answers and says how many could not be reached', async () => {
    refreshAnswer = (name) => ({ name, curated: true, stale: name === 'openrouter', warning: null, models: [] })
    const { host, unmount } = await mount('providers')
    await act(async () => byTestId(host, 'mdl-check-providers')!.click())
    await act(async () => {
      await new Promise((r) => setTimeout(r, 50))
    })
    expect(toast).toHaveBeenCalledWith('Refreshed 1 model list · 1 could not be reached', 'warn')
    await unmount()
  })

  it('a warning on the answer counts as not reached too', async () => {
    refreshAnswer = (name) => ({ name, curated: true, stale: false, warning: 'x', models: [] })
    const { host, unmount } = await mount('providers')
    await act(async () => byTestId(host, 'mdl-check-providers')!.click())
    await act(async () => {
      await new Promise((r) => setTimeout(r, 50))
    })
    expect(toast).toHaveBeenCalledWith('Refreshed 0 model lists · 2 could not be reached', 'warn')
    await unmount()
  })

  it('says there is no provider to refresh when none is eligible', async () => {
    upstreamOverride = []
    const { host, unmount } = await mount('providers')
    await act(async () => byTestId(host, 'mdl-check-providers')!.click())
    expect(posts).toEqual([])
    expect(toast).toHaveBeenCalledWith('No provider to refresh.', 'info')
    await unmount()
  })
})
