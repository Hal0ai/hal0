// @vitest-environment happy-dom
//
// OvServingNow: lane-grouped tiles, stopped chips that load, tile + link navigation.
import React from 'react'
import { createRoot } from 'react-dom/client'
import { act } from 'react-dom/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { SERVING_UTILITY, SLOTS, SYS } from './serving-fixtures'

;(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true
;(globalThis as any).React = React

let slotsData: any[] = SLOTS
let sysData: any = SYS
let history: any = null
const loadMutate = vi.fn()
const unloadMutate = vi.fn()

vi.mock('@/api/hooks/useSlots', () => ({
  useSlots: () => ({ data: slotsData }),
  useSlotLoad: () => ({ mutate: loadMutate }),
  useSlotUnload: () => ({ mutate: unloadMutate }),
}))
vi.mock('@/api/hooks/useSystemStats', () => ({ useSystemStats: () => ({ data: sysData }) }))
vi.mock('@/api/hooks/useThroughputHistory', () => ({ useThroughputHistory: () => ({ data: history }) }))

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
  history = null
  loadMutate.mockClear()
  unloadMutate.mockClear()
})

afterEach(() => {
  act(() => root.unmount())
  document.body.innerHTML = ''
})

describe('OvServingNow', () => {
  it('renders four lane headers with the loaded count', () => {
    mount()
    const lanes = host.querySelectorAll('.ov-lane')
    expect(lanes.length).toBe(4)
    expect(lanes[0].querySelector('.ov-lane-h')?.textContent).toContain('3 / 4 loaded')
    expect(host.querySelector('.ov-card-n')?.textContent).toBe('5/13 loaded')
  })

  it('renders a tile per loaded slot with a default chip on agent', () => {
    mount()
    const tiles = [...host.querySelectorAll('.ov-lane')[0].querySelectorAll('.ov-tile')]
    expect(tiles.map((t) => t.querySelector('.ov-nm')?.textContent)).toEqual(['agent', 'utility', 'brain'])
    expect(tiles[0].querySelector('.ov-chip.amber')?.textContent).toBe('default')
    expect(tiles[1].querySelector('.ov-chip.amber')).toBeNull()
    expect(tiles[1].textContent).toContain('ctx')
    expect(tiles[1].textContent).toContain('10k/131k')
  })

  it('stopped chip loads the slot on click', () => {
    mount()
    const chip = [...host.querySelectorAll('.ov-stopped .ov-sc')].find((b) => b.textContent?.includes('ops')) as HTMLButtonElement
    expect(chip).toBeTruthy()
    act(() => chip.click())
    expect(loadMutate).toHaveBeenCalledWith('ops')
  })

  it('tile click opens the slot, Manage slots opens the slots page', () => {
    const onGo = vi.fn()
    mount(onGo)
    act(() => (host.querySelector('.ov-tile') as HTMLElement).click())
    expect(onGo).toHaveBeenCalledWith('slots/agent')
    const link = [...host.querySelectorAll('button.ov-lnk')].find((b) => b.textContent?.includes('Manage slots')) as HTMLButtonElement
    act(() => link.click())
    expect(onGo).toHaveBeenLastCalledWith('slots')
  })

  it('unload button does not open the slot', () => {
    const onGo = vi.fn()
    ;(window as any).confirm = vi.fn(() => true)
    mount(onGo)
    const btn = [...host.querySelectorAll('.ov-tile .ov-acts .ov-btn')].find((b) => b.textContent === 'unload') as HTMLButtonElement
    act(() => btn.click())
    expect(unloadMutate).toHaveBeenCalledWith('agent')
    expect(onGo).not.toHaveBeenCalled()
  })

  it('a serving tile is marked, shows tok/s and draws the sparkline', () => {
    slotsData = SLOTS.map((s) => (s.name === 'utility' ? { ...s, ...SERVING_UTILITY } : s))
    sysData = { ...SYS, slots: [...SYS.slots.filter((s: any) => s.slot_id !== 'utility'), { slot_id: 'utility', inflight: 2 }] }
    history = { per_slot: { utility: [40, 52, 61] } }
    mount()
    const tile = [...host.querySelectorAll('.ov-tile')].find((t) => t.querySelector('.ov-nm')?.textContent === 'utility')!
    expect(tile.classList.contains('serving')).toBe(true)
    expect(tile.textContent).toContain('58 tok/s')
    expect(tile.querySelector('svg')).not.toBeNull()
  })

  it('counts warming and error tiles as loaded in the lane header and the card note', () => {
    slotsData = [
      { name: 'a', device: 'gpu-vulkan', state: 'ready', type: 'llm' },
      { name: 'b', device: 'gpu-vulkan', state: 'warming', type: 'llm' },
      { name: 'c', device: 'gpu-vulkan', state: 'error', type: 'llm' },
      { name: 'd', device: 'gpu-vulkan', state: 'offline', type: 'llm' },
    ]
    mount()
    expect(host.querySelector('.ov-lane-h')?.textContent).toContain('3 / 4 loaded')
    expect(host.querySelector('.ov-card-n')?.textContent).toBe('3/4 loaded')
  })

  it('marks an error tile and a warming tile so the state colours apply', () => {
    slotsData = [
      { name: 'b', device: 'gpu-vulkan', state: 'warming', type: 'llm' },
      { name: 'c', device: 'gpu-vulkan', state: 'error', type: 'llm' },
      { name: 'a', device: 'gpu-vulkan', state: 'ready', type: 'llm' },
    ]
    mount()
    const tile = (n: string) => [...host.querySelectorAll('.ov-tile')].find((t) => t.querySelector('.ov-nm')?.textContent === n)!
    expect(tile('c').classList.contains('error')).toBe(true)
    expect(tile('b').classList.contains('warming')).toBe(true)
    expect(tile('a').classList.contains('error') || tile('a').classList.contains('warming')).toBe(false)
  })

  it('styles error tiles with --err and warming text with --warn', async () => {
    const { readFileSync } = await import(/* @vite-ignore */ ['node', 'fs'].join(':') as string)
    const css = readFileSync(`${(globalThis as any).process.cwd()}/src/dash/overview/overview.css`, 'utf8')
    expect(css).toMatch(/\.ov-tile\.error\s*\{[^}]*border-color:\s*var\(--err-line\)/)
    expect(css).toMatch(/\.ov-tile\.error \.ov-st\s*\{[^}]*color:\s*var\(--err\)/)
    expect(css).toMatch(/\.ov-tile\.warming \.ov-st\s*\{[^}]*color:\s*var\(--warn\)/)
  })

  it('logs routes to slots then dispatches hal0:slot-logs with the slot name', () => {
    vi.useFakeTimers()
    try {
      const onGo = vi.fn()
      const seen: string[] = []
      const listener = (e: Event) => seen.push((e as CustomEvent).detail.name)
      window.addEventListener('hal0:slot-logs', listener)
      mount(onGo)
      const btn = [...host.querySelectorAll('.ov-tile .ov-acts .ov-btn')].find((b) => b.textContent === 'logs') as HTMLButtonElement
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
  })
})
