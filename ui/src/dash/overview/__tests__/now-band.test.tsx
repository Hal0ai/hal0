// @vitest-environment happy-dom
//
// OvNowBand: mode pip, facts line, contextual actions and the unified memory bar. The real
// memory-map model runs; only the data hooks are mocked.
import React from 'react'
import { createRoot } from 'react-dom/client'
import { act } from 'react-dom/test-utils'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

;(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true
;(globalThis as any).React = React
// primitives.jsx (Modal) reads `Icons` off the window at render time.
;(globalThis as any).Icons = new Proxy({}, { get: () => null })

const slot = (name: string, state: string, device: string, toks?: number) => ({
  name,
  type: name === 'img' ? 'image' : 'llm',
  device,
  model: `m-${name}`,
  state,
  ...(toks != null ? { metrics: { toks } } : {}),
})
// 13 slots, 5 loaded (brain + agent serving, img + two ready), 8 stopped.
const SLOTS = [
  slot('brain', 'serving', 'gpu-rocm', 40),
  slot('agent', 'serving', 'gpu-vulkan', 39),
  slot('img', 'ready', 'gpu-rocm'),
  slot('e1', 'ready', 'gpu-vulkan'),
  slot('e2', 'ready', 'gpu-vulkan'),
  slot('spare', 'offline', 'gpu-rocm'),
  ...Array.from({ length: 7 }, (_, i) => slot(`off${i}`, 'offline', 'cpu')),
]

const budget = {
  gpu: {
    kind: 'shared',
    total_gb: 116,
    used_gb: 43.4,
    this_box: [
      { slot: 'brain', gb: 30, state: 'serving' },
      { slot: 'agent', gb: 12, state: 'serving' },
    ],
    image_engine_gb: 4.4,
    image_engine_slot: 'img',
    image_engine_estimated: false,
    other_guests_gb: 8,
    attribution_excess_gb: 0,
    free_gb: 48,
  },
  ram: { limit_gb: 105.5, used_gb: 16.1, free_gb: 89.4, source: 'container' },
  render_free_gb: 60,
}

const state: { arbiter: any; slots: any; comfy: any; switchoverError: Error | null } = {
  arbiter: { mode: 'llm', switching: false, pinned: false, image_jobs: 0, phase: null },
  slots: { data: undefined, isPending: false, isError: false },
  comfy: undefined,
  switchoverError: null,
}
const switchover = vi.fn(() => (state.switchoverError ? Promise.reject(state.switchoverError) : Promise.resolve({})))
const slotLoad = vi.fn(() => Promise.resolve({}))
const mutation = (fn: any) => ({ mutateAsync: fn, mutate: fn, isPending: false })

vi.mock('@/api/hooks/useSlots', () => ({
  useSlots: () => (state.slots.data === undefined && !state.slots.isPending && !state.slots.isError ? { data: SLOTS, isPending: false, isError: false } : state.slots),
  useSlotLoad: () => mutation(slotLoad),
}))
vi.mock('@/api/hooks/useHardware', () => ({
  useHardware: () => ({ data: { platform_label: 'test', memoryKind: 'unified' }, isLoading: false }),
  hasGpuAccess: () => true,
  hasNpuAccess: () => false,
}))
vi.mock('@/api/hooks/useStatsHardware', () => ({
  useStatsHardware: () => ({
    data: { memory: budget, per_slot: {}, gpu_util: 0, gpu_temp_c: 47 },
    isLoading: false,
  }),
  useMemoryBudget: () => ({ data: budget }),
}))
vi.mock('@/api/hooks/useStatsPower', () => ({
  useStatsPower: () => ({ data: { gpu_power_w: 33, gpu_temp_c: 47, gpu_sclk_mhz: 800, cpu_temp_c: 40 } }),
}))
vi.mock('@/api/hooks/useProxmoxSettings', () => ({ useProxmoxSettings: () => ({ data: null, isLoading: false }) }))
vi.mock('@/api/hooks/useSystemStats', () => ({
  useSystemStats: () => ({
    data: {
      ts: 'x',
      fleet: {},
      slots: [
        { slot_id: 'brain', inflight: 2 },
        { slot_id: 'agent', inflight: 1 },
      ],
    },
  }),
}))
vi.mock('@/api/hooks/useComfyui', () => ({
  useComfyui: () => ({ data: state.comfy }),
  useArbiter: () => ({ data: { arbiter: state.arbiter, mode: state.arbiter?.mode ?? null, skewMs: 0 } }),
  useComfyuiSwitchover: () => mutation(switchover),
  useComfyuiPin: () => mutation(vi.fn()),
  useComfyuiRenderCancel: () => mutation(vi.fn()),
  useComfyuiRestart: () => mutation(vi.fn()),
}))

await import('../shell.jsx')
await import('../now-band.jsx')
const OvNowBand = (window as any).OvNowBand as React.ComponentType<{ onGo: (id: string) => void }>

let host: HTMLDivElement
let root: ReturnType<typeof createRoot>

function mount(onGo: (id: string) => void = () => {}) {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  act(() => {
    root.render(
      <QueryClientProvider client={new QueryClient()}>
        <OvNowBand onGo={onGo} />
      </QueryClientProvider>,
    )
  })
}

const button = (label: string) =>
  [...host.querySelectorAll('button')].find((b) => b.textContent === label) as HTMLButtonElement | undefined

beforeEach(() => {
  state.arbiter = { mode: 'llm', switching: false, pinned: false, image_jobs: 0, phase: null }
  state.slots = { data: undefined, isPending: false, isError: false }
  state.comfy = undefined
  state.switchoverError = null
  switchover.mockClear()
  slotLoad.mockClear()
  // The bar is 1000px wide: 30 GB of 116 is ~259px, wide enough for a label.
  Object.defineProperty(HTMLElement.prototype, 'offsetWidth', { configurable: true, get: () => 1000 })
  ;(globalThis as any).ResizeObserver = class {
    cb: (e: any[]) => void
    constructor(cb: (e: any[]) => void) {
      this.cb = cb
    }
    observe() {
      this.cb([{ contentRect: { width: 1000 } }])
    }
    disconnect() {}
  }
})

afterEach(() => {
  act(() => root.unmount())
  document.body.innerHTML = ''
  delete (globalThis as any).ResizeObserver
  delete (HTMLElement.prototype as any).offsetWidth
})

describe('OvNowBand', () => {
  it('shows the mode and the facts line', () => {
    mount()
    expect(host.querySelector('.ov-now-mode')?.textContent).toBe('chat mode')
    const facts = host.querySelector('.ov-now-facts')?.textContent ?? ''
    expect(facts).toContain('5 of 13 slots loaded')
    expect(facts).toContain('2 serving · 79 tok/s')
    expect(facts).toContain('3 in flight')
    expect(facts).toContain('48.0 GB free of 116')
    expect(facts).toContain('gpu 0% · 47°C · 33 W')
  })

  it('draws one segment per budget slot plus the striped block, and a serving label with tok/s', () => {
    mount()
    const segs = [...host.querySelectorAll('.ov-mem-seg:not(.sys)')]
    // brain, agent and the image engine
    expect(segs).toHaveLength(3)
    expect(host.querySelectorAll('.ov-mem-seg.sys')).toHaveLength(1)
    expect(host.querySelector('.ov-mem-free')?.textContent).toBe('free · 48.0 GB')
    const brain = segs.find((s) => (s.getAttribute('title') ?? '').startsWith('brain'))!
    expect(brain.textContent).toContain('brain · 30.0G')
    expect(brain.textContent).toContain('tok/s')
    expect(host.querySelector('.ov-mem-leg')?.textContent).toContain('other guests 8.0G')
  })

  it('opens the slot page from a segment', () => {
    const onGo = vi.fn()
    mount(onGo)
    const brain = [...host.querySelectorAll('.ov-mem-seg')].find((s) => (s.getAttribute('title') ?? '').startsWith('brain'))!
    act(() => (brain as HTMLElement).click())
    expect(onGo).toHaveBeenCalledWith('slots/brain')
  })

  it('holds image mode only after the pin confirm', async () => {
    mount()
    act(() => button('Hold image mode')!.click())
    expect(switchover).not.toHaveBeenCalled()
    const confirm = [...document.body.querySelectorAll('button')].filter((b) => b.textContent === 'Hold image mode').pop()!
    await act(async () => confirm.click())
    expect(switchover).toHaveBeenCalledWith({ mode: 'generation', pin: true })
  })

  it('loads the first offline vulkan-group slot', async () => {
    mount()
    // the only offline gpu slot is `spare` (rocm); no vulkan one is offline, so the fallback wins
    await act(async () => button('Load spare')!.click())
    expect(slotLoad).toHaveBeenCalledWith('spare')
  })

  it('opens Agent Chat through the topbar toggle event', () => {
    mount()
    const seen = vi.fn()
    window.addEventListener('hal0:toggle-agent-chat', seen)
    act(() => button('Agent Chat')!.click())
    window.removeEventListener('hal0:toggle-agent-chat', seen)
    expect(seen).toHaveBeenCalledTimes(1)
  })

  it('offers Back to chat in image mode', () => {
    state.arbiter = { mode: 'img', switching: false, pinned: true, image_jobs: 0, phase: null }
    mount()
    expect(host.querySelector('.ov-now-mode')?.textContent).toBe('image mode')
    expect(host.querySelector('.ov-now-mode.img')).not.toBeNull()
    expect(button('Back to chat')).toBeDefined()
    expect(button('Hold image mode')).toBeUndefined()
  })

  it('shows dashes, not 0 of 0, while /api/slots is pending', () => {
    state.slots = { data: undefined, isPending: true, isError: false }
    mount()
    const facts = host.querySelector('.ov-now-facts')?.textContent ?? ''
    expect(facts).toContain('— of — slots loaded')
    expect(facts).toContain('— serving')
    expect(facts).not.toContain('0 of 0')
    expect(button('Load spare')).toBeUndefined()
  })

  it('shows the same dashes when /api/slots errored', () => {
    state.slots = { data: undefined, isPending: false, isError: true }
    mount()
    const facts = host.querySelector('.ov-now-facts')?.textContent ?? ''
    expect(facts).toContain('— of — slots loaded')
    expect(facts).not.toContain('0 of 0')
    expect(button('Load spare')).toBeUndefined()
  })

  it('colours the mode pip: amber while switching, grey when unknown, green for chat, blue for image', () => {
    const cls = () => host.querySelector('.ov-now-mode')!.className
    state.arbiter = { mode: 'llm', switching: true, pinned: false, image_jobs: 0, phase: null }
    mount()
    expect(cls()).toContain('warn')
    act(() => root.unmount())
    document.body.innerHTML = ''
    state.arbiter = null
    mount()
    expect(cls()).toContain('unk')
    act(() => root.unmount())
    document.body.innerHTML = ''
    state.arbiter = { mode: 'llm', switching: false, pinned: false, image_jobs: 0, phase: null }
    mount()
    expect(cls()).not.toMatch(/warn|unk|img/)
  })

  it('styles the warn and unknown pips with --warn and --fg-4', async () => {
    const { readFileSync } = await import(/* @vite-ignore */ ['node', 'fs'].join(':') as string)
    const css = readFileSync(`${(globalThis as any).process.cwd()}/src/dash/overview/overview.css`, 'utf8')
    expect(css).toMatch(/\.ov-now-mode\.warn \.ov-d\s*\{[^}]*var\(--warn\)/)
    expect(css).toMatch(/\.ov-now-mode\.unk \.ov-d\s*\{[^}]*var\(--fg-4\)/)
  })

  it('Back to chat while the ComfyUI editor has a render queued takes the force-confirm path', async () => {
    state.arbiter = { mode: 'img', switching: false, pinned: false, image_jobs: 0, phase: null }
    state.comfy = { queue: { running: 1, pending: 0 } }
    mount()
    act(() => button('Back to chat')!.click())
    expect(switchover).not.toHaveBeenCalled()
    const confirm = [...document.body.querySelectorAll('button')].filter((b) => /stop|back to chat/i.test(b.textContent ?? '') && b !== button('Back to chat')).pop()
    expect(confirm).toBeTruthy()
    await act(async () => confirm!.click())
    expect(switchover).toHaveBeenCalledWith({ mode: 'inference', force: true })
  })

  it('drops a failure message once the mode has moved on', async () => {
    state.arbiter = { mode: 'img', switching: false, pinned: false, image_jobs: 0, phase: null }
    state.switchoverError = new Error('gpu busy')
    mount()
    await act(async () => button('Back to chat')!.click())
    expect(host.querySelector('[role="alert"]')?.textContent).toBe('gpu busy')
    state.arbiter = { mode: 'llm', switching: false, pinned: false, image_jobs: 0, phase: null }
    await act(async () => {
      root.render(
        <QueryClientProvider client={new QueryClient()}>
          <OvNowBand onGo={() => {}} />
        </QueryClientProvider>,
      )
    })
    expect(host.querySelector('[role="alert"]')).toBeNull()
  })
})
