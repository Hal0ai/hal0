// @vitest-environment happy-dom
//
// Delegated runs (Hermes card ▸ Delegated tab). A real render + click test over the real hook
// and the real agents-hook-bridge; only the API client is mocked, at its boundary.
//
// Pinned here: a row per run (short id with the full one on hover, client, agent or
// agent/profile, started, duration), the status chip's colour class by group, the client and
// status filters, the Events button's `hal0:open-events` dispatch, the empty state and the 503.
import React, { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

;(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
;(globalThis as unknown as { React: typeof React }).React = React

vi.mock('@/api/client', async () => {
  const actual = await vi.importActual<typeof import('@/api/client')>('@/api/client')
  return { ...actual, apiGet: vi.fn(), apiPost: vi.fn(), apiPatch: vi.fn(), apiDelete: vi.fn() }
})

const client = await import('@/api/client')
await import('../agents-hook-bridge')
await import('../bridge-runs.jsx')

const NOW_S = Math.floor(Date.now() / 1000)

const run = (over: Record<string, unknown>) => ({
  run_id: 'run_000000000001',
  agent: 'hermes',
  profile: null,
  client: 'laptop',
  status: 'running',
  created_at: NOW_S - 300,
  updated_at: NOW_S - 290,
  input_preview: 'fix the failing build',
  output: null,
  error: null,
  usage: null,
  pending: null,
  last_seq: 0,
  ...over,
})

const RUNS = [
  run({ run_id: 'run_aaaaaaaaaaaa_long_tail', status: 'running' }),
  run({ run_id: 'run_bbbbbbbbbbbb', status: 'waiting_for_approval', client: 'phone', profile: 'coder' }),
  run({ run_id: 'run_cccccccccccc', status: 'completed', created_at: NOW_S - 400, updated_at: NOW_S - 275 }),
  run({ run_id: 'run_dddddddddddd', status: 'failed', client: 'phone' }),
  run({ run_id: 'run_eeeeeeeeeeee', status: 'interrupted' }),
]

let root: Root | null = null
let container: HTMLElement

async function flush() {
  for (let i = 0; i < 6; i++) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0))
    })
  }
}

async function mount() {
  const List = (window as unknown as { BridgeRunsList: React.ComponentType }).BridgeRunsList
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  await act(async () => {
    root!.render(
      <QueryClientProvider client={qc}>
        <List />
      </QueryClientProvider>,
    )
  })
  await flush()
}

const q = (sel: string) => container.querySelector(sel) as HTMLElement | null
const qa = (sel: string) => Array.from(container.querySelectorAll(sel)) as HTMLElement[]
const row = (id: string) => q(`[data-testid="bridge-run-${id}"]`)!

async function choose(select: HTMLSelectElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')!.set!
  await act(async () => {
    setter.call(select, value)
    select.dispatchEvent(new Event('change', { bubbles: true }))
  })
}

beforeEach(() => {
  for (const fn of [client.apiGet, client.apiPost, client.apiPatch, client.apiDelete]) vi.mocked(fn).mockReset()
  vi.mocked(client.apiGet).mockResolvedValue({ items: RUNS })
})

afterEach(() => {
  if (root) act(() => root!.unmount())
  root = null
  document.body.innerHTML = ''
})

describe('BridgeRunsList', () => {
  it('reads the runs route and renders a row per run with the fields an operator scans', async () => {
    await mount()
    expect(client.apiGet).toHaveBeenCalledWith('/api/bridge/runs?limit=100')
    expect(qa('.brg-run')).toHaveLength(5)

    const first = row('run_aaaaaaaaaaaa_long_tail')
    // first 12 characters, the whole id on hover
    expect(first.querySelector('.brg-run-id')!.textContent).toBe('run_aaaaaaaa')
    expect(first.querySelector('.brg-run-id')!.getAttribute('title')).toBe('run_aaaaaaaaaaaa_long_tail')
    expect(first.querySelector('.brg-run-client')!.textContent).toBe('laptop')
    expect(first.querySelector('.brg-run-meta')!.textContent).toContain('hermes')
    expect(first.querySelector('.brg-run-when')!.textContent).toBe('5m ago')
    expect(first.querySelector('.brg-run-in')!.textContent).toBe('fix the failing build')
    // a live run's duration is now − created
    expect(first.querySelector('.brg-run-meta')!.textContent).toMatch(/·5m \d+s$/)

    // a named profile reads agent/profile
    expect(row('run_bbbbbbbbbbbb').querySelector('.brg-run-meta')!.textContent).toContain('hermes/coder')
    // a finished run's duration is updated − created, and stops moving
    expect(row('run_cccccccccccc').querySelector('.brg-run-meta')!.textContent).toMatch(/·2m 5s$/)
  })

  it('colours the status chip by group', async () => {
    await mount()
    const chip = (id: string) => row(id).querySelector('[data-testid="bridge-run-status"]') as HTMLElement
    const expected: Array<[string, string, string, string]> = [
      ['run_aaaaaaaaaaaa_long_tail', 'live', 'info', 'running'],
      ['run_bbbbbbbbbbbb', 'waiting', 'warn', 'waiting for approval'],
      ['run_cccccccccccc', 'ok', 'ok', 'completed'],
      ['run_dddddddddddd', 'bad', 'err', 'failed'],
      ['run_eeeeeeeeeeee', 'bad', 'err', 'interrupted'],
    ]
    for (const [id, group, cls, label] of expected) {
      const el = chip(id)
      expect(el.getAttribute('data-group')).toBe(group)
      expect(el.classList.contains('chip')).toBe(true)
      expect(el.classList.contains(cls)).toBe(true)
      expect(el.textContent).toBe(label)
    }
  })

  it('filters by client and by status group', async () => {
    await mount()
    const [clientSel, statusSel] = qa('select') as HTMLSelectElement[]
    expect(Array.from(clientSel.options).map((o) => o.value)).toEqual(['', 'laptop', 'phone'])

    await choose(clientSel, 'phone')
    expect(qa('.brg-run').map((r) => r.getAttribute('data-testid'))).toEqual([
      'bridge-run-run_bbbbbbbbbbbb',
      'bridge-run-run_dddddddddddd',
    ])

    await choose(statusSel, 'bad')
    expect(qa('.brg-run').map((r) => r.getAttribute('data-testid'))).toEqual(['bridge-run-run_dddddddddddd'])

    await choose(statusSel, 'ok')
    expect(qa('.brg-run')).toHaveLength(0)
    expect(q('[data-testid="bridge-runs"]')!.textContent).toContain('No runs match these filters')

    await choose(clientSel, '')
    expect(qa('.brg-run').map((r) => r.getAttribute('data-testid'))).toEqual(['bridge-run-run_cccccccccccc'])
  })

  it('the Events button opens the dock\'s events tab, saying which run asked', async () => {
    await mount()
    const seen: CustomEvent[] = []
    const onOpen = (e: Event) => seen.push(e as CustomEvent)
    window.addEventListener('hal0:open-events', onOpen)
    try {
      const btn = Array.from(row('run_bbbbbbbbbbbb').querySelectorAll('button')).find((b) => b.textContent === 'Events')!
      await act(async () => {
        btn.click()
      })
    } finally {
      window.removeEventListener('hal0:open-events', onOpen)
    }
    expect(seen).toHaveLength(1)
    expect(seen[0].detail).toEqual({ source: 'bridge', run_id: 'run_bbbbbbbbbbbb', client: 'phone' })
  })

  it('does not let a click inside the list flip the card it sits on', async () => {
    const flip = vi.fn()
    const List = (window as unknown as { BridgeRunsList: React.ComponentType }).BridgeRunsList
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
    // the card root flips on any click that reaches it
    await act(async () => {
      root!.render(
        <QueryClientProvider client={qc}>
          <div onClick={flip}>
            <List />
          </div>
        </QueryClientProvider>,
      )
    })
    await flush()
    await act(async () => {
      q('[data-testid="bridge-runs"]')!.click()
    })
    const events = Array.from(container.querySelectorAll('button')).find((b) => b.textContent === 'Events')!
    await act(async () => {
      events.click()
    })
    expect(flip).not.toHaveBeenCalled()
  })

  it('says so when nothing has been delegated yet', async () => {
    vi.mocked(client.apiGet).mockResolvedValue({ items: [] })
    await mount()
    expect(q('[data-testid="bridge-runs"]')!.textContent).toContain('No delegated runs yet')
  })

  it('says the service is unavailable on a 503', async () => {
    vi.mocked(client.apiGet).mockRejectedValue(
      new client.Hal0Error('the agent bridge is shutting down', { code: 'bridge.unavailable', status: 503 }),
    )
    await mount()
    expect(q('[data-testid="bridge-runs"]')!.textContent).toContain('Bridge service unavailable')
  })
})
