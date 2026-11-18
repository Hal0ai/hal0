// @vitest-environment happy-dom
// Models → Providers: the Add provider modal. The HTTP client is replaced by a small router so
// the real hooks run; what is pinned here is what the operator sees and exactly what is sent.
import React, { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

;(globalThis as unknown as { window: typeof globalThis }).window = globalThis
;(globalThis as unknown as { React: typeof React }).React = React
;(globalThis as unknown as { Icons: unknown }).Icons = new Proxy({}, { get: () => null })
;(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const KEY = 'sk-test-SECRET-0123456789'

const { box } = vi.hoisted(() => ({
  box: {
    posts: [] as Array<{ path: string; body: Record<string, unknown> }>,
    handlers: {} as Record<string, (body: Record<string, unknown>) => unknown>,
    upstreams: [] as Array<Record<string, unknown>>,
    clears: 0,
  },
}))

vi.mock('@/api/client', async (importOriginal) => {
  const real = await importOriginal<Record<string, unknown>>()
  return {
    ...real,
    apiGet: async (path: string) => {
      if (path === '/api/providers/catalog') return CATALOG
      if (path === '/api/upstreams') return box.upstreams
      throw new Error(`unexpected GET ${path}`)
    },
    apiPost: async (path: string, body: Record<string, unknown>) => {
      box.posts.push({ path, body })
      const h = box.handlers[path]
      if (!h) throw new Error(`unexpected POST ${path}`)
      return h(body)
    },
  }
})

vi.mock('@/api/hooks/useUpstreams', async (importOriginal) => {
  const real = await importOriginal<Record<string, (...a: unknown[]) => Record<string, unknown>>>()
  return {
    ...real,
    useProviderProbe: () => {
      const p = real.useProviderProbe() as { clear: () => void }
      return { ...p, clear: () => { box.clears += 1; p.clear() } }
    },
  }
})

const CATALOG = {
  minimax: {
    id: 'minimax', name: 'MiniMax', category: 'cloud', base_url: 'https://api.minimax.io/v1', auth: 'bearer',
    auth_header_name: 'Authorization', notes: 'MiniMax OpenAI-compatible API. Second sentence.', models: [],
  },
  ollama: {
    id: 'ollama', name: 'Ollama', category: 'local', base_url: 'http://localhost:11434/v1', auth: 'none',
    auth_header_name: '', notes: 'Local Ollama server.', models: [],
  },
  custom: {
    id: 'custom', name: 'Custom', category: 'custom', base_url: '', auth: 'bearer',
    auth_header_name: '', notes: 'Any OpenAI-compatible endpoint.', models: [],
  },
}

const probeRow = (id: string, over: Record<string, unknown> = {}) => ({
  id, canonical_id: null, type: 'chat', type_source: 'name', context: 204000, price_in: 0.3, price_out: 1.2,
  price_unit: 'Mtok', recommended: false, ...over,
})
const ROWS = [probeRow('MiniMax-M3', { recommended: true }), probeRow('MiniMax-M2'), probeRow('speech-01', { type: 'tts' })]
const okProbe = (models = ROWS) => ({ ok: true, status: 200, latency_ms: 212, models, rate_limit: {}, error: null })

const { Modal, ConfirmDialog } = await import('../primitives.jsx')
Object.assign(globalThis, { Modal, ConfirmDialog })
const { Hal0Error } = await import('@/api/client')
const { AddProviderModal } = await import('./AddProviderModal.jsx')

let host: HTMLDivElement
let root: Root
let qc: QueryClient
const onClose = vi.fn()
const onAdded = vi.fn()
const toast = vi.fn()

function mount(open = true) {
  act(() => {
    root.render(
      <QueryClientProvider client={qc}>
        <AddProviderModal open={open} onClose={onClose} onAdded={onAdded} />
      </QueryClientProvider>,
    )
  })
}
async function settle() {
  // The catalog and the provider list load through react-query: wait for both before acting.
  await act(async () => {
    await vi.waitFor(() => {
      if (qc.getQueryCache().getAll().some((q) => q.state.status === 'pending')) throw new Error('queries still loading')
    })
    await new Promise((r) => setTimeout(r, 0))
  })
}
const $ = (sel: string) => document.body.querySelector(sel) as HTMLElement | null
const byText = (tag: string, text: string) =>
  Array.from(document.body.querySelectorAll(tag)).find((e) => e.textContent?.trim().startsWith(text)) as HTMLElement | undefined
const click = (el: Element | null | undefined) => {
  if (!el) throw new Error('missing element')
  act(() => {
    ;(el as HTMLElement).click()
  })
}
function type(el: HTMLInputElement | null, value: string) {
  if (!el) throw new Error('missing input')
  const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
  act(() => {
    set.call(el, value)
    el.dispatchEvent(new Event('input', { bubbles: true }))
  })
}
const input = (label: string) => {
  const l = Array.from(document.body.querySelectorAll('label')).find((e) => e.textContent?.trim().startsWith(label))
  const el = l?.htmlFor ? document.getElementById(l.htmlFor) : null
  return (el ?? document.body.querySelector(`input[aria-label="${label}"]`)) as HTMLInputElement | null
}
const tile = (name: string) => byText('button.addprov-tile', name)
const checkBtn = () => byText('button', 'Check') as HTMLButtonElement
const submitBtn = () => byText('button', 'Add provider ·') as HTMLButtonElement | undefined
const rowChecks = () => Array.from(document.body.querySelectorAll('.addprov-row input[type="checkbox"]')) as HTMLInputElement[]

// The key may sit in the password input's value and in the requests; nowhere else.
function expectKeyNowhere() {
  const clone = document.body.cloneNode(true) as HTMLElement
  clone.querySelectorAll('input[type="password"]').forEach((i) => i.removeAttribute('value'))
  expect(clone.innerHTML).not.toContain(KEY)
  expect(clone.textContent).not.toContain(KEY)
  expect(JSON.stringify(toast.mock.calls)).not.toContain(KEY)
  expect(JSON.stringify(qc.getMutationCache().getAll().map((m) => [m.options.mutationKey, m.state]))).not.toContain(KEY)
  expect(JSON.stringify(qc.getQueryCache().getAll().map((q) => q.state.data))).not.toContain(KEY)
}
const deferred = <T,>() => {
  let resolve!: (v: T) => void
  let reject!: (e: unknown) => void
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}
const esc = () => act(() => { document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })) })
const backdrop = () => act(() => { $('.modal-backdrop')!.dispatchEvent(new MouseEvent('mousedown', { bubbles: true })) })
const fail = (msg: string, status = 500) => () => { throw new Hal0Error(msg, { status, code: 'system.error' }) }

async function pickAndCheck(rows = ROWS) {
  box.handlers['/api/providers/probe'] = () => okProbe(rows)
  click(tile('MiniMax'))
  type(input('API key'), KEY)
  click(checkBtn())
  await settle()
}

beforeEach(() => {
  box.posts = []
  box.upstreams = [{ name: 'minimax', auth_value_env: 'MINIMAX_API_KEY' }]
  box.clears = 0
  box.handlers = {
    '/api/upstreams': (b) => ({ name: b.name, auth_value_env: b.auth_value_env }),
  }
  onClose.mockReset()
  onAdded.mockReset()
  toast.mockReset()
  ;(globalThis as unknown as { __hal0Toast: unknown }).__hal0Toast = toast
  qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})
afterEach(() => {
  act(() => root.unmount())
  host.remove()
  document.body.innerHTML = ''
})

describe('AddProviderModal', () => {
  it('renders nothing while closed', () => {
    mount(false)
    expect($('[data-testid="add-provider-modal"]')).toBeNull()
  })

  it('lists a tile per preset, custom last, and only then shows the connection section', async () => {
    mount()
    await settle()
    const names = Array.from(document.body.querySelectorAll('button.addprov-tile')).map((t) => t.querySelector('b')?.textContent)
    expect(names).toEqual(['MiniMax', 'Ollama', 'Custom'])
    expect(document.body.textContent).not.toContain('Check connection')
    click(tile('MiniMax'))
    expect(tile('MiniMax')!.getAttribute('aria-pressed')).toBe('true')
    expect(tile('Ollama')!.getAttribute('aria-pressed')).toBe('false')
    expect(document.body.textContent).toContain('Check connection')
  })

  it('prefills name and URL from the tile and avoids a taken name', async () => {
    mount()
    await settle()
    click(tile('MiniMax'))
    expect(input('Name')!.value).toBe('minimax-2')
    expect(input('Base URL')!.value).toBe('https://api.minimax.io/v1')
    click(tile('Ollama'))
    expect(input('Name')!.value).toBe('ollama')
  })

  it('blocks Check on an invalid name and says why', async () => {
    mount()
    await settle()
    click(tile('MiniMax'))
    type(input('Name'), 'Bad Name')
    expect(checkBtn().disabled).toBe(true)
    expect(document.body.textContent).toContain('Use lowercase letters')
    type(input('Name'), 'minimax')
    expect(document.body.textContent).toContain('A provider with this name already exists.')
    type(input('Name'), 'mm')
    expect(checkBtn().disabled).toBe(false)
    expect(input('Name')!.getAttribute('aria-invalid')).toBe('false')
  })

  it('keeps the key in a password input only: never as text, and not after a rejected key', async () => {
    mount()
    await settle()
    box.handlers['/api/providers/probe'] = () => ({ ok: false, status: 401, latency_ms: 80, models: [], rate_limit: {}, error: 'unauthorized' })
    click(tile('MiniMax'))
    const key = input('API key')!
    expect(key.type).toBe('password')
    expect(key.getAttribute('autocomplete')).toBe('off')
    type(key, KEY)
    click(checkBtn())
    await settle()
    expect(document.body.textContent).toContain('rejected')
    expect(input('API key')!.value).toBe(KEY)
    expect(document.body.textContent).not.toContain(KEY)
    expectKeyNowhere()
    expect(box.posts.map((p) => p.path)).toEqual(['/api/providers/probe'])
    expect(submitBtn()?.disabled).not.toBe(false)
  })

  it('sends the preset id, and the URL only when edited, with the key to the probe', async () => {
    mount()
    await settle()
    await pickAndCheck()
    expect(box.posts[0]).toEqual({
      path: '/api/providers/probe',
      body: { catalog_id: 'minimax', auth_style: 'bearer', key: KEY },
    })
    click(checkBtn())
    type(input('Base URL'), 'https://proxy.example/v1')
    box.posts = []
    click(checkBtn())
    await settle()
    expect(box.posts[0].body).toMatchObject({ catalog_id: 'minimax', url: 'https://proxy.example/v1' })
  })

  it('lists the models with recommended ones ticked and a count in the footer', async () => {
    mount()
    await settle()
    await pickAndCheck()
    expect(document.body.textContent).toContain('Reachable · 212 ms · 3 models offered')
    expect(rowChecks().map((c) => c.checked)).toEqual([true, false, false])
    expect(document.body.textContent).toContain('3 offered · 1 picked')
    expect(submitBtn()!.textContent).toContain('Add provider · 1 on')
    expect(document.activeElement?.getAttribute('data-testid')).toBe('addprov-result')
  })

  it('hides the list when the URL, key or auth changes after a check, but not when only the name does', async () => {
    mount()
    await settle()
    await pickAndCheck()
    type(input('Name'), 'mm-two')
    expect(rowChecks().length).toBe(3)
    type(input('Base URL'), 'https://other.example/v1')
    expect(rowChecks().length).toBe(0)
    expect(document.body.textContent).toContain("Check the connection to see this provider's models.")
    await settle()
    click(checkBtn())
    await settle()
    expect(rowChecks().length).toBe(3)
    type(input('API key'), KEY + 'x')
    expect(rowChecks().length).toBe(0)
  })

  it('supports Recommended only and select all', async () => {
    mount()
    await settle()
    await pickAndCheck()
    const all = document.body.querySelector('#addprov-all') as HTMLInputElement
    click(all)
    expect(rowChecks().map((c) => c.checked)).toEqual([true, true, true])
    click(all)
    expect(rowChecks().map((c) => c.checked)).toEqual([false, false, false])
    click(byText('button', 'Recommended only'))
    expect(rowChecks().map((c) => c.checked)).toEqual([true, false, false])
  })

  it('searches long lists, selects only the filtered rows and counts every pick', async () => {
    const many = Array.from({ length: 250 }, (_, i) => probeRow(i < 10 ? `alpha-${i}` : `m-${String(i).padStart(3, '0')}`))
    mount()
    await settle()
    await pickAndCheck(many)
    expect(rowChecks().length).toBe(100)
    click(byText('button', 'Show more'))
    expect(rowChecks().length).toBe(200)
    type(input('Search models'), 'alpha')
    expect(rowChecks().length).toBe(10)
    click(document.body.querySelector('#addprov-all'))
    expect(document.body.textContent).toContain('250 offered · 10 picked')
    type(input('Search models'), '')
    expect(rowChecks().filter((c) => c.checked).length).toBe(10)
  })

  it('creates then saves the key, with the exact bodies, and no key in the create body', async () => {
    mount()
    await settle()
    await pickAndCheck()
    type(input('Name'), 'mm')
    const sel = document.body.querySelector('select[aria-label="Type of MiniMax-M2"]') as HTMLSelectElement
    act(() => {
      const set = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')!.set!
      set.call(sel, 'embed')
      sel.dispatchEvent(new Event('change', { bubbles: true }))
    })
    box.handlers['/api/providers/mm/credentials'] = () => ({ ok: true })
    box.posts = []
    click(submitBtn())
    await settle()
    expect(box.posts.map((p) => p.path)).toEqual(['/api/upstreams', '/api/providers/mm/credentials'])
    expect(box.posts[0].body).toEqual({
      name: 'mm',
      catalog_id: 'minimax',
      url: 'https://api.minimax.io/v1',
      auth_style: 'bearer',
      auth_value_env: 'MM_API_KEY',
      models: [
        { id: 'MiniMax-M3', enabled: true, type: 'chat', context: 204000, price_in: 0.3, price_out: 1.2, price_unit: 'Mtok' },
        { id: 'MiniMax-M2', enabled: false, type: 'embed', context: 204000, price_in: 0.3, price_out: 1.2, price_unit: 'Mtok' },
        { id: 'speech-01', enabled: false, type: 'tts', context: 204000, price_in: 0.3, price_out: 1.2, price_unit: 'Mtok' },
      ],
    })
    expect(JSON.stringify(box.posts[0].body)).not.toContain(KEY)
    expect(box.posts[1].body).toEqual({ key: 'MM_API_KEY', value: KEY })
    expect(onAdded).toHaveBeenCalledWith('mm')
    expect(onClose).toHaveBeenCalledTimes(1)
    expect(toast).toHaveBeenCalledWith('mm added · 1 models on', 'ok')
    expectKeyNowhere()
  })

  it('allows adding with zero models picked', async () => {
    mount()
    await settle()
    await pickAndCheck()
    click(document.body.querySelector('#addprov-all'))
    click(document.body.querySelector('#addprov-all'))
    expect(rowChecks().every((c) => !c.checked)).toBe(true)
    expect(submitBtn()!.disabled).toBe(false)
    box.handlers['/api/providers/minimax-2/credentials'] = () => ({ ok: true })
    click(submitBtn())
    await settle()
    const create = box.posts.find((p) => p.path === '/api/upstreams')!
    expect((create.body.models as Array<{ enabled: boolean }>).every((m) => !m.enabled)).toBe(true)
    expect(toast).toHaveBeenCalledWith('minimax-2 added · 0 models on', 'ok')
  })

  it('adds without checking: models empty, key still saved, plain toast', async () => {
    mount()
    await settle()
    click(tile('MiniMax'))
    type(input('Name'), 'mm')
    type(input('API key'), KEY)
    box.handlers['/api/providers/mm/credentials'] = () => ({ ok: true })
    click(byText('button', 'Add without checking'))
    await settle()
    expect(box.posts[0]).toEqual({
      path: '/api/upstreams',
      body: { name: 'mm', catalog_id: 'minimax', url: 'https://api.minimax.io/v1', auth_style: 'bearer', auth_value_env: 'MM_API_KEY', models: [] },
    })
    expect(box.posts[1]).toEqual({ path: '/api/providers/mm/credentials', body: { key: 'MM_API_KEY', value: KEY } })
    expect(toast).toHaveBeenCalledWith('mm added. No models on yet: refresh its model list when it is reachable.', 'info')
    expect(onAdded).toHaveBeenCalledWith('mm')
  })

  it('needs a valid name and URL for Add without checking', async () => {
    mount()
    await settle()
    click(tile('Custom'))
    expect((byText('button', 'Add without checking') as HTMLButtonElement).disabled).toBe(true)
    type(input('Name'), 'corp')
    expect((byText('button', 'Add without checking') as HTMLButtonElement).disabled).toBe(true)
    type(input('Base URL'), 'https://corp.example/v1')
    expect((byText('button', 'Add without checking') as HTMLButtonElement).disabled).toBe(false)
  })

  it('custom provider: bearer by default, header name required, no catalog id, own key variable', async () => {
    mount()
    await settle()
    click(tile('Custom'))
    type(input('Name'), 'corp-gw')
    type(input('Base URL'), 'https://corp.example/v1')
    const auth = input('Auth') as unknown as HTMLSelectElement
    expect(auth.value).toBe('bearer')
    act(() => {
      Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')!.set!.call(auth, 'header')
      auth.dispatchEvent(new Event('change', { bubbles: true }))
    })
    expect(checkBtn().disabled).toBe(true)
    expect(document.body.textContent).toContain('Enter the header name')
    type(input('Header name'), 'X-Api-Key')
    expect(checkBtn().disabled).toBe(false)
    type(input('API key'), KEY)
    box.handlers['/api/providers/probe'] = () => okProbe()
    click(checkBtn())
    await settle()
    expect(box.posts[0].body).toEqual({ url: 'https://corp.example/v1', auth_style: 'header', auth_header: 'X-Api-Key', key: KEY })
    box.handlers['/api/providers/corp-gw/credentials'] = () => ({ ok: true })
    click(submitBtn())
    await settle()
    expect(box.posts[1].body).toMatchObject({
      name: 'corp-gw', url: 'https://corp.example/v1', auth_style: 'header', auth_header: 'X-Api-Key', auth_value_env: 'CORP_GW_API_KEY',
    })
    expect(box.posts[1].body).not.toHaveProperty('catalog_id')
  })

  it('labels the key optional when the preset needs none', async () => {
    mount()
    await settle()
    click(tile('Ollama'))
    expect(document.body.textContent).toContain('API key (optional)')
  })

  it('shows the server message for a 400 from the probe next to the key field', async () => {
    mount()
    await settle()
    box.handlers['/api/providers/probe'] = () => {
      throw new Hal0Error('key must be printable ASCII without whitespace', { status: 400, code: 'upstream.invalid' })
    }
    click(tile('MiniMax'))
    type(input('API key'), KEY)
    click(checkBtn())
    await settle()
    const err = $('#addprov-err-key')
    expect(err?.textContent).toContain('key must be printable ASCII without whitespace')
    expect(input('API key')!.getAttribute('aria-describedby')).toContain('addprov-err-key')
  })

  it('keeps the modal open on a 409 and shows the message under Name', async () => {
    mount()
    await settle()
    await pickAndCheck()
    box.handlers['/api/upstreams'] = () => {
      throw new Hal0Error('upstream "minimax-2" already exists', { status: 409, code: 'upstream.conflict' })
    }
    click(submitBtn())
    await settle()
    expect($('#addprov-err-name')?.textContent).toContain('already exists')
    expect(onAdded).not.toHaveBeenCalled()
    expect(onClose).not.toHaveBeenCalled()
    expect(box.posts.map((p) => p.path)).toEqual(['/api/providers/probe', '/api/upstreams'])
  })

  it('shows a form error for other create failures and stays open', async () => {
    mount()
    await settle()
    await pickAndCheck()
    box.handlers['/api/upstreams'] = () => {
      throw new Hal0Error('invalid upstream configuration', { status: 400, code: 'upstream.invalid' })
    }
    click(submitBtn())
    await settle()
    expect($('[data-testid="addprov-form-error"]')?.textContent).toContain('invalid upstream configuration')
    expect(onClose).not.toHaveBeenCalled()
  })

  it('after a failed key write offers retry (credentials only) and Close reports the provider', async () => {
    mount()
    await settle()
    await pickAndCheck()
    type(input('Name'), 'mm')
    box.handlers['/api/providers/mm/credentials'] = () => {
      throw new Hal0Error('failed to write api.env', { status: 500, code: 'system.error' })
    }
    click(submitBtn())
    await settle()
    expect(document.body.textContent).toContain('Provider added, but the key was not saved.')
    expect(byText('button', 'Add provider ·')).toBeUndefined()
    expect(onAdded).not.toHaveBeenCalled()
    box.posts = []
    box.handlers['/api/providers/mm/credentials'] = () => ({ ok: true })
    click(byText('button', 'Try saving the key again'))
    await settle()
    expect(box.posts.map((p) => p.path)).toEqual(['/api/providers/mm/credentials'])
    expect(onAdded).toHaveBeenCalledWith('mm')
    expect(onClose).toHaveBeenCalled()
  })

  it('Close after a failed key write calls onAdded so the list shows the provider', async () => {
    mount()
    await settle()
    await pickAndCheck()
    type(input('Name'), 'mm')
    box.handlers['/api/providers/mm/credentials'] = () => {
      throw new Hal0Error('nope', { status: 500 })
    }
    click(submitBtn())
    await settle()
    click(byText('button', 'Close'))
    expect(onAdded).toHaveBeenCalledWith('mm')
    expect(onClose).toHaveBeenCalled()
  })

  it('asks before closing with typed data and closes without asking when nothing was typed', async () => {
    mount()
    await settle()
    click($('button[aria-label="Close"]'))
    expect(onClose).toHaveBeenCalledTimes(1)
    mount()
    click(tile('MiniMax'))
    click($('button[aria-label="Close"]'))
    expect(onClose).toHaveBeenCalledTimes(1)
    expect(document.body.textContent).toContain('Discard')
  })

  it('Cancel asks before discarding typed data and closes on confirm', async () => {
    mount()
    await settle()
    click(tile('MiniMax'))
    click(byText('button', 'Cancel'))
    expect(onClose).not.toHaveBeenCalled()
    expect(document.body.textContent).toContain('Discard this provider?')
    click(byText('button', 'Keep editing'))
    expect(onClose).not.toHaveBeenCalled()
    click(byText('button', 'Cancel'))
    click(byText('button', 'Discard'))
    expect(onClose).toHaveBeenCalledTimes(1)
    expect(onAdded).not.toHaveBeenCalled()
  })

  it('clears the key and the check when closed and reopened', async () => {
    mount()
    await settle()
    await pickAndCheck()
    mount(false)
    mount(true)
    await settle()
    expect(document.body.querySelector('input[type="password"]')).toBeNull()
    click(tile('MiniMax'))
    expect(input('API key')!.value).toBe('')
    expect(rowChecks().length).toBe(0)
  })

  it('clears the key when another tile is picked', async () => {
    mount()
    await settle()
    click(tile('MiniMax'))
    type(input('API key'), KEY)
    click(tile('Ollama'))
    expect(input('API key')!.value).toBe('')
  })

  it('closing mid-check causes no unhandled rejection and no late state update', async () => {
    const unhandled = vi.fn()
    const proc = (globalThis as unknown as { process: { on: (e: string, f: () => void) => void; off: (e: string, f: () => void) => void } }).process
    proc.on('unhandledRejection', unhandled)
    const errors = vi.spyOn(console, 'error').mockImplementation(() => {})
    let fail: (e: Error) => void = () => {}
    box.handlers['/api/providers/probe'] = () => new Promise((_, rej) => { fail = rej })
    mount()
    await settle()
    click(tile('MiniMax'))
    type(input('API key'), KEY)
    click(checkBtn())
    expect(checkBtn().textContent).toContain('Checking…')
    mount(false)
    mount(true)
    await settle()
    fail(new Hal0Error('connection refused', { status: 502 }))
    await settle()
    await new Promise((r) => setTimeout(r, 10))
    expect(unhandled).not.toHaveBeenCalled()
    expect(errors).not.toHaveBeenCalled()
    expect(document.body.textContent).not.toContain('connection refused')
    proc.off('unhandledRejection', unhandled)
    errors.mockRestore()
  })

  // ---- review round 1 -------------------------------------------------------------------

  async function toKeyFailed(message = 'failed to write api.env') {
    mount()
    await settle()
    await pickAndCheck()
    type(input('Name'), 'mm')
    box.handlers['/api/providers/mm/credentials'] = fail(message)
    click(submitBtn())
    await settle()
  }

  it('cannot be closed while the create request is in flight, and reports once when it lands', async () => {
    const d = deferred<unknown>()
    box.handlers['/api/upstreams'] = () => d.promise
    box.handlers['/api/providers/mm/credentials'] = () => ({ ok: true })
    mount()
    await settle()
    await pickAndCheck()
    type(input('Name'), 'mm')
    click(submitBtn())
    expect($('button[aria-label="Close"]')).toBeNull()
    expect((byText('button', 'Cancel') as HTMLButtonElement).disabled).toBe(true)
    expect(submitBtn()).toBeUndefined()
    expect(byText('button', 'Adding…')).toBeDefined()
    esc()
    backdrop()
    expect(onClose).not.toHaveBeenCalled()
    expect(onAdded).not.toHaveBeenCalled()
    d.resolve({ name: 'mm', auth_value_env: 'MM_API_KEY' })
    await settle()
    expect(onAdded).toHaveBeenCalledTimes(1)
    expect(onClose).toHaveBeenCalledTimes(1)
    expect(toast).toHaveBeenCalledTimes(1)
  })

  it('cannot be closed while the key is being saved: success reports once', async () => {
    const d = deferred<unknown>()
    mount()
    await settle()
    await pickAndCheck()
    type(input('Name'), 'mm')
    box.handlers['/api/providers/mm/credentials'] = () => d.promise
    click(submitBtn())
    await settle()
    esc()
    backdrop()
    expect($('button[aria-label="Close"]')).toBeNull()
    expect((byText('button', 'Close') as HTMLButtonElement | undefined)?.disabled ?? true).toBe(true)
    expect(onClose).not.toHaveBeenCalled()
    expect(onAdded).not.toHaveBeenCalled()
    d.resolve({ ok: true })
    await settle()
    expect(onAdded).toHaveBeenCalledTimes(1)
    expect(onClose).toHaveBeenCalledTimes(1)
    expect(toast).toHaveBeenCalledTimes(1)
    expect(toast).toHaveBeenCalledWith('mm added · 1 models on', 'ok')
  })

  it('cannot be closed while the key is being saved: failure leaves the modal open, then Close reports once', async () => {
    const d = deferred<unknown>()
    mount()
    await settle()
    await pickAndCheck()
    type(input('Name'), 'mm')
    box.handlers['/api/providers/mm/credentials'] = () => d.promise
    click(submitBtn())
    await settle()
    esc()
    expect(onClose).not.toHaveBeenCalled()
    d.reject(new Hal0Error('failed to write api.env', { status: 500 }))
    await settle()
    expect(document.body.textContent).toContain('Provider added, but the key was not saved.')
    expect(onAdded).not.toHaveBeenCalled()
    expect(onClose).not.toHaveBeenCalled()
    click(byText('button', 'Close'))
    expect(onAdded).toHaveBeenCalledTimes(1)
    expect(onClose).toHaveBeenCalledTimes(1)
    expect(toast).toHaveBeenCalledTimes(1)
    expect(toast).toHaveBeenCalledWith('mm added, but its key was not saved.', 'info')
  })

  it.each([
    ['create resolves', 'create', 'resolve'],
    ['create rejects', 'create', 'reject'],
    ['key save resolves', 'creds', 'resolve'],
    ['key save rejects', 'creds', 'reject'],
  ])('unmounting mid-request (%s) causes no callback, no state update and no unhandled rejection', async (_l, stage, how) => {
    const proc = (globalThis as unknown as { process: { on: (e: string, f: () => void) => void; off: (e: string, f: () => void) => void } }).process
    const unhandled = vi.fn()
    proc.on('unhandledRejection', unhandled)
    const errors = vi.spyOn(console, 'error').mockImplementation(() => {})
    const d = deferred<unknown>()
    mount()
    await settle()
    await pickAndCheck()
    type(input('Name'), 'mm')
    if (stage === 'create') box.handlers['/api/upstreams'] = () => d.promise
    else box.handlers['/api/providers/mm/credentials'] = () => d.promise
    click(submitBtn())
    await settle()
    act(() => root.unmount())
    root = createRoot(host)
    if (how === 'resolve') d.resolve({ name: 'mm', auth_value_env: 'MM_API_KEY' })
    else d.reject(new Hal0Error('boom', { status: 500 }))
    await settle()
    await new Promise((r) => setTimeout(r, 10))
    expect(onAdded).not.toHaveBeenCalled()
    expect(onClose).not.toHaveBeenCalled()
    expect(toast).not.toHaveBeenCalled()
    expect(unhandled).not.toHaveBeenCalled()
    expect(errors).not.toHaveBeenCalled()
    proc.off('unhandledRejection', unhandled)
    errors.mockRestore()
  })

  it('starts clean when reopened after a key-failed close', async () => {
    await toKeyFailed()
    click(byText('button', 'Close'))
    mount(false)
    mount(true)
    await settle()
    expect(document.body.textContent).not.toContain('Provider added, but the key was not saved.')
    expect(document.body.querySelectorAll('button.addprov-tile').length).toBe(3)
    expect(document.body.querySelector('input[type="password"]')).toBeNull()
  })

  it('shows the credentials error under the key, keeps the key editable, and a failed retry updates it', async () => {
    await toKeyFailed('key must be printable ASCII without whitespace')
    const err = () => $('#addprov-err-key')
    expect(err()?.textContent).toContain('key must be printable ASCII without whitespace')
    expect(input('API key')!.getAttribute('aria-describedby')).toContain('addprov-err-key')
    expect(input('API key')!.disabled).toBe(false)
    expect(input('API key')!.type).toBe('password')
    expectKeyNowhere()
    box.handlers['/api/providers/mm/credentials'] = fail('upstream "mm" is managed by agent:hermes', 409)
    box.posts = []
    click(byText('button', 'Try saving the key again'))
    await settle()
    expect(err()?.textContent).toContain('managed by agent:hermes')
    expect(document.activeElement).toBe(err())
    expect(onAdded).not.toHaveBeenCalled()
    expectKeyNowhere()
  })

  it('a corrected key saved on retry succeeds; credentials only; onAdded once', async () => {
    await toKeyFailed()
    type(input('API key'), KEY + '-fixed')
    box.handlers['/api/providers/mm/credentials'] = () => ({ ok: true })
    box.posts = []
    click(byText('button', 'Try saving the key again'))
    await settle()
    expect(box.posts).toEqual([{ path: '/api/providers/mm/credentials', body: { key: 'MM_API_KEY', value: KEY + '-fixed' } }])
    expect(onAdded).toHaveBeenCalledTimes(1)
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('a retry with an invalid or empty key is refused client-side', async () => {
    await toKeyFailed()
    box.posts = []
    type(input('API key'), 'two words')
    click(byText('button', 'Try saving the key again'))
    await settle()
    expect($('#addprov-err-key')?.textContent).toContain('printable ASCII')
    type(input('API key'), '')
    click(byText('button', 'Try saving the key again'))
    expect($('#addprov-err-key')?.textContent).toContain('Enter the key')
    expect(box.posts).toEqual([])
  })

  it('once the provider exists, only the key and the footer buttons act', async () => {
    await toKeyFailed()
    expect(document.body.querySelectorAll('button.addprov-tile').length).toBe(0)
    expect(input('Name')).toBeNull()
    expect(input('Base URL')).toBeNull()
    expect(byText('button', 'Check connection')).toBeUndefined()
    expect(byText('button', 'Add without checking')).toBeUndefined()
    expect(rowChecks().length).toBe(0)
    expect($('[data-testid="addprov-summary"]')?.textContent).toContain('mm')
    expect(input('API key')).not.toBeNull()
  })

  it('blocks a key that can never be saved, on Check and on Add without checking', async () => {
    mount()
    await settle()
    click(tile('MiniMax'))
    type(input('Name'), 'mm')
    type(input('API key'), 'two words')
    expect(document.body.textContent).toContain('The key must be printable ASCII without spaces.')
    expect(checkBtn().disabled).toBe(true)
    expect((byText('button', 'Add without checking') as HTMLButtonElement).disabled).toBe(true)
    type(input('API key'), 'Bearer abc123')
    expect((byText('button', 'Add without checking') as HTMLButtonElement).disabled).toBe(false)
  })

  it('stores each key under a variable named after the provider (minimax-2 never shares minimax)', async () => {
    mount()
    await settle()
    await pickAndCheck()
    box.handlers['/api/providers/minimax-2/credentials'] = () => ({ ok: true })
    click(submitBtn())
    await settle()
    expect(box.posts[1].path).toBe('/api/upstreams')
    expect(box.posts[1].body.auth_value_env).toBe('MINIMAX_2_API_KEY')
    expect(box.posts[2]).toEqual({ path: '/api/providers/minimax-2/credentials', body: { key: 'MINIMAX_2_API_KEY', value: KEY } })
  })

  it('prefixes PROVIDER_ when the derived variable would be reserved', async () => {
    mount()
    await settle()
    click(tile('Custom'))
    type(input('Name'), 'python-tools')
    type(input('Base URL'), 'https://corp.example/v1')
    type(input('API key'), KEY)
    box.handlers['/api/providers/python-tools/credentials'] = () => ({ ok: true })
    click(byText('button', 'Add without checking'))
    await settle()
    expect(box.posts[0].body.auth_value_env).toBe('PROVIDER_PYTHON_TOOLS_API_KEY')
    expect(box.posts[1].body.key).toBe('PROVIDER_PYTHON_TOOLS_API_KEY')
  })

  it('omits auth_value_env when no key is typed', async () => {
    mount()
    await settle()
    click(tile('Ollama'))
    click(byText('button', 'Add without checking'))
    await settle()
    expect(box.posts).toHaveLength(1)
    expect(box.posts[0].body).not.toHaveProperty('auth_value_env')
  })

  it('adds a numeric suffix when another provider already uses the derived variable', async () => {
    box.upstreams = [{ name: 'corp-gw', auth_value_env: 'CORP_GW_API_KEY' }]
    mount()
    await settle()
    click(tile('Custom'))
    type(input('Name'), 'corp_gw')
    type(input('Base URL'), 'https://corp.example/v1')
    type(input('API key'), KEY)
    box.handlers['/api/providers/corp_gw/credentials'] = () => ({ ok: true })
    click(byText('button', 'Add without checking'))
    await settle()
    expect(box.posts[0].body.auth_value_env).toBe('CORP_GW_2_API_KEY')
  })

  it('routes a header message to the header field and an unknown 400 to a form error', async () => {
    mount()
    await settle()
    click(tile('Custom'))
    type(input('Name'), 'corp')
    type(input('Base URL'), 'https://corp.example/v1')
    const auth = input('Auth') as unknown as HTMLSelectElement
    act(() => {
      Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')!.set!.call(auth, 'header')
      auth.dispatchEvent(new Event('change', { bubbles: true }))
    })
    type(input('Header name'), 'X-Api-Key')
    box.handlers['/api/providers/probe'] = fail("auth_style 'header' needs an auth_header name", 400)
    click(checkBtn())
    await settle()
    expect($('#addprov-err-header')?.textContent).toContain("needs an auth_header name")
    expect($('#addprov-err-url')).toBeNull()
    box.handlers['/api/providers/probe'] = fail('unknown catalog_id', 400)
    click(checkBtn())
    await settle()
    expect($('[data-testid="addprov-form-error"]')?.textContent).toContain('unknown catalog_id')
    box.handlers['/api/providers/probe'] = fail('url must be an http:// or https:// URL with a host', 400)
    click(checkBtn())
    await settle()
    expect($('#addprov-err-url')?.textContent).toContain('url must be')
  })

  it('a repeat check with nothing edited keeps the picks and type overrides that still exist', async () => {
    mount()
    await settle()
    await pickAndCheck()
    click(rowChecks()[1])
    click(rowChecks()[0])
    const sel = document.body.querySelector('select[aria-label="Type of speech-01"]') as HTMLSelectElement
    act(() => {
      Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')!.set!.call(sel, 'stt')
      sel.dispatchEvent(new Event('change', { bubbles: true }))
    })
    box.handlers['/api/providers/probe'] = () => okProbe([ROWS[0], ROWS[1], ROWS[2]])
    click(checkBtn())
    await settle()
    expect(rowChecks().map((c) => c.checked)).toEqual([false, true, false])
    expect((document.body.querySelector('select[aria-label="Type of speech-01"]') as HTMLSelectElement).value).toBe('stt')
  })

  it('titles the price by its own unit and labels select-all by what it applies to', async () => {
    mount()
    await settle()
    await pickAndCheck([
      probeRow('chat-a'),
      probeRow('img-a', { type: 'image', price_unit: 'img', price_in: 0.04, price_out: null }),
      probeRow('tts-a', { type: 'tts', price_unit: 'Mchar' }),
    ])
    const titles = Array.from(document.body.querySelectorAll('.addprov-row .addprov-num[title^="Price"]')).map((e) => e.getAttribute('title'))
    expect(titles).toEqual([
      'Price, in / out, per million tokens',
      'Price, in / out, per image',
      'Price, in / out, per million characters',
    ])
    const all = $('#addprov-all')!
    expect(all.getAttribute('aria-label')).toBeNull()
    expect(document.body.querySelector('label[for="addprov-all"]')?.textContent).toBe('Select all')
  })

  it('calls the probe hook clear() when the modal closes', async () => {
    mount()
    await settle()
    await pickAndCheck()
    const before = box.clears
    mount(false)
    expect(box.clears).toBeGreaterThan(before)
  })

  it('keeps the key out of every cache and out of the DOM after a rejected key', async () => {
    mount()
    await settle()
    box.handlers['/api/providers/probe'] = () => ({ ok: false, status: 403, latency_ms: 50, models: [], rate_limit: {}, error: null })
    click(tile('MiniMax'))
    type(input('API key'), KEY)
    click(checkBtn())
    await settle()
    expect(document.body.textContent).toContain('rejected')
    expectKeyNowhere()
  })

  // ---- review round 2 -------------------------------------------------------------------

  it.each([
    ['git', 'PROVIDER_GIT_API_KEY'],
    ['uv', 'PROVIDER_UV_API_KEY'],
    ['pip', 'PROVIDER_PIP_API_KEY'],
    ['ld', 'PROVIDER_LD_API_KEY'],
    ['xdg', 'PROVIDER_XDG_API_KEY'],
    ['openssl', 'PROVIDER_OPENSSL_API_KEY'],
    ['npm-config', 'PROVIDER_NPM_CONFIG_API_KEY'],
    ['hf-hub', 'PROVIDER_HF_HUB_API_KEY'],
    ['hf-xet', 'PROVIDER_HF_XET_API_KEY'],
    ['huggingface-hub', 'PROVIDER_HUGGINGFACE_HUB_API_KEY'],
    ['dyld', 'PROVIDER_DYLD_API_KEY'],
    ['hal0-x', 'PROVIDER_HAL0_X_API_KEY'],
    ['python-tools', 'PROVIDER_PYTHON_TOOLS_API_KEY'],
    ['9lives', 'PROVIDER_9LIVES_API_KEY'],
    ['acme', 'ACME_API_KEY'],
    ['hal0x', 'HAL0X_API_KEY'],
  ])('derives the key variable for %s as %s (tested against the final name)', async (provider, env) => {
    mount()
    await settle()
    click(tile('Custom'))
    type(input('Name'), provider)
    type(input('Base URL'), 'https://corp.example/v1')
    type(input('API key'), KEY)
    box.handlers[`/api/providers/${provider}/credentials`] = () => ({ ok: true })
    click(byText('button', 'Add without checking'))
    await settle()
    expect(box.posts[0].body.auth_value_env).toBe(env)
    expect(box.posts[1].body.key).toBe(env)
  })

  it('sends the name-derived variable even when no key is typed, except for auth none', async () => {
    mount()
    await settle()
    click(tile('MiniMax'))
    type(input('Name'), 'mm')
    click(byText('button', 'Add without checking'))
    await settle()
    expect(box.posts).toHaveLength(1)
    expect(box.posts[0].body.auth_value_env).toBe('MM_API_KEY')
    mount(false)
    mount(true)
    await settle()
    box.posts = []
    click(tile('Ollama'))
    click(byText('button', 'Add without checking'))
    await settle()
    expect(box.posts[0].body).not.toHaveProperty('auth_value_env')
  })

  it('shows a create 400 about the key variable as a form error, not under Name', async () => {
    mount()
    await settle()
    await pickAndCheck()
    box.handlers['/api/upstreams'] = fail("auth_value_env 'X' is reserved (process_control) and cannot name an upstream credential", 400)
    click(submitBtn())
    await settle()
    expect($('[data-testid="addprov-form-error"]')?.textContent).toContain('reserved')
    expect($('#addprov-err-name')).toBeNull()
  })

  it('stops waiting for a create after 30 s: dismissable, nothing reported, list refreshed on close', async () => {
    box.handlers['/api/upstreams'] = () => new Promise(() => {})
    mount()
    await settle()
    await pickAndCheck()
    type(input('Name'), 'mm')
    const spy = vi.spyOn(qc, 'invalidateQueries')
    vi.useFakeTimers()
    try {
      click(submitBtn())
      expect($('button[aria-label="Close"]')).toBeNull()
      await act(async () => { vi.advanceTimersByTime(29_000) })
      expect($('button[aria-label="Close"]')).toBeNull()
      await act(async () => { vi.advanceTimersByTime(1_500) })
    } finally {
      vi.useRealTimers()
    }
    expect($('[data-testid="addprov-form-error"]')?.textContent).toContain(
      'hal0 did not answer. The provider may or may not have been added; close this and check the list.',
    )
    expect($('button[aria-label="Close"]')).not.toBeNull()
    expect(onAdded).not.toHaveBeenCalled()
    expectKeyNowhere()
    esc()
    expect(onClose).toHaveBeenCalledTimes(1)
    expect(onAdded).not.toHaveBeenCalled()
    expect(spy).toHaveBeenCalledWith({ queryKey: ['upstreams'] })
    expect(toast).not.toHaveBeenCalled()
  })

  it('stops waiting for the key save after 30 s: key-failed state with retry, dismissable', async () => {
    mount()
    await settle()
    await pickAndCheck()
    type(input('Name'), 'mm')
    box.handlers['/api/providers/mm/credentials'] = () => new Promise(() => {})
    vi.useFakeTimers()
    try {
      click(submitBtn())
      await act(async () => { await Promise.resolve() })
      await act(async () => { vi.advanceTimersByTime(30_500) })
    } finally {
      vi.useRealTimers()
    }
    expect(document.body.textContent).toContain('Provider added, but the key was not saved.')
    expect($('#addprov-err-key')?.textContent).toContain('hal0 did not answer while saving the key.')
    expect((byText('button', 'Try saving the key again') as HTMLButtonElement).disabled).toBe(false)
    expect($('button[aria-label="Close"]')).not.toBeNull()
    expect(onAdded).not.toHaveBeenCalled()
    expectKeyNowhere()
  })

  it('rejects a bare or oddly spaced Bearer prefix with the fixed message', async () => {
    mount()
    await settle()
    click(tile('MiniMax'))
    for (const bad of ['Bearer ', 'Bearer', 'Bearer  abc']) {
      type(input('API key'), bad)
      expect(document.body.textContent).toContain('The key must be printable ASCII without spaces.')
      expect(checkBtn().disabled).toBe(true)
    }
    type(input('API key'), 'Bearer abc')
    expect(document.body.textContent).not.toContain('The key must be printable ASCII without spaces.')
  })

  it('disables the model controls while the create is in flight', async () => {
    const d = deferred<unknown>()
    box.handlers['/api/upstreams'] = () => d.promise
    mount()
    await settle()
    await pickAndCheck()
    click(submitBtn())
    expect(rowChecks().every((c) => c.disabled)).toBe(true)
    expect((document.body.querySelector('select[aria-label="Type of MiniMax-M3"]') as HTMLSelectElement).disabled).toBe(true)
    expect((byText('button', 'Recommended only') as HTMLButtonElement).disabled).toBe(true)
    expect((($('#addprov-all')) as HTMLInputElement).disabled).toBe(true)
    d.reject(new Hal0Error('x', { status: 500 }))
    await settle()
  })
})
