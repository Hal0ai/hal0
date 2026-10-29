// @vitest-environment happy-dom
//
// OvUsage: window picker over /api/stats, heroes, bar strip, per-model table.
import React from 'react'
import { createRoot } from 'react-dom/client'
import { act } from 'react-dom/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

;(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true
;(globalThis as any).React = React

const MODEL = 'tiel-coder-35b-a3b-mtp-ud-q6-k-xl'
const STATS_1H = {
  window: { from: 'a', to: 'b', bucket: '1h' },
  totals: { requests: 6, ok: 6, errors: 0, tokens_completed: 1854 },
  by_model: [
    {
      model_id: MODEL,
      ttft_ms: { p50: 3777, p95: 7434 },
      tps_decode: { avg: 60.34, p50: 59.8 },
      tps_prefill: { avg: 1046.2 },
      spec_accept_rate: 0.5337,
      ok: 6,
      errors: 0,
    },
  ],
} as any
const STATS_EMPTY = { ...STATS_1H, totals: { requests: 0, ok: 0, errors: 0, tokens_completed: 0 }, by_model: [] }

let stats: unknown = STATS_1H
const useStatsSummary = vi.fn((_w: string) => ({ data: stats }))
vi.mock('@/api/hooks/useStatsSummary', () => ({ useStatsSummary: (w: string) => useStatsSummary(w) }))

await import('../shell.jsx')
await import('../usage.jsx')
const OvUsage = (window as any).OvUsage as React.ComponentType<{ onGo: (id: string) => void }>

let host: HTMLDivElement
let root: ReturnType<typeof createRoot>

function mount(onGo: (id: string) => void = () => {}) {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  act(() => root.render(<OvUsage onGo={onGo} />))
}

beforeEach(() => {
  stats = STATS_1H
  useStatsSummary.mockClear()
  localStorage.clear()
})
afterEach(() => {
  act(() => root.unmount())
  document.body.innerHTML = ''
})

const tab = (w: string) => host.querySelector(`.ov-tabs button[data-win="${w}"]`) as HTMLButtonElement

describe('OvUsage', () => {
  it('defaults to 24h and shows heroes and the model row', () => {
    mount()
    expect(tab('24h').getAttribute('aria-pressed')).toBe('true')
    expect(tab('1h').getAttribute('aria-pressed')).toBe('false')
    expect(useStatsSummary).toHaveBeenLastCalledWith('24h')
    const heroes = host.querySelector('.ov-heroes')!.textContent!
    expect(heroes).toContain('1.9k')
    expect(heroes).toContain('60.3')
    expect(heroes).toContain('3.8s')
    expect(heroes).toContain('1,046')
    expect(host.querySelectorAll('.ov-mtable tbody tr')).toHaveLength(1)
    expect(host.querySelector('.ov-mtable tbody')!.textContent).toContain('53%')
  })

  it('persists a window choice and refetches with it', () => {
    mount()
    act(() => tab('7d').click())
    expect(localStorage.getItem('hal0.overview.usageWindow')).toBe('7d')
    expect(useStatsSummary).toHaveBeenLastCalledWith('7d')
    expect(tab('7d').getAttribute('aria-pressed')).toBe('true')
  })

  it('restores the stored window', () => {
    localStorage.setItem('hal0.overview.usageWindow', '1h')
    mount()
    expect(tab('1h').getAttribute('aria-pressed')).toBe('true')
  })

  it('draws no positive-height bar when buckets are absent', () => {
    mount()
    const svg = host.querySelector('svg.ov-usage-spk')!
    expect(svg).not.toBeNull()
    const tall = Array.from(svg.querySelectorAll('rect')).filter((r) => Number(r.getAttribute('height')) > 0)
    expect(tall).toHaveLength(0)
  })

  it('draws bars when buckets are present', () => {
    stats = { ...STATS_1H, buckets: [{ ts: 'a', requests: 0, tokens_completed: 0 }, { ts: 'b', requests: 6, tokens_completed: 1854 }] }
    mount()
    expect(host.querySelectorAll('svg.ov-usage-spk rect')).toHaveLength(2)
  })

  it('drops the table and says so for an empty window', () => {
    stats = STATS_EMPTY
    mount()
    expect(host.querySelector('.ov-mtable')).toBeNull()
    expect(host.textContent).toContain('no requests in the last 24h')
  })

  it('renders dashes while loading', () => {
    stats = undefined
    mount()
    expect(host.querySelector('.ov-heroes')!.textContent).toContain('—')
    expect(host.querySelector('.ov-mtable')).toBeNull()
  })

  it('footer Benchmarks link goes to benchmarks', () => {
    const onGo = vi.fn()
    mount(onGo)
    const btn = Array.from(host.querySelectorAll('footer button')).find((b) => b.textContent?.includes('Benchmarks')) as HTMLButtonElement
    act(() => btn.click())
    expect(onGo).toHaveBeenCalledWith('benchmarks')
  })
})
