// @vitest-environment happy-dom
//
// Models → Providers: the detail panel. Real hooks and query cache; only the HTTP client is replaced.
import React, { act, createElement } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, click, mount } from '@/dash/memory-area/shared/test-harness'
import type { UpstreamEntry, UpstreamModelRow } from '@/api/hooks/useUpstreams'

;(globalThis as unknown as { React: typeof React }).React = React
;(globalThis as unknown as { Icons: unknown }).Icons = new Proxy({}, { get: () => () => null })

vi.mock('@/api/client', async () => {
  const actual = await vi.importActual<typeof import('@/api/client')>('@/api/client')
  return { ...actual, apiGet: vi.fn(), apiPost: vi.fn(), apiPatch: vi.fn(), apiDelete: vi.fn() }
})
const copied: string[] = []
vi.mock('@/lib/clipboard', () => ({
  copyTextToClipboard: async (t: string) => {
    copied.push(t)
    return true
  },
}))

const client = await import('@/api/client')
const { ProviderDetail } = await import('./ProviderDetail.jsx')

const entry = (name: string, over: Partial<UpstreamEntry> = {}): UpstreamEntry => ({
  name,
  kind: 'remote',
  url: `https://api.${name}.example/v1`,
  auth_style: 'bearer',
  auth_header: '',
  auth_value_env: `${name.toUpperCase()}_API_KEY`,
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
  models_total: 2,
  ...over,
})

const row = (id: string, over: Partial<UpstreamModelRow> = {}): UpstreamModelRow => ({
  id,
  canonical_id: `minimax/${id}`,
  enabled: false,
  type: 'chat',
  context: 204000,
  price_in: 0.3,
  price_out: 1.2,
  price_unit: 'Mtok',
  discovered: true,
  recommended: false,
  in_use_by: [],
  ...over,
})

const SECRET = 'sk-test-0123456789'
let upstreams: UpstreamEntry[]
let models: UpstreamModelRow[]
let toasts: Array<[string, string]>
let qc: QueryClient
let listingOver: Record<string, unknown>

beforeEach(() => {
  upstreams = [entry('alpha'), entry('minimax'), entry('zeta')]
  models = [row('m1', { enabled: true }), row('m2')]
  listingOver = {}
  toasts = []
  copied.length = 0
  ;(window as unknown as { __hal0Toast: unknown }).__hal0Toast = (m: string, k: string) => toasts.push([m, k])
  for (const fn of [client.apiGet, client.apiPost, client.apiPatch, client.apiDelete]) vi.mocked(fn).mockReset()
  vi.mocked(client.apiGet).mockImplementation(async (url: string) => {
    if (url === '/api/upstreams') return upstreams as never
    if (url === '/api/providers/catalog') return {} as never
    if (/^\/api\/upstreams\/[^/]+\/models$/.test(url)) {
      return { name: 'minimax', curated: true, protected: false, fetched_at: Math.floor(Date.now() / 1000) - 120, stale: false, models, ...listingOver } as never
    }
    throw new Error('unexpected GET ' + url)
  })
  qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 30_000 }, mutations: { retry: false } } })
})
afterEach(cleanup)

type Sel = { provider: string | null; model: string | null; editKey?: boolean }
function view(selected: Sel) {
  const onSelect = vi.fn()
  const ui = (s: Sel) => createElement(QueryClientProvider, { client: qc }, createElement(ProviderDetail, { selected: s, onSelect }))
  const m = mount(ui(selected))
  return { ...m, onSelect, rerender: (s: Sel) => m.render(ui(s)) }
}
// react-query hands results to React through a zero-delay timer, so waiting is real time.
async function settle() {
  await act(async () => {
    await new Promise((r) => setTimeout(r, 4))
  })
}

const q = <T extends Element = HTMLElement>(sel: string) => document.querySelector(sel) as T | null
const qa = (sel: string) => Array.from(document.querySelectorAll<HTMLElement>(sel))
const btn = (label: string) => qa('button').find((b) => b.textContent === label)
const text = () => document.body.textContent ?? ''
const flush = async () => {
  await settle()
  await settle()
  await settle()
}
async function type(el: HTMLElement | null, value: string) {
  expect(el).not.toBeNull()
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
  await act(async () => {
    setter.call(el, value)
    el!.dispatchEvent(new Event('input', { bubbles: true }))
  })
}

describe('provider selected', () => {
  it('shows the facts and the actions', async () => {
    view({ provider: 'minimax', model: null })
    await flush()
    expect(q('[data-testid="prov-fact-name"]')!.textContent).toBe('minimax')
    expect(q('[data-testid="prov-fact-url"]')!.textContent).toBe('https://api.minimax.example/v1')
    expect(q('[data-testid="prov-fact-key"]')!.textContent).toBe('MINIMAX_API_KEY · set')
    expect(q('[data-testid="prov-fact-checked"]')!.textContent).toBe('2m ago')
    expect(btn('Test connection')).toBeDefined()
    expect(btn('Refresh model list')).toBeDefined()
    expect(btn('Replace key')).toBeDefined()
    expect(btn('Remove')).toBeDefined()
  })

  it('shows "never" before the first model list check', async () => {
    vi.mocked(client.apiGet).mockImplementation(async (url: string) => {
      if (url === '/api/upstreams') return upstreams as never
      if (url === '/api/providers/catalog') return {} as never
      return { name: 'minimax', curated: true, protected: false, fetched_at: null, stale: false, models: [] } as never
    })
    view({ provider: 'minimax', model: null })
    await flush()
    expect(q('[data-testid="prov-fact-checked"]')!.textContent).toBe('never')
  })

  it('"Shown in app model pickers" PATCHes advertise_models', async () => {
    vi.mocked(client.apiPatch).mockResolvedValue({} as never)
    view({ provider: 'minimax', model: null })
    await flush()
    const sw = q('[role="switch"][aria-label="Show this provider\'s models in app model pickers"]')!
    expect(sw.getAttribute('aria-checked')).toBe('true')
    click(sw)
    await flush()
    expect(client.apiPatch).toHaveBeenCalledWith('/api/upstreams/minimax', { advertise_models: false })
  })

  it('"Test connection" shows latency and the model count, or the error', async () => {
    vi.mocked(client.apiPost).mockResolvedValueOnce({ ok: true, latency_ms: 142, models_count: 7 } as never)
    view({ provider: 'minimax', model: null })
    await flush()
    click(btn('Test connection')!)
    await flush()
    expect(client.apiPost).toHaveBeenCalledWith('/api/upstreams/minimax/test')
    expect(q('[data-testid="prov-test-line"]')!.textContent).toBe('Reachable · 142 ms · 7 models')
    vi.mocked(client.apiPost).mockResolvedValueOnce({ ok: false, error: 'HTTP 401' } as never)
    click(btn('Test connection')!)
    await flush()
    expect(q('[data-testid="prov-test-line"]')!.textContent).toBe('HTTP 401')
  })

  it('"Test connection" on a provider with no key says so and does not call the test route', async () => {
    upstreams = [entry('minimax', { auth_key_present: false })]
    view({ provider: 'minimax', model: null })
    await flush()
    const b = btn('Test connection') as HTMLButtonElement
    expect(b.disabled).toBe(false)
    click(b)
    await flush()
    expect(client.apiPost).not.toHaveBeenCalled()
    expect(q('[data-testid="prov-test-line"]')!.textContent).toBe('No key stored for this provider. Add a key, then test again.')
  })

  it('"Refresh model list" on a provider with no key asks for a key and does not call the server', async () => {
    upstreams = [entry('minimax', { auth_key_present: false })]
    view({ provider: 'minimax', model: null })
    await flush()
    click(btn('Refresh model list')!)
    await flush()
    expect(client.apiPost).not.toHaveBeenCalled()
    expect(toasts).toEqual([['Add a key for minimax to load its model list.', 'warn']])
  })

  it('the header shows the provider name when it differs from the preset default name', async () => {
    upstreams = [entry('ct150-check', { catalog_id: 'minimax' })]
    vi.mocked(client.apiGet).mockImplementation(async (url: string) => {
      if (url === '/api/upstreams') return upstreams as never
      if (url === '/api/providers/catalog') return { minimax: { id: 'minimax', name: 'MiniMax' } } as never
      return { name: 'ct150-check', curated: true, protected: false, fetched_at: 1, stale: false, models } as never
    })
    view({ provider: 'ct150-check', model: null })
    await flush()
    const head = q('[data-testid="prov-detail-provider"]')!.textContent!
    expect(head).toContain('MiniMax')
    expect(head).toContain('ct150-check · api.ct150-check.example')
  })

  it('a not-converted provider offers "Choose models" instead of "Refresh model list"', async () => {
    upstreams = [entry('minimax', { curated: false })]
    view({ provider: 'minimax', model: null })
    await flush()
    expect(btn('Refresh model list')).toBeUndefined()
    expect(btn('Choose models')).toBeDefined()
  })

  it('a managed provider shows facts only and the read-only line', async () => {
    upstreams = [entry('hermes.coder', { managed_by: 'agent:hermes' })]
    view({ provider: 'hermes.coder', model: null })
    await flush()
    expect(q('[data-testid="prov-fact-key"]')!.textContent).toBe('held by the agent')
    expect(text()).toContain('Managed providers cannot be edited or removed here.')
    for (const l of ['Test connection', 'Refresh model list', 'Replace key', 'Add key', 'Remove']) expect(btn(l)).toBeUndefined()
    expect(q<HTMLButtonElement>('[role="switch"]')!.disabled).toBe(true)
  })
})

describe('key slot', () => {
  it('a provider with no key env name gets no editor and says so', async () => {
    upstreams = [entry('minimax', { auth_value_env: '', auth_key_present: false })]
    view({ provider: 'minimax', model: null })
    await flush()
    expect(btn('Add key')).toBeUndefined()
    expect(btn('Replace key')).toBeUndefined()
    expect(text()).toContain('This provider has no key slot configured.')
    expect(btn('Save key')).toBeUndefined()
  })
})

describe('key editor', () => {
  it('is a labelled password input that never shows the saved value and clears on success', async () => {
    vi.mocked(client.apiPost).mockResolvedValue({} as never)
    view({ provider: 'minimax', model: null })
    await flush()
    click(btn('Replace key')!)
    const input = q<HTMLInputElement>('input[type="password"]')!
    expect(input.labels?.[0]?.textContent).toBe('MINIMAX_API_KEY')
    expect(input.value).toBe('')
    expect(text()).toContain('Stored on this box only. Never shown again.')
    await type(input, SECRET)
    click(btn('Save key')!)
    await flush()
    expect(client.apiPost).toHaveBeenCalledWith('/api/providers/minimax/credentials', { key: 'MINIMAX_API_KEY', value: SECRET })
    expect(q('input[type="password"]')).toBeNull()
    expect(document.body.innerHTML).not.toContain(SECRET)
    expect(JSON.stringify(toasts)).not.toContain(SECRET)
    expect(qc.getMutationCache().getAll().map((m) => m.state.variables)).not.toContainEqual(expect.objectContaining({ value: SECRET }))
    // reopening starts empty
    click(btn('Replace key')!)
    expect(q<HTMLInputElement>('input[type="password"]')!.value).toBe('')
  })

  it('Cancel clears the typed key', async () => {
    view({ provider: 'minimax', model: null })
    await flush()
    click(btn('Replace key')!)
    await type(q('input[type="password"]'), SECRET)
    click(btn('Cancel')!)
    expect(q('input[type="password"]')).toBeNull()
    click(btn('Replace key')!)
    expect(q<HTMLInputElement>('input[type="password"]')!.value).toBe('')
    expect(client.apiPost).not.toHaveBeenCalled()
  })

  it('a failed save keeps the editor and says what to do, without echoing the key', async () => {
    vi.mocked(client.apiPost).mockRejectedValue(new Error('key store is read-only'))
    view({ provider: 'minimax', model: null })
    await flush()
    click(btn('Replace key')!)
    await type(q('input[type="password"]'), SECRET)
    click(btn('Save key')!)
    await flush()
    expect(q('[data-testid="prov-key-editor"] [role="alert"]')!.textContent).toContain('key store is read-only')
    expect(q<HTMLInputElement>('input[type="password"]')).not.toBeNull()
    expect(document.body.textContent).not.toContain(SECRET)
    expect(qc.getMutationCache().getAll()).toHaveLength(0)
  })

  it('opens when the list asked for it through the selection, and clears the request', async () => {
    upstreams = [entry('minimax', { auth_key_present: false })]
    const v = view({ provider: 'minimax', model: null, editKey: true })
    await flush()
    expect(q('input[type="password"]')).not.toBeNull()
    expect(btn('Add key')).toBeUndefined() // the editor is open; the button is hidden
    expect(v.onSelect).toHaveBeenCalledWith({ provider: 'minimax', model: null })
  })
})

describe('model panel state', () => {
  it('open Edit on one model, select another: no editor, Save sends nothing until Edit is opened again', async () => {
    vi.mocked(client.apiPatch).mockResolvedValue({} as never)
    models = [row('m1', { context: 1000 }), row('m2', { context: 2000 })]
    const v = view({ provider: 'minimax', model: 'm1' })
    await flush()
    click(btn('Edit')!)
    await type(qa('[data-testid="prov-edit"] input')[0], '999')
    v.rerender({ provider: 'minimax', model: 'm2' })
    await flush()
    expect(q('[data-testid="prov-edit"]')).toBeNull()
    expect(btn('Save')).toBeUndefined()
    expect(client.apiPatch).not.toHaveBeenCalled()
    click(btn('Edit')!)
    expect((qa('[data-testid="prov-edit"] input')[0] as HTMLInputElement).value).toBe('2000')
  })
})

describe('remove', () => {
  it('asks first, then deletes and moves the selection to the first remaining provider', async () => {
    vi.mocked(client.apiDelete).mockResolvedValue({} as never)
    const v = view({ provider: 'alpha', model: null })
    await flush()
    click(btn('Remove')!)
    await flush()
    expect(client.apiDelete).not.toHaveBeenCalled()
    expect(text()).toContain('Remove alpha?')
    expect(text()).toContain('Its models stop being callable')
    expect(text()).toContain('stored key is kept')
    click(btn('Remove provider')!)
    await flush()
    expect(client.apiDelete).toHaveBeenCalledWith('/api/upstreams/alpha')
    expect(v.onSelect).toHaveBeenCalledWith({ provider: 'minimax', model: null })
  })

  it('Cancel on the confirmation deletes nothing', async () => {
    view({ provider: 'alpha', model: null })
    await flush()
    click(btn('Remove')!)
    await flush()
    click(btn('Cancel')!)
    await flush()
    expect(client.apiDelete).not.toHaveBeenCalled()
  })
})

describe('model selected', () => {
  it('shows the callable name, copies it and goes back', async () => {
    const v = view({ provider: 'minimax', model: 'm2' })
    await flush()
    expect(q('[data-testid="prov-call-name"]')!.textContent).toBe('minimax/m2')
    click(btn('Copy')!)
    await flush()
    expect(copied).toEqual(['minimax/m2'])
    expect(text()).toContain('yes')
    click(btn('Back to provider')!)
    expect(v.onSelect).toHaveBeenCalledWith({ provider: 'minimax', model: null })
  })

  it('says when the provider no longer lists the model', async () => {
    models = [row('m2', { discovered: false })]
    view({ provider: 'minimax', model: 'm2' })
    await flush()
    expect(text()).toContain('no longer listed')
  })

  it('says "unknown" while the model list is not known', async () => {
    models = [row('m2', { discovered: false })]
    listingOver = { stale: true, fetched_at: null }
    view({ provider: 'minimax', model: 'm2' })
    await flush()
    expect(text()).not.toContain('no longer listed')
    expect(text()).toContain('unknown')
  })

  it('says "yes" for a model a managed or not-converted provider is serving (no fetch time, rows discovered)', async () => {
    for (const over of [{ managed_by: 'agent:hermes' }, { curated: false }]) {
      upstreams = [entry('minimax', over)]
      models = [row('m2')]
      listingOver = { stale: over.curated === false, fetched_at: null, protected: !!over.managed_by, curated: over.curated !== false }
      const v = view({ provider: 'minimax', model: 'm2' })
      await flush()
      expect(q('[data-testid="prov-detail-model"]')!.textContent).toContain('yes')
      expect(text()).not.toContain('unknown')
      v.unmount?.()
      cleanup()
    }
  })

  it('changing the type PATCHes { type }', async () => {
    vi.mocked(client.apiPatch).mockResolvedValue({} as never)
    view({ provider: 'minimax', model: 'm2' })
    await flush()
    const sel = q<HTMLSelectElement>('select[aria-label="Model type"]')!
    expect(sel.value).toBe('chat')
    await act(async () => {
      sel.value = 'embed'
      sel.dispatchEvent(new Event('change', { bubbles: true }))
    })
    await flush()
    expect(client.apiPatch).toHaveBeenCalledWith('/api/upstreams/minimax/models/m2', { type: 'embed' })
  })

  it('"Edit" turns context and prices into inputs; Save PATCHes, Cancel does not', async () => {
    vi.mocked(client.apiPatch).mockResolvedValue({} as never)
    view({ provider: 'minimax', model: 'm2' })
    await flush()
    click(btn('Edit')!)
    click(btn('Cancel')!)
    expect(client.apiPatch).not.toHaveBeenCalled()
    click(btn('Edit')!)
    const inputs = qa('[data-testid="prov-edit"] input')
    expect(inputs.length).toBe(3)
    await type(inputs[0], '256000')
    await type(inputs[2], '')
    click(btn('Save')!)
    await flush()
    expect(client.apiPatch).toHaveBeenCalledWith('/api/upstreams/minimax/models/m2', {
      context: 256000,
      price_in: 0.3,
      price_out: null,
    })
    expect(q('[data-testid="prov-edit"]')).toBeNull()
  })

  it('"Switch on" / "Switch off" flips the model', async () => {
    vi.mocked(client.apiPatch).mockResolvedValue({} as never)
    view({ provider: 'minimax', model: 'm2' })
    await flush()
    click(btn('Switch on')!)
    await flush()
    expect(client.apiPatch).toHaveBeenCalledWith('/api/upstreams/minimax/models/m2', { enabled: true })
  })

  it('is read-only for a managed provider', async () => {
    upstreams = [entry('minimax', { managed_by: 'agent:hermes' })]
    view({ provider: 'minimax', model: 'm2' })
    await flush()
    expect(btn('Switch on')).toBeUndefined()
    expect(btn('Edit')).toBeUndefined()
    expect(q<HTMLSelectElement>('select[aria-label="Model type"]')!.disabled).toBe(true)
    expect(btn('Back to provider')).toBeDefined()
  })

  it('is read-only for a not-converted provider', async () => {
    upstreams = [entry('minimax', { curated: false })]
    view({ provider: 'minimax', model: 'm2' })
    await flush()
    expect(btn('Switch on')).toBeUndefined()
    expect(btn('Edit')).toBeUndefined()
  })
})

describe('nothing selected', () => {
  it('says so', async () => {
    view({ provider: null, model: null })
    await flush()
    expect(text()).toContain('Select a provider')
  })
})

describe('Routes requests', () => {
  it('is a switch for an operator provider, above "Shown in app model pickers", and PATCHes enabled', async () => {
    vi.mocked(client.apiPatch).mockResolvedValue({} as never)
    view({ provider: 'minimax', model: null })
    await flush()
    const sw = q<HTMLButtonElement>('[data-testid="prov-routes-switch"] [role="switch"]')!
    expect(sw.getAttribute('aria-checked')).toBe('true')
    expect(text().indexOf('Routes requests')).toBeGreaterThan(-1)
    expect(text().indexOf('Routes requests')).toBeLessThan(text().indexOf('Shown in app model pickers'))
    click(sw)
    await settle()
    expect(client.apiPatch).toHaveBeenCalledWith('/api/upstreams/minimax', { enabled: false })
  })

  it('reads off for a switched-off provider and sends enabled: true', async () => {
    upstreams = [entry('minimax', { enabled: false })]
    vi.mocked(client.apiPatch).mockResolvedValue({} as never)
    view({ provider: 'minimax', model: null })
    await flush()
    const sw = q<HTMLButtonElement>('[data-testid="prov-routes-switch"] [role="switch"]')!
    expect(sw.getAttribute('aria-checked')).toBe('false')
    click(sw)
    await settle()
    expect(client.apiPatch).toHaveBeenCalledWith('/api/upstreams/minimax', { enabled: true })
  })

  it('a refused change toasts the server message and the switch follows the cache', async () => {
    vi.mocked(client.apiPatch).mockRejectedValue(Object.assign(new Error('could not write the config'), { status: 500 }))
    view({ provider: 'minimax', model: null })
    await flush()
    click(q('[data-testid="prov-routes-switch"] [role="switch"]')!)
    await flush()
    expect(toasts).toContainEqual(['could not write the config', 'err'])
    expect(q('[data-testid="prov-routes-switch"] [role="switch"]')!.getAttribute('aria-checked')).toBe('true')
  })

  it('is absent for a managed provider', async () => {
    upstreams = [entry('hermes.coder', { managed_by: 'agent:hermes' })]
    view({ provider: 'hermes.coder', model: null })
    await flush()
    expect(q('[data-testid="prov-routes-switch"]')).toBeNull()
  })
})

describe('key editor input and rule', () => {
  it('asks password managers to leave the key alone', async () => {
    view({ provider: 'minimax', model: null })
    await flush()
    click(btn('Replace key')!)
    const input = q<HTMLInputElement>('input[type="password"]')!
    expect(input.getAttribute('autocomplete')).toBe('off')
    expect(input.getAttribute('data-1p-ignore')).not.toBeNull()
    expect(input.getAttribute('data-lpignore')).toBe('true')
  })

  it('applies the same key rule as the Add provider modal: no request for a key that can never be stored', async () => {
    view({ provider: 'minimax', model: null })
    await flush()
    click(btn('Replace key')!)
    await type(q('input[type="password"]'), 'sk with spaces')
    expect(q('[data-testid="prov-key-editor"] [role="alert"]')!.textContent).toBe('The key must be printable ASCII without spaces.')
    expect(btn('Save key')!.hasAttribute('disabled')).toBe(true)
    click(btn('Save key')!)
    await flush()
    expect(client.apiPost).not.toHaveBeenCalled()
    expect(document.body.textContent).not.toContain('sk with spaces')
  })
})

describe('context and price editor rules', () => {
  const open = async () => {
    view({ provider: 'minimax', model: 'm2' })
    await flush()
    click(btn('Edit')!)
    return qa('[data-testid="prov-edit"] input') as HTMLInputElement[]
  }

  it('refuses a context that is not a whole number above 0 with a field message', async () => {
    const inputs = await open()
    for (const bad of ['0', '1.5', '-3']) {
      await type(inputs[0], bad)
      expect(q('[data-testid="prov-edit"] [role="alert"]')!.textContent).toContain('Context')
      expect(btn('Save')!.hasAttribute('disabled')).toBe(true)
    }
    await type(inputs[0], '')
    expect(q('[data-testid="prov-edit"] [role="alert"]')).toBeNull()
    expect(btn('Save')!.hasAttribute('disabled')).toBe(false)
  })

  it('refuses a negative or non-numeric price', async () => {
    const inputs = await open()
    await type(inputs[1], '-0.1')
    expect(q('[data-testid="prov-edit"] [role="alert"]')!.textContent).toContain('Price')
    expect(btn('Save')!.hasAttribute('disabled')).toBe(true)
    await type(inputs[1], 'abc')
    expect(btn('Save')!.hasAttribute('disabled')).toBe(true)
    await type(inputs[1], '0')
    expect(btn('Save')!.hasAttribute('disabled')).toBe(false)
  })

  it('a refusal from the server shows the server message, not the generic one', async () => {
    vi.mocked(client.apiPatch).mockRejectedValue(
      Object.assign(new Error('request validation failed'), { status: 422, details: { fields: [{ loc: ['body', 'context'], msg: 'Input should be greater than 0' }] } }),
    )
    await open()
    click(btn('Save')!)
    await flush()
    expect(toasts).toContainEqual(['context: Input should be greater than 0', 'err'])
    expect(JSON.stringify(toasts)).not.toContain('Could not change the model. Try again.')
  })
})
