// @vitest-environment happy-dom
// The band's activity panel: window switch, stacked lanes, duty strip, lane table, hover focus.
import React, { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
;(globalThis as unknown as { window: typeof globalThis }).window = globalThis
;(globalThis as unknown as { React: typeof React }).React = React
;(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const state: { windows: string[]; data: unknown } = { windows: [], data: undefined }
vi.mock('@/api/hooks/useSlotActivity', () => ({
  ACTIVITY_WINDOWS: ['5m', '15m', '1h'],
  useSlotActivity: (w: string) => {
    state.windows.push(w)
    return { data: state.data, isLoading: false, isError: false }
  },
}))

const { NpuActivity } = await import('../npu/NpuActivity')

const HUES = { flm: { hue: 'purple' }, xdna: { hue: 'teal' } }
const row = (name: string, engine: 'flm' | 'xdna', extra: Record<string, unknown> = {}) => ({
  name, engine, role: 'chat', model: 'm', cols: '8', nowToks: null, lastUsedAge: null, activity: null, ...extra,
})
const lane = (tps: number[], extra: Record<string, unknown> = {}) => ({
  tps, peak_tps: Math.max(...tps), tokens: 10, requests: 3, errors: 0, ttft_p50_ms: 410, ...extra,
})
const payload = (slots: Record<string, unknown>, npu: Array<number | null> = [null, null]) => ({
  window: '15m', window_s: 900, bucket_s: 15, buckets: 2, end: '', fleet: { gpu_util: [null, null], npu_util: npu }, slots,
})

let host: HTMLDivElement
let root: Root
const render = (rows: unknown[]) => act(() => root.render(<NpuActivity rows={rows as never} hues={HUES} />))
const q = (id: string) => host.querySelector(`[data-testid="${id}"]`) as HTMLElement | null

beforeEach(() => {
  state.windows = []
  state.data = undefined
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})
afterEach(() => {
  act(() => root.unmount())
  host.remove()
})

describe('NpuActivity', () => {
  it('opens on 15m and the switch changes the window', () => {
    render([row('flm', 'flm')])
    expect(state.windows.at(-1)).toBe('15m')
    expect(q('npu-activity-win-15m')!.getAttribute('aria-pressed')).toBe('true')
    act(() => q('npu-activity-win-1h')!.click())
    expect(state.windows.at(-1)).toBe('1h')
    expect(q('npu-activity-win-1h')!.getAttribute('aria-pressed')).toBe('true')
  })

  it('one band per lane that moved tokens; STT rows stay table-only', () => {
    state.data = payload({ flm: lane([4, 8]), 'npu-fast': lane([0, 6]), 'flm-stt': lane([0, 0], { tokens: 0 }) })
    render([row('flm', 'flm'), row('flm-stt', 'flm', { role: 'stt', cols: 'shares flm' }), row('npu-fast', 'xdna')])
    const bands = [...host.querySelectorAll('[data-lane]')].map((g) => g.getAttribute('data-lane'))
    expect(bands).toEqual(['flm', 'npu-fast'])
    expect(q('npu-activity-empty')).toBeNull()
  })

  it('empty window says so', () => {
    state.data = payload({})
    render([row('flm', 'flm')])
    expect(q('npu-activity-empty')!.textContent).toBe('No NPU traffic in this window.')
  })

  it('table columns come from the activity payload, "—" without it', () => {
    state.data = payload({ flm: lane([4, 8], { errors: 1 }) })
    render([row('flm', 'flm', { nowToks: 18.24, lastUsedAge: 2 }), row('npu-fast', 'xdna')])
    const flm = q('npu-lane-row-flm')!.textContent!
    expect(flm).toContain('18.2')
    expect(flm).toContain('8.0') // peak
    expect(flm).toContain('3 · 1 err')
    expect(flm).toContain('410 ms')
    expect(flm).toContain('now')
    expect(flm).toContain('FLM')
    const ggml = q('npu-lane-row-npu-fast')!.textContent!
    expect(ggml).toContain('GGML')
    expect(ggml.match(/—/g)!.length).toBeGreaterThanOrEqual(5)
  })

  it('duty strip only when the sampler has readings', () => {
    state.data = payload({ flm: lane([4, 8]) })
    render([row('flm', 'flm')])
    expect(q('npu-activity-duty')).toBeNull()
    state.data = payload({ flm: lane([4, 8]) }, [0.2, null])
    render([row('flm', 'flm')])
    expect(q('npu-activity-duty')!.children).toHaveLength(2)
  })

  it('hovering a row fades the other lanes', () => {
    state.data = payload({ flm: lane([4, 8]), 'npu-fast': lane([2, 2]) })
    render([row('flm', 'flm'), row('npu-fast', 'xdna')])
    act(() => {
      q('npu-lane-row-flm')!.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }))
    })
    const fill = (k: string) => (host.querySelector(`[data-lane="${k}"] path`) as SVGPathElement).style.fillOpacity
    expect(fill('flm')).toBe('0.26')
    expect(fill('npu-fast')).toBe('0.06')
  })

  it('no lanes: the empty-NPU hint instead of a table', () => {
    render([])
    expect(q('npu-lanes-table')).toBeNull()
    expect(host.textContent).toContain('Nothing holds the NPU. Start a lane below.')
  })
})
