// @vitest-environment happy-dom
//
// OvServingNow: activity chart, lane-grouped slot rows, stopped chips that load, row + link navigation.
import React from 'react'
import { createRoot } from 'react-dom/client'
import { act } from 'react-dom/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { SERVING_UTILITY, SLOTS, SYS } from './serving-fixtures'

;(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true
;(globalThis as any).React = React

let slotsData: any[] = SLOTS
let sysData: any = SYS
let activity: any = undefined
let hw: any = {}
const windows: string[] = []
const loadMutate = vi.fn()
const unloadMutate = vi.fn()

vi.mock('@/api/hooks/useSlots', () => ({
  useSlots: () => ({ data: slotsData }),
  useSlotLoad: () => ({ mutate: loadMutate }),
  useSlotUnload: () => ({ mutate: unloadMutate }),
}))
vi.mock('@/api/hooks/useSystemStats', () => ({ useSystemStats: () => ({ data: sysData }) }))
vi.mock('@/api/hooks/useStatsHardware', () => ({ useStatsHardware: () => ({ data: hw }) }))
vi.mock('@/api/hooks/useSlotActivity', () => ({
  ACTIVITY_WINDOWS: ['5m', '15m', '1h'],
  useSlotActivity: (w: string) => {
    windows.push(w)
    return { data: activity, isLoading: false, isError: false }
  },
}))

await import('../shell.jsx')
await import('../serving-now.jsx')
const OvServingNow = (window as any).OvServingNow as React.ComponentType<{ onGo: (id: string) => void }>

let host: HTMLDivElement
let root: ReturnType<typeof createRoot>

function mount(onGo: (id: string) => void = () => {}) {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  act(() => root.render(<OvServingNow onGo={onGo} />))
}

beforeEach(() => {
  slotsData = SLOTS
  sysData = SYS
  activity = undefined
  hw = {}
  windows.length = 0
  loadMutate.mockClear()
  unloadMutate.mockClear()
})

afterEach(() => {
  act(() => root.unmount())
  document.body.innerHTML = ''
})

describe('OvServingNow', () => {
  const row = (n: string) => host.querySelector(`[data-testid="ov-slot-row-${n}"]`) as HTMLTableRowElement | null
  const lane = (tps: number[], extra: Record<string, unknown> = {}) => ({
    tps, peak_tps: Math.max(...tps), tokens: 10, requests: 3, errors: 0, ttft_p50_ms: 410, ...extra,
  })
  const payload = (slots: Record<string, unknown>, gpu: Array<number | null> = [null, null]) => ({
    window: '15m', window_s: 900, bucket_s: 15, buckets: 2, end: '', fleet: { gpu_util: gpu, npu_util: [null, null] }, slots,
  })
  const bands = () => [...host.querySelectorAll('[data-lane]')].map((g) => g.getAttribute('data-lane'))

  it('renders four lane sections with the loaded count', () => {
    mount()
    const lanes = host.querySelectorAll('tbody.ov-lane')
    expect(lanes.length).toBe(4)
    expect(lanes[0].querySelector('.ov-lane-h')?.textContent).toContain('3 / 4 loaded')
    expect(host.querySelector('.ov-card-n')?.textContent).toBe('5/13 loaded')
  })

  it('a row per loaded slot, a default chip on agent, ctx on utility', () => {
    mount()
    const names = [...host.querySelectorAll('tbody.ov-lane')[0].querySelectorAll('.ov-srow .ov-nm')].map((b) => b.textContent)
    expect(names).toEqual(['agent', 'utility', 'brain'])
    expect(row('agent')!.querySelector('.ov-chip.amber')?.textContent).toBe('default')
    expect(row('utility')!.querySelector('.ov-chip.amber')).toBeNull()
    expect(row('utility')!.textContent).toContain('10k/131k')
  })

  it('stopped chip loads the slot on click', () => {
    mount()
    const chip = [...host.querySelectorAll('.ov-stopped .ov-sc')].find((b) => b.textContent?.includes('ops')) as HTMLButtonElement
    expect(chip).toBeTruthy()
    act(() => chip.click())
    expect(loadMutate).toHaveBeenCalledWith('ops')
  })

  it('row click and the name button open the slot, Manage slots opens the slots page', () => {
    const onGo = vi.fn()
    mount(onGo)
    act(() => row('agent')!.click())
    expect(onGo).toHaveBeenLastCalledWith('slots/agent')
    act(() => (row('utility')!.querySelector('.ov-nm') as HTMLButtonElement).click())
    expect(onGo).toHaveBeenLastCalledWith('slots/utility')
    expect(onGo).toHaveBeenCalledTimes(2) // the name button does not also fire the row
    const link = [...host.querySelectorAll('button.ov-lnk')].find((b) => b.textContent?.includes('Manage slots')) as HTMLButtonElement
    act(() => link.click())
    expect(onGo).toHaveBeenLastCalledWith('slots')
  })

  it('unload button does not open the slot', () => {
    const onGo = vi.fn()
    ;(window as any).confirm = vi.fn(() => true)
    mount(onGo)
    const btn = [...row('agent')!.querySelectorAll('.ov-acts .ov-btn')].find((b) => b.textContent === 'unload') as HTMLButtonElement
    act(() => btn.click())
    expect(unloadMutate).toHaveBeenCalledWith('agent')
    expect(onGo).not.toHaveBeenCalled()
  })

  it('a serving row is marked and shows live tok/s and in-flight', () => {
    slotsData = SLOTS.map((s) => (s.name === 'utility' ? { ...s, ...SERVING_UTILITY } : s))
    sysData = { ...SYS, slots: [...SYS.slots.filter((s: any) => s.slot_id !== 'utility'), { slot_id: 'utility', inflight: 2 }] }
    mount()
    const r = row('utility')!
    expect(r.classList.contains('serving')).toBe(true)
    expect(r.textContent).toContain('58.0')
    expect(r.textContent).toContain('2 in flight')
  })

  it('a ready slot shows no live rate even when its metrics keep the last sample', () => {
    slotsData = SLOTS.map((s) => (s.name === 'agent' ? { ...s, metrics: { toks: 31 } } : s))
    mount()
    const cells = [...row('agent')!.querySelectorAll('td.num')].map((td) => td.textContent)
    expect(cells[0]).toBe('—')
  })

  it('hidden row actions take no taps', async () => {
    const { readFileSync } = await import(/* @vite-ignore */ ['node', 'fs'].join(':') as string)
    const css = readFileSync(`${(globalThis as any).process.cwd()}/src/dash/overview/overview.css`, 'utf8')
    expect(css).toMatch(/\.ov-srow \.ov-acts \{[^}]*opacity: 0;[^}]*pointer-events: none/)
    expect(css).toMatch(/\.ov-srow:hover \.ov-acts, \.ov-srow:focus-within \.ov-acts \{[^}]*pointer-events: auto/)
  })

  it('warming and error rows count as loaded and carry their state class', () => {
    slotsData = [
      { name: 'a', device: 'gpu-vulkan', state: 'ready', type: 'llm' },
      { name: 'b', device: 'gpu-vulkan', state: 'warming', type: 'llm' },
      { name: 'c', device: 'gpu-vulkan', state: 'error', type: 'llm' },
      { name: 'd', device: 'gpu-vulkan', state: 'offline', type: 'llm' },
    ]
    mount()
    expect(host.querySelector('.ov-lane-h')?.textContent).toContain('3 / 4 loaded')
    expect(host.querySelector('.ov-card-n')?.textContent).toBe('3/4 loaded')
    expect(row('c')!.classList.contains('error')).toBe(true)
    expect(row('b')!.classList.contains('warming')).toBe(true)
    expect(row('a')!.classList.contains('error') || row('a')!.classList.contains('warming')).toBe(false)
  })

  it('styles error rows with --err and warming state with --warn', async () => {
    const { readFileSync } = await import(/* @vite-ignore */ ['node', 'fs'].join(':') as string)
    const css = readFileSync(`${(globalThis as any).process.cwd()}/src/dash/overview/overview.css`, 'utf8')
    expect(css).toMatch(/\.ov-srow\.error \.ov-st\s*\{[^}]*color:\s*var\(--err\)/)
    expect(css).toMatch(/\.ov-srow\.warming \.ov-st\s*\{[^}]*color:\s*var\(--warn\)/)
  })

  it('logs routes to slots then dispatches hal0:slot-logs with the slot name', () => {
    vi.useFakeTimers()
    try {
      const onGo = vi.fn()
      const seen: string[] = []
      const listener = (e: Event) => seen.push((e as CustomEvent).detail.name)
      window.addEventListener('hal0:slot-logs', listener)
      mount(onGo)
      const btn = [...row('agent')!.querySelectorAll('.ov-acts .ov-btn')].find((b) => b.textContent === 'logs') as HTMLButtonElement
      act(() => btn.click())
      expect(onGo).toHaveBeenCalledTimes(1)
      expect(onGo).toHaveBeenCalledWith('slots')
      expect(seen).toEqual([])
      act(() => {
        vi.advanceTimersByTime(100)
      })
      expect(seen).toEqual(['agent'])
      window.removeEventListener('hal0:slot-logs', listener)
    } finally {
      vi.useRealTimers()
    }
  })

  it('shows an empty state when there are no slots', () => {
    slotsData = []
    mount()
    expect(host.querySelector('.ov-empty')).not.toBeNull()
    expect(host.querySelector('[data-testid="ov-activity"]')).toBeNull()
  })

  it('opens on 15m and the switch changes the window', () => {
    mount()
    expect(windows.at(-1)).toBe('15m')
    act(() => (host.querySelector('[data-testid="ov-activity-win-1h"]') as HTMLButtonElement).click())
    expect(windows.at(-1)).toBe('1h')
  })

  it('charts every slot that moved tokens, stopped ones included, in lane order; empty says so', () => {
    activity = payload({})
    mount()
    expect(host.querySelector('[data-testid="ov-activity-empty"]')?.textContent).toBe('No traffic in this window.')
    act(() => root.unmount())
    activity = payload({ img: lane([1, 0]), ops: lane([0, 3]), agent: lane([4, 8]), gone: lane([9, 9]) })
    mount()
    // agent (vulkan, loaded) → ops (vulkan, stopped since) → img (rocm); a name with no slot is dropped.
    expect(bands()).toEqual(['agent', 'ops', 'img'])
  })

  it('table figures come from the activity payload, "—" without it', () => {
    activity = payload({ agent: lane([4, 8], { errors: 1 }) })
    mount()
    const t = row('agent')!.textContent!
    expect(t).toContain('8.0') // peak
    expect(t).toContain('3 · 1 err')
    expect(t).toContain('410 ms')
    expect(row('brain')!.textContent!.match(/—/g)!.length).toBeGreaterThanOrEqual(4)
  })

  it('a row swatch carries its lane colour', () => {
    mount()
    expect((row('agent')!.querySelector('.sw') as HTMLElement).style.background).toBe('var(--dev-vulkan)')
  })

  it('GPU busy strip only with readings, and never when the GPU clock is pinned', () => {
    activity = payload({ agent: lane([4, 8]) })
    mount()
    expect(host.querySelector('[data-testid="ov-activity-duty"]')).toBeNull()
    act(() => root.unmount())
    activity = payload({ agent: lane([4, 8]) }, [0.5, 0.9])
    mount()
    expect(host.querySelector('[data-testid="ov-activity-duty"]')!.children).toHaveLength(2)
    act(() => root.unmount())
    hw = { util_is_forced_high: true }
    mount()
    expect(host.querySelector('[data-testid="ov-activity-duty"]')).toBeNull()
    expect(host.textContent).toContain('GPU clock pinned, busy not shown')
  })

  it('hovering a row fades the other slots in the chart', () => {
    activity = payload({ agent: lane([4, 8]), utility: lane([2, 2]) })
    mount()
    act(() => {
      row('agent')!.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }))
    })
    const fill = (k: string) => (host.querySelector(`[data-lane="${k}"] path`) as SVGPathElement).style.fillOpacity
    expect(fill('agent')).toBe('0.26')
    expect(fill('utility')).toBe('0.06')
  })
})
