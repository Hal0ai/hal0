// @vitest-environment happy-dom
//
// The dock's events pane and ticker: the journal ring and the activity trail merged into one
// timeline. Both streams are mocked at the module boundary with settable rings; what is pinned
// is the DOM the operator reads — row order, stream tags, filter narrowing, the activity detail,
// the unseen badge, the export link and the slot-event dispatch — not the SSE plumbing.
import React, { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ActivityRecord } from '@/api/hooks/useActivity'
import type { JournalEntry } from '@/api/hooks/useLogs'

;(globalThis as unknown as { React: typeof React }).React = React
;(globalThis as unknown as { Icons: unknown }).Icons = new Proxy({}, { get: () => null })
;(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

type Json = Record<string, unknown>

const { box } = vi.hoisted(() => ({
  box: {
    ring: [] as unknown[],
    records: [] as unknown[],
    disconnected: false,
    slots: [] as unknown[],
    /** The `follow` option of every call to the two stream hooks, latest last. */
    logsFollow: [] as unknown[],
    activityFollow: [] as unknown[],
  },
}))

vi.mock('@/api/hooks/useLogs', async (importOriginal) => ({
  ...(await importOriginal<Json>()),
  useLogsStream: (opts: { follow?: boolean } = {}) => (
    box.logsFollow.push(opts.follow),
    { ring: box.ring, disconnected: box.disconnected, degraded: false }
  ),
}))
vi.mock('@/api/hooks/useActivity', async (importOriginal) => ({
  ...(await importOriginal<Json>()),
  useActivityStream: (opts: { follow?: boolean } = {}) => (
    box.activityFollow.push(opts.follow),
    { records: box.records, disconnected: false, epoch: null }
  ),
}))
vi.mock('@/api/hooks/useSlots', () => ({
  useSlots: () => ({ data: box.slots, isLoading: false }),
  useSlotLoad: () => ({ mutate: () => {} }),
  useSlotUnload: () => ({ mutate: () => {} }),
  useSlotSwap: () => ({ mutate: () => {} }),
  useSlotRestart: () => ({ mutate: () => {} }),
  useSlotEdit: () => ({ isPending: false, mutate: () => {}, mutateAsync: async () => {} }),
}))
// No GPU segment: the engine is unavailable.
vi.mock('@/api/hooks/useComfyui', async (importOriginal) => ({
  ...(await importOriginal<Json>()),
  useArbiter: () => ({ data: undefined }),
  useComfyui: () => ({ data: undefined, dataUpdatedAt: Date.now() }),
  useComfyuiModels: () => ({
    data: { models: [], engine: { available: false, reason: 'No ROCm lane' }, store: { free_gb: 1, path: '/m' } },
  }),
  useComfyuiSwitchover: () => ({ mutateAsync: async () => ({}) }),
  useComfyuiPin: () => ({ mutateAsync: async () => ({}) }),
  useComfyuiRenderCancel: () => ({ mutateAsync: async () => ({}) }),
  useComfyuiRestart: () => ({ mutateAsync: async () => ({}) }),
}))
vi.mock('@/api/hooks/useStatsHardware', async (importOriginal) => ({
  ...(await importOriginal<Json>()),
  useMemoryBudget: () => ({ data: undefined }),
}))
vi.mock('@/api/hooks/useConfigUrls', () => ({ useConfigUrls: () => ({ data: undefined }) }))
vi.mock('@/api/hooks/useModelsFeasibility', () => ({
  useModelsFeasibility: () => ({ data: undefined, mutate: () => {} }),
}))
vi.mock('@/api/hooks/useRuntime', async (importOriginal) => ({
  ...(await importOriginal<Json>()),
  useRuntimeRollup: () => ({ status: 'up', ready: 2, total: 2 }),
  useHealthSystem: () => ({ data: { status: 'ok', checks: {} }, isError: false }),
}))
vi.mock('@/api/hooks/useServicesHealth', () => ({
  useServicesHealth: () => ({ services: [], pending: false, error: false }),
}))
vi.mock('@/api/hooks/useModels', async (importOriginal) => ({
  ...(await importOriginal<Json>()),
  usePullsList: () => ({ jobs: [], hasActive: false }),
  useClearPullJob: () => ({ mutate: () => {} }),
}))
vi.mock('@/api/hooks/useUpdates', async (importOriginal) => ({
  ...(await importOriginal<Json>()),
  useUpdateState: () => ({ data: undefined }),
}))
vi.mock('@/api/hooks/useProfiles', () => ({ useProfiles: () => ({ data: [] }) }))
vi.mock('@/api/hooks/useRuntimes', async (importOriginal) => ({
  ...(await importOriginal<Json>()),
  useSystemInfo: () => ({ data: { backends: {} } }),
}))

const { Dock } = await import('../dock/Dock.jsx')

// ── fixtures ─────────────────────────────────────────────────────────────────

const jr = (over: Partial<JournalEntry> = {}): JournalEntry => ({
  id: 1,
  ts: '2026-10-26T15:00:01Z',
  source: 'hal0',
  level: 'info',
  msg: 'journal line',
  ...over,
})

const ar = (over: Partial<ActivityRecord> = {}): ActivityRecord => ({
  id: 1,
  ts: '2026-10-26T15:00:03Z',
  kind: 'action',
  category: 'slot',
  action: 'slot.edit_config',
  target: 'brain',
  actor: 'mcp:hermes',
  severity: 'ok',
  outcome: 'ok',
  message: 'edited brain',
  before: null,
  after: null,
  error: null,
  duration_ms: 42,
  request_id: null,
  ...over,
})

// J(:01) J(:02) A(:03) J(:05); the :04 activity record is an event and joins nothing.
function seedMerged() {
  box.ring = [
    jr({ id: 1, ts: '2026-10-26T15:00:01Z', source: 'hal0', msg: 'first' }),
    jr({ id: 2, ts: '2026-10-26T15:00:02Z', source: 'slot:brain', level: 'warn', msg: 'second' }),
    jr({ id: 3, ts: '2026-10-26T15:00:05Z', source: 'system', level: 'error', msg: 'boom' }),
  ]
  box.records = [
    ar({ id: 2, ts: '2026-10-26T15:00:04Z', kind: 'event', message: 'an event, not shown' }),
    ar({ id: 1, ts: '2026-10-26T15:00:03Z', message: 'edited brain' }),
  ]
}

// ── harness ──────────────────────────────────────────────────────────────────

const roots: { root: Root; host: HTMLElement }[] = []
const renderers = new Map<HTMLElement, (el: React.ReactElement) => void>()

function mount(el: React.ReactElement) {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const root = createRoot(host)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const render = (next: React.ReactElement) =>
    act(() => {
      root.render(<QueryClientProvider client={client}>{next}</QueryClientProvider>)
    })
  render(el)
  roots.push({ root, host })
  renderers.set(host, render)
  return host
}
const rerender = (host: HTMLElement, el: React.ReactElement) => renderers.get(host)!(el)

const dock = (expanded: boolean) => <Dock route="slots" param="inference" expanded={expanded} onToggle={() => {}} />
const byId = (testid: string) => document.querySelector(`[data-testid="${testid}"]`) as HTMLElement | null
const rows = () => Array.from(document.querySelectorAll('[data-testid="dock-event-row"]')) as HTMLElement[]
const tags = () => rows().map((r) => r.querySelector('.ev-k')!.textContent)
const msgs = () => rows().map((r) => r.querySelector('.msg')!.textContent)

async function click(el: Element | null | undefined) {
  expect(el, 'element to click').toBeTruthy()
  await act(async () => {
    el!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
  })
}
const chip = (label: string) =>
  Array.from(document.querySelectorAll('.foot-pane-chip')).find((b) => b.textContent === label) as HTMLElement | undefined

async function type(input: HTMLInputElement, value: string) {
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
    setter.call(input, value)
    input.dispatchEvent(new Event('input', { bubbles: true }))
  })
}
async function pick(select: HTMLSelectElement, value: string) {
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')!.set!
    setter.call(select, value)
    select.dispatchEvent(new Event('change', { bubbles: true }))
  })
}

beforeEach(() => {
  box.ring = []
  box.records = []
  box.disconnected = false
  box.logsFollow = []
  box.activityFollow = []
  box.slots = [
    { name: 'brain', model: 'm1' },
    { name: 'coder', model: 'm2' },
  ]
})

afterEach(() => {
  for (const { root, host } of roots.splice(0)) {
    act(() => root.unmount())
    host.remove()
  }
  document.body.innerHTML = ''
})

describe('the dock events pane', () => {
  it('ticker shows the newest two merged rows, newest last', () => {
    seedMerged()
    mount(dock(false))
    const ticker = byId('dock-ticker')!
    expect(ticker.classList.contains('foot-journal')).toBe(true)
    const lines = Array.from(ticker.querySelectorAll('.foot-line'))
    expect(lines.map((l) => l.querySelector('.msg')!.firstChild!.textContent)).toEqual(['edited brain', 'boom'])
    expect(lines.map((l) => l.querySelector('.ev-k')!.textContent)).toEqual(['A', 'J'])
    expect(ticker.querySelector('.stripe')).toBeNull()
  })

  it('events tab merges journal lines and activity actions, tagged J and A', () => {
    seedMerged()
    mount(dock(true))
    expect(byId('dock-events')).toBeTruthy()
    expect(tags()).toEqual(['J', 'J', 'A', 'J'])
    expect(rows().map((r) => r.dataset.stream)).toEqual(['j', 'j', 'a', 'j'])
    expect(msgs().some((m) => m!.includes('an event, not shown'))).toBe(false)
    expect(msgs()[2]).toContain('edited brain')
    // The actor label rides on the activity row.
    expect(rows()[2].querySelector('.actor')!.textContent).toContain('hermes (mcp)')
    // The source is a data attribute, never a class: an activity category such as `slot` must not
    // pick up the slot card's global `.slot` rules (CT150 live check, 2026-10-26).
    for (const r of rows()) {
      const src = r.querySelector('.src') as HTMLElement
      expect(src.className).toBe('src')
      expect(src.dataset.source).toBeTruthy()
    }
    // The all chip replaced merged.
    expect(chip('all')).toBeTruthy()
    expect(chip('merged')).toBeUndefined()
  })

  it('stream, severity, source, slot and search filters narrow the rows', async () => {
    seedMerged()
    mount(dock(true))
    await click(chip('activity'))
    expect(tags()).toEqual(['A'])
    await click(chip('all')) // first "all" chip is the stream group
    expect(tags()).toEqual(['J', 'J', 'A', 'J'])

    const sev = byId('dock-filter-severity')!
    await click(Array.from(sev.querySelectorAll('.foot-pane-chip')).find((b) => b.textContent === 'warn') as HTMLElement)
    expect(msgs()).toEqual(['second'])
    await click(sev.querySelector('.foot-pane-chip') as HTMLElement) // severity all
    expect(rows()).toHaveLength(4)

    const src = byId('dock-filter-source')!
    await click(Array.from(src.querySelectorAll('.foot-pane-chip')).find((b) => b.textContent === 'system') as HTMLElement)
    expect(msgs()).toEqual(['boom'])
    await click(src.querySelector('.foot-pane-chip') as HTMLElement)

    const slot = byId('dock-slot') as unknown as HTMLSelectElement
    expect(Array.from(slot.options).map((o) => o.textContent)).toEqual(['All slots', 'brain', 'coder'])
    await pick(slot, 'brain')
    expect(tags()).toEqual(['J', 'A']) // slot:brain journal line + the brain-targeted action
    await pick(slot, 'all')

    await type(document.querySelector('input.foot-pane-search') as HTMLInputElement, 'BOOM')
    expect(msgs()).toEqual(['boom'])
  })

  it('an activity row opens its detail with actor, outcome and before/after', async () => {
    box.ring = [jr({ id: 1 })]
    box.records = [
      ar({ id: 1, before: { n_ctx: 4096 }, after: { n_ctx: 8192 }, error: 'ctx too big', outcome: 'error' }),
    ]
    mount(dock(true))
    expect(document.querySelector('.foot-line-detail')).toBeNull()
    const arow = rows().find((r) => r.dataset.stream === 'a')!
    await click(arow)
    const detail = document.querySelector('.foot-line-detail')!
    expect(detail.textContent).toContain('slot.edit_config · brain · error · 42 ms')
    expect(detail.textContent).toContain('ctx too big')
    expect(detail.querySelector('pre')!.textContent).toBe(JSON.stringify({ before: { n_ctx: 4096 }, after: { n_ctx: 8192 } }, null, 1))
    await click(arow)
    expect(document.querySelector('.foot-line-detail')).toBeNull()
    // A journal row does nothing.
    await click(rows().find((r) => r.dataset.stream === 'j'))
    expect(document.querySelector('.foot-line-detail')).toBeNull()
  })

  it('unseen badge counts rows appended while collapsed and clears on expand', () => {
    box.ring = [jr({ id: 1 }), jr({ id: 2, ts: '2026-10-26T15:00:02Z' })]
    const host = mount(dock(false))
    expect(byId('dock-unseen')).toBeNull()
    box.ring = [...box.ring, jr({ id: 3, ts: '2026-10-26T15:00:03Z' }), jr({ id: 4, ts: '2026-10-26T15:00:04Z' })]
    rerender(host, dock(false))
    expect(byId('dock-unseen')!.textContent).toBe('+2')
    expect(byId('dock-toggle')!.contains(byId('dock-unseen'))).toBe(true)
    rerender(host, dock(true))
    expect(byId('dock-unseen')).toBeNull()
    rerender(host, dock(false))
    expect(byId('dock-unseen')).toBeNull()
  })

  it('unseen never counts below zero after a ring reset', () => {
    box.ring = Array.from({ length: 10 }, (_, i) => jr({ id: i + 1, ts: `2026-10-26T15:00:${String(i).padStart(2, '0')}Z` }))
    const host = mount(dock(false))
    box.ring = box.ring.slice(0, 3)
    rerender(host, dock(false))
    expect(byId('dock-unseen')).toBeNull()
    // And it counts again from the reset size.
    box.ring = [...box.ring, jr({ id: 99, ts: '2026-10-26T15:01:00Z' })]
    rerender(host, dock(false))
    expect(byId('dock-unseen')!.textContent).toBe('+1')
  })

  it('export link carries the active filters and names the activity trail', async () => {
    seedMerged()
    mount(dock(true))
    const exp = () => byId('dock-export') as HTMLAnchorElement
    expect(exp().getAttribute('title')).toBe(
      'Exports the activity trail with these filters. Journal lines are on the Logs page.',
    )
    expect(exp().getAttribute('href')).toContain('fmt=csv')
    const sev = byId('dock-filter-severity')!
    await click(Array.from(sev.querySelectorAll('.foot-pane-chip')).find((b) => b.textContent === 'warn') as HTMLElement)
    await pick(byId('dock-slot') as unknown as HTMLSelectElement, 'brain')
    const q = new URLSearchParams(exp().getAttribute('href')!.split('?')[1])
    expect(q.get('severity')).toBe('warn')
    expect(q.get('search')).toBe('brain')
    expect(q.get('fmt')).toBe('csv')
  })

  it('dispatches hal0:slot-event for a slot journal line and a slot-targeted action', () => {
    box.ring = [jr({ id: 1, source: 'slot:old', ts: '2026-10-26T15:00:00Z' })]
    box.records = [ar({ id: 1, target: 'older', ts: '2026-10-26T15:00:01Z' })]
    const seen: string[] = []
    const on = (e: Event) => seen.push((e as CustomEvent).detail.slot)
    window.addEventListener('hal0:slot-event', on)
    try {
      const host = mount(dock(false))
      expect(seen).toEqual([]) // both backfills are skipped
      box.ring = [...box.ring, jr({ id: 2, source: 'slot:brain', ts: '2026-10-26T15:00:10Z' }), jr({ id: 3, source: 'hal0', ts: '2026-10-26T15:00:11Z' })]
      box.records = [ar({ id: 5, target: 'coder/config', ts: '2026-10-26T15:00:12Z' }), ...box.records]
      rerender(host, dock(false))
      expect(seen).toEqual(['brain', 'coder'])
      rerender(host, dock(false))
      expect(seen).toEqual(['brain', 'coder']) // once per event
    } finally {
      window.removeEventListener('hal0:slot-event', on)
    }
  })

  it('backfill from the slower stream is not unseen and dispatches no slot events', () => {
    box.ring = [jr({ id: 1, ts: '2026-10-26T15:00:00Z' })]
    const seen: string[] = []
    const on = (e: Event) => seen.push((e as CustomEvent).detail.slot)
    window.addEventListener('hal0:slot-event', on)
    try {
      const host = mount(dock(false))
      box.records = [
        ar({ id: 3, target: 'brain', ts: '2026-10-26T15:00:03Z' }),
        ar({ id: 2, target: 'coder', ts: '2026-10-26T15:00:02Z' }),
        ar({ id: 1, target: null, ts: '2026-10-26T15:00:01Z' }),
      ]
      rerender(host, dock(false))
      expect(byId('dock-unseen')).toBeNull()
      expect(seen).toEqual([])
      box.records = [ar({ id: 4, target: 'brain', ts: '2026-10-26T15:00:04Z' }), ...box.records]
      rerender(host, dock(false))
      expect(byId('dock-unseen')!.textContent).toBe('+1')
      expect(seen).toEqual(['brain'])
    } finally {
      window.removeEventListener('hal0:slot-event', on)
    }
  })

  it('backfill from the journal arriving after the activity trail is not unseen either', () => {
    box.records = [ar({ id: 1, target: 'brain', ts: '2026-10-26T15:00:01Z' })]
    const seen: string[] = []
    const on = (e: Event) => seen.push((e as CustomEvent).detail.slot)
    window.addEventListener('hal0:slot-event', on)
    try {
      const host = mount(dock(false))
      box.ring = [
        jr({ id: 1, source: 'slot:brain', ts: '2026-10-26T15:00:02Z' }),
        jr({ id: 2, source: 'slot:coder', ts: '2026-10-26T15:00:03Z' }),
      ]
      rerender(host, dock(false))
      expect(byId('dock-unseen')).toBeNull()
      expect(seen).toEqual([])
      box.ring = [...box.ring, jr({ id: 3, source: 'slot:coder', ts: '2026-10-26T15:00:04Z' })]
      rerender(host, dock(false))
      expect(byId('dock-unseen')!.textContent).toBe('+1')
      expect(seen).toEqual(['coder'])
    } finally {
      window.removeEventListener('hal0:slot-event', on)
    }
  })

  it('a hidden tab closes both streams; the reopen keeps the rows and counts nothing as new', async () => {
    const setVisibility = async (state: 'hidden' | 'visible') => {
      Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => state })
      await act(async () => {
        document.dispatchEvent(new Event('visibilitychange'))
      })
    }
    box.ring = [jr({ id: 1 }), jr({ id: 2, ts: '2026-10-26T15:00:02Z' })]
    box.records = [ar({ id: 1, ts: '2026-10-26T15:00:03Z' })]
    const host = mount(dock(false))
    try {
      expect(box.logsFollow.at(-1)).toBe(true)
      expect(box.activityFollow.at(-1)).toBe(true)
      await setVisibility('hidden')
      expect(box.logsFollow.at(-1)).toBe(false)
      expect(box.activityFollow.at(-1)).toBe(false)
      await setVisibility('visible')
      expect(box.logsFollow.at(-1)).toBe(true)
      expect(box.activityFollow.at(-1)).toBe(true)
      // The replay on reopen is deduped by the hooks: same rows, no badge.
      rerender(host, dock(false))
      expect(byId('dock-unseen')).toBeNull()
      // A row that arrived after the reopen is still news.
      box.ring = [...box.ring, jr({ id: 3, ts: '2026-10-26T15:00:09Z' })]
      rerender(host, dock(false))
      expect(byId('dock-unseen')!.textContent).toBe('+1')
      expect(byId('dock-toggle')!.getAttribute('aria-label')).toBe('Toggle dock, 1 unseen events')
    } finally {
      // @ts-expect-error restore the prototype getter
      delete document.visibilityState
    }
  })

  it('hal0:open-events expands the dock on the events tab', async () => {
    seedMerged()
    const onToggle = vi.fn()
    mount(<Dock route="slots" param="inference" expanded={false} onToggle={onToggle} />)
    await act(async () => {
      window.dispatchEvent(new CustomEvent('hal0:open-events'))
    })
    expect(onToggle).toHaveBeenCalledTimes(1)
  })
})
