// @vitest-environment happy-dom
//
// Models → Providers list. The real hooks and query cache run; only the HTTP client is replaced,
// so a refused switch really goes through the patch hook's rollback.
import React, { act, createElement, useState } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, click, mount } from '@/dash/memory-area/shared/test-harness'
import type { UpstreamEntry, UpstreamModelRow } from '@/api/hooks/useUpstreams'
import { OFF_PAGE } from './providers-pure'

;(globalThis as unknown as { React: typeof React }).React = React

vi.mock('@/api/client', async () => {
  const actual = await vi.importActual<typeof import('@/api/client')>('@/api/client')
  return { ...actual, apiGet: vi.fn(), apiPost: vi.fn(), apiPatch: vi.fn(), apiDelete: vi.fn() }
})

const client = await import('@/api/client')
const { ProvidersTab } = await import('./ProvidersTab.jsx')

const entry = (name: string, over: Partial<UpstreamEntry> = {}): UpstreamEntry => ({
  name,
  kind: 'remote',
  url: `https://api.${name}.example/v1`,
  auth_style: 'bearer',
  auth_header: '',
  auth_value_env: `${name.toUpperCase()}_KEY`,
  auth_configured: true,
  auth_key_present: true,
  timeout_seconds: 30,
  slot_name: null,
  warmup_strategy: 'none',
  advertise_models: true,
  enabled: true,
  model_filters: null,
  models: [],
  managed_by: null,
  catalog_id: null,
  curated: true,
  budget_monthly_usd: null,
  enabled_count: 1,
  models_total: 3,
  ...over,
})

const row = (id: string, over: Partial<UpstreamModelRow> = {}): UpstreamModelRow => ({
  id,
  canonical_id: null,
  enabled: false,
  type: 'chat',
  context: 128000,
  price_in: 0.3,
  price_out: 1.2,
  price_unit: 'Mtok',
  discovered: true,
  recommended: false,
  in_use_by: [],
  ...over,
})

const listing = (name: string, models: UpstreamModelRow[], over: Record<string, unknown> = {}) => ({
  name,
  curated: true,
  protected: false,
  fetched_at: 1_700_000_000,
  stale: false,
  models: models.map((m) => ({ ...m, canonical_id: `${name}/${m.id}` })),
  ...over,
})

const big = Array.from({ length: 300 }, (_, i) => row(`m${String(i).padStart(3, '0')}`, { enabled: i < 2 }))

let upstreams: UpstreamEntry[]
let listings: Record<string, unknown>
let toasts: Array<[string, string]>
let qc: QueryClient

const modelCalls = () =>
  vi.mocked(client.apiGet).mock.calls.filter(([u]) => /\/api\/upstreams\/[^/]+\/models$/.test(String(u))).length

beforeEach(() => {
  upstreams = [
    entry('openrouter', { enabled_count: 2, models_total: 300 }),
    entry('minimax', { enabled_count: 1, models_total: 2, catalog_id: 'minimax' }),
    entry('hermes.coder', { managed_by: 'agent:hermes', enabled_count: 1, models_total: 1 }),
    entry('corp', { curated: false, auth_style: 'none', enabled_count: 0, models_total: 0 }),
    entry('llama-main', { kind: 'slot', slot_name: 'llama-main' }),
  ]
  listings = {
    openrouter: listing('openrouter', big),
    minimax: listing('minimax', [row('m1', { enabled: true }), row('m2')]),
    corp: listing('corp', [row('c1')], { curated: false, stale: true }),
    'hermes.coder': listing('hermes.coder', [row('h1', { enabled: true })], { protected: true }),
  }
  toasts = []
  ;(window as unknown as { __hal0Toast: unknown }).__hal0Toast = (m: string, k: string) => toasts.push([m, k])
  for (const fn of [client.apiGet, client.apiPost, client.apiPatch, client.apiDelete]) vi.mocked(fn).mockReset()
  vi.mocked(client.apiGet).mockImplementation(async (url: string) => {
    if (url === '/api/upstreams') return upstreams as never
    if (url === '/api/providers/catalog') return { minimax: { id: 'minimax', name: 'MiniMax' } } as never
    const m = /^\/api\/upstreams\/([^/]+)\/models$/.exec(url)
    if (m && listings[m[1]]) return listings[m[1]] as never
    throw new Error('unexpected GET ' + url)
  })
  qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 30_000 }, mutations: { retry: false } } })
})
afterEach(cleanup)

function Harness({ initial, spy, onAdd }: { initial: Sel; spy: (n: Sel) => void; onAdd: () => void }) {
  const [sel, setSel] = useState(initial)
  return createElement(ProvidersTab, {
    selected: sel,
    onSelect: (n: Sel) => {
      spy(n)
      setSel(n)
    },
    onAdd,
  })
}

type Sel = { provider: string | null; model: string | null; editKey?: boolean }

function view(initial: Sel = { provider: null, model: null }) {
  const onSelect = vi.fn()
  const onAdd = vi.fn()
  const m = mount(
    createElement(QueryClientProvider, { client: qc }, createElement(Harness, { initial, spy: onSelect, onAdd })),
  )
  return { ...m, onSelect, onAdd }
}

// react-query hands results to React through a zero-delay timer, so waiting is real time.
async function settle() {
  await act(async () => {
    await new Promise((r) => setTimeout(r, 4))
  })
}

const q = <T extends Element = HTMLElement>(sel: string) => document.querySelector(sel) as T | null
const qa = (sel: string) => Array.from(document.querySelectorAll<HTMLElement>(sel))
const text = () => document.body.textContent ?? ''

async function type(el: HTMLElement | null, value: string) {
  expect(el).not.toBeNull()
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
  await act(async () => {
    setter.call(el, value)
    el!.dispatchEvent(new Event('input', { bubbles: true }))
  })
}

const expand = async (name: string) => {
  const sel = { provider: name, model: null }
  const v = view(sel)
  await settle()
  await settle()
  return v
}

describe('provider sections', () => {
  it('lists operator providers by name, then managed ones, with chips; slot upstreams are absent', async () => {
    view()
    await settle()
    await settle()
    const heads = qa('[data-testid^="prov-head-"]').map((e) => e.getAttribute('data-testid'))
    expect(heads).toEqual(['prov-head-corp', 'prov-head-minimax', 'prov-head-openrouter', 'prov-head-hermes.coder'])
    const minimax = q('[data-testid="prov-head-minimax"]')!.textContent!
    expect(minimax).toContain('MiniMax')
    expect(minimax).toContain('api.minimax.example')
    expect(minimax).toContain('key set')
    expect(minimax).toContain('1 of 2 on')
    expect(q('[data-testid="prov-head-corp"]')!.textContent).toContain('routes everything')
    expect(q('[data-testid="prov-head-corp"]')!.textContent).toContain('no key needed')
    expect(q('[data-testid="prov-head-hermes.coder"]')!.textContent).toContain('read-only')
    expect(text()).not.toContain('llama-main')
  })

  it('loads a listing when a section opens; collapsing and reopening does not refetch', async () => {
    const { onSelect } = view()
    await settle()
    await settle()
    expect(modelCalls()).toBe(0)
    click(q('[data-testid="prov-head-minimax"]')!)
    await settle()
    await settle()
    expect(onSelect).toHaveBeenCalledWith({ provider: 'minimax', model: null })
    expect(modelCalls()).toBe(1)
    expect(text()).toContain('On · 1')
    const head = q('[data-testid="prov-head-minimax"]')!
    expect(head.getAttribute('aria-expanded')).toBe('true')
    click(head) // the selected header collapses
    expect(head.getAttribute('aria-expanded')).toBe('false')
    click(head)
    await settle()
    expect(head.getAttribute('aria-expanded')).toBe('true')
    expect(modelCalls()).toBe(1)
  })

  it('caps a 300-row provider at the paged off rows and adds 25 per click', async () => {
    await expand('openrouter')
    expect(qa('[data-testid^="prov-model-openrouter-"]').length).toBe(2 + OFF_PAGE)
    expect(text()).toContain('273 more available')
    click(qa('button').find((b) => b.textContent === 'Show 25 more')!)
    expect(qa('[data-testid^="prov-model-openrouter-"]').length).toBe(2 + OFF_PAGE * 2)
    expect(text()).toContain('248 more available')
  })

  it('a search reveals a row beyond the page', async () => {
    await expand('openrouter')
    expect(q('[data-testid="prov-model-openrouter-m299"]')).toBeNull()
    await type(q('input[aria-label="Search models"]'), 'm299')
    expect(q('[data-testid="prov-model-openrouter-m299"]')).not.toBeNull()
    expect(qa('[data-testid^="prov-model-openrouter-"]').length).toBe(1)
  })

  it('the type chip narrows every section', async () => {
    listings.minimax = listing('minimax', [row('m1', { enabled: true }), row('e1', { type: 'embed' })])
    await expand('minimax')
    expect(qa('[data-testid^="prov-model-minimax-"]').length).toBe(2)
    click(q('[data-testid="mdl-filter-type-embed"]')!)
    expect(qa('[data-testid^="prov-model-minimax-"]').map((e) => e.getAttribute('data-testid'))).toEqual(['prov-model-minimax-e1'])
  })

  it('clicking a row selects the model, not the switch', async () => {
    const v = await expand('minimax')
    click(q('[data-testid="prov-model-minimax-m2"] .prov-pick')!)
    expect(v.onSelect).toHaveBeenCalledWith({ provider: 'minimax', model: 'm2' })
    v.onSelect.mockClear()
    click(q('[data-testid="prov-model-minimax-m2"] [role="switch"]')!)
    expect(v.onSelect).not.toHaveBeenCalled()
  })

  it('shows the quiet line for a stale listing', async () => {
    await expand('corp')
    expect(text()).toContain('Model list could not be refreshed; showing the last known list.')
  })

  it('shows a flag chip and the callable name', async () => {
    listings.minimax = listing('minimax', [row('m1', { enabled: true, discovered: false }), row('m2', { recommended: true })])
    await expand('minimax')
    expect(q('[data-testid="prov-model-minimax-m1"]')!.textContent).toContain('gone')
    expect(q('[data-testid="prov-model-minimax-m2"]')!.textContent).toContain('recommended')
    expect(q('[data-testid="prov-model-minimax-m2"]')!.textContent).toContain('minimax/m2')
  })
})

describe('search', () => {
  const later = async (ms: number) => {
    await act(async () => {
      await new Promise((r) => setTimeout(r, ms))
    })
  }

  it('with every section collapsed, finds a deep row in the 300-row provider after the debounce', async () => {
    view()
    await settle()
    await settle()
    expect(modelCalls()).toBe(0)
    await type(q('input[aria-label="Search models"]'), 'm299')
    expect(modelCalls()).toBe(0) // debounced
    await later(300)
    await settle()
    expect(q('[data-testid="prov-model-openrouter-m299"]')).not.toBeNull()
    // sections with no match show the header only, muted, and the count chip says "N match"
    expect(q('[data-testid="prov-head-minimax"]')!.textContent).toContain('no match')
    expect(q('[data-testid="prov-head-openrouter"]')!.textContent).toContain('1 match')
    expect(q('[data-testid="prov-on-minimax"]')).toBeNull()
  })

  it('clearing the search returns sections to how they were', async () => {
    view()
    await settle()
    await type(q('input[aria-label="Search models"]'), 'm299')
    await later(300)
    await settle()
    expect(q('[data-testid="prov-head-openrouter"]')!.getAttribute('aria-expanded')).toBe('true')
    await type(q('input[aria-label="Search models"]'), '')
    await later(300)
    await settle()
    expect(q('[data-testid="prov-head-openrouter"]')!.getAttribute('aria-expanded')).toBe('false')
    expect(q('[data-testid="prov-model-openrouter-m299"]')).toBeNull()
  })

  it('a one-character query still renders at most the paged off rows per section', async () => {
    view()
    await settle()
    await type(q('input[aria-label="Search models"]'), 'm')
    await later(300)
    await settle()
    const off = qa('[data-testid^="prov-model-openrouter-"]').filter((e) => e.closest('[data-testid="prov-off-openrouter"]'))
    expect(off.length).toBe(OFF_PAGE)
    expect(text()).toContain('273 more available')
    click(qa('button').find((b) => b.textContent === 'Show 25 more')!)
    expect(qa('[data-testid="prov-off-openrouter"] [data-testid^="prov-model-"]').length).toBe(OFF_PAGE * 2)
  })
})

describe('switches', () => {
  it('names the model on the switch and toasts on success', async () => {
    vi.mocked(client.apiPatch).mockResolvedValue({} as never)
    await expand('minimax')
    const sw = q('[data-testid="prov-model-minimax-m2"] [role="switch"]')!
    expect(sw.getAttribute('aria-label')).toBe('Switch on minimax/m2')
    expect(sw.getAttribute('aria-checked')).toBe('false')
    click(sw)
    await settle()
    await settle()
    expect(client.apiPatch).toHaveBeenCalledWith('/api/upstreams/minimax/models/m2', { enabled: true })
    expect(toasts).toContainEqual(['minimax/m2 switched on', 'ok'])
  })

  it('switching off toasts "switched off"', async () => {
    vi.mocked(client.apiPatch).mockResolvedValue({} as never)
    await expand('minimax')
    click(q('[data-testid="prov-model-minimax-m1"] [role="switch"]')!)
    await settle()
    await settle()
    expect(toasts).toContainEqual(['minimax/m1 switched off', 'ok'])
  })

  const cases: Array<[string, { status: number; code: string }, string]> = [
    ['409 not converted', { status: 409, code: 'upstream.not_curated' }, 'Choose models for this provider first.'],
    ['409 protected', { status: 409, code: 'upstream.protected' }, 'This provider is managed by an agent and cannot be changed here.'],
    ['404', { status: 404, code: 'upstream.not_found' }, 'This provider no longer exists. Reload the list.'],
  ]
  it.each(cases)('a %s refusal toasts the mapped message and the switch is back', async (_n, init, message) => {
    const { Hal0Error } = await import('@/api/client')
    vi.mocked(client.apiPatch).mockRejectedValue(new Hal0Error('refused', init))
    await expand('minimax')
    const sw = () => q('[data-testid="prov-model-minimax-m2"] [role="switch"]')!
    expect(sw().getAttribute('aria-checked')).toBe('false')
    click(sw())
    await settle()
    await settle()
    expect(toasts).toContainEqual([message, 'err'])
    expect(sw().getAttribute('aria-checked')).toBe('false')
  })

  it('a transport error (no status) toasts the generic message and the switch is back', async () => {
    vi.mocked(client.apiPatch).mockRejectedValue(new TypeError('Failed to fetch'))
    await expand('minimax')
    const sw = () => q('[data-testid="prov-model-minimax-m2"] [role="switch"]')!
    click(sw())
    await settle()
    await settle()
    expect(toasts).toContainEqual(['Could not change the model. Try again.', 'err'])
    expect(sw().getAttribute('aria-checked')).toBe('false')
  })

  it('a listing the server marks protected disables the switches even for an operator-looking entry', async () => {
    listings.minimax = listing('minimax', [row('m1', { enabled: true })], { protected: true })
    await expand('minimax')
    expect(q<HTMLButtonElement>('[data-testid="prov-model-minimax-m1"] [role="switch"]')!.disabled).toBe(true)
  })

  it('a not-converted and a managed provider have disabled switches', async () => {
    await expand('corp')
    expect(q<HTMLButtonElement>('[data-testid="prov-model-corp-c1"] [role="switch"]')!.disabled).toBe(true)
    cleanup()
    await expand('hermes.coder')
    expect(q<HTMLButtonElement>('[data-testid="prov-model-hermes.coder-h1"] [role="switch"]')!.disabled).toBe(true)
    expect(text()).toContain('Created and kept up to date by the Hermes agent. Read-only here.')
    expect(text()).not.toContain('agent:hermes')
  })
})

describe('row control', () => {
  it('the row has a real button with a name for selecting the model; the switch is separate', async () => {
    const v = await expand('minimax')
    const b = q<HTMLButtonElement>('[data-testid="prov-model-minimax-m2"] button.prov-pick')!
    expect(b.getAttribute('aria-label')).toBe('Show details of minimax/m2')
    click(b)
    expect(v.onSelect).toHaveBeenCalledWith({ provider: 'minimax', model: 'm2' })
  })
})

describe('banners', () => {
  it('"Choose models" refreshes the provider and shows a returned warning verbatim', async () => {
    vi.mocked(client.apiPost).mockResolvedValue(listing('corp', [row('c1')], { warning: 'Provider listed 9000 models; kept the first 500.' }) as never)
    await expand('corp')
    expect(text()).toContain('Added before per-model switches. It still sends any model it lists.')
    click(qa('button').find((b) => b.textContent === 'Choose models')!)
    await settle()
    await settle()
    expect(client.apiPost).toHaveBeenCalledWith('/api/upstreams/corp/refresh')
    expect(toasts).toContainEqual(['Provider listed 9000 models; kept the first 500.', 'warn'])
  })

  it('"Choose models" without a warning toasts the conversion', async () => {
    vi.mocked(client.apiPost).mockResolvedValue(listing('corp', [row('c1', { enabled: true })], { warning: null }) as never)
    await expand('corp')
    click(qa('button').find((b) => b.textContent === 'Choose models')!)
    await settle()
    await settle()
    expect(toasts).toContainEqual(['corp converted. Everything it offered is on; switch off what you do not use.', 'ok'])
  })

  it('a provider without a key offers "Add key", which selects it and asks for the editor', async () => {
    upstreams = [entry('openai', { auth_key_present: false })]
    listings.openai = listing('openai', [row('gpt', { enabled: true })])
    const v = await expand('openai')
    expect(text()).toContain('No key stored. Models that are on will fail until a key is added.')
    click(qa('button').find((b) => b.textContent === 'Add key')!)
    expect(v.onSelect).toHaveBeenCalledWith({ provider: 'openai', model: null, editKey: true })
  })

  it('a failed listing is an error on that section only', async () => {
    delete listings.minimax
    view({ provider: 'minimax', model: null })
    await settle()
    await settle()
    await settle()
    expect(q('[data-testid="prov-section-minimax"] [role="alert"]')).not.toBeNull()
    expect(q('[data-testid="prov-head-openrouter"]')).not.toBeNull()
  })
})

describe('empty state', () => {
  it('offers "+ Add provider"', async () => {
    upstreams = [entry('llama-main', { kind: 'slot', slot_name: 'llama-main' })]
    const v = view()
    await settle()
    await settle()
    expect(text()).toContain('No providers yet')
    click(qa('button').find((b) => b.textContent === '+ Add provider')!)
    expect(v.onAdd).toHaveBeenCalledTimes(1)
  })
})
