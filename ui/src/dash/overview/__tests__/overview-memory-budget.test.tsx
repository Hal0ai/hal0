// @vitest-environment happy-dom
//
// The Overview Now band's unified-memory bar reads the backend memory budget (ADR-0039) through
// useMemoryMapModel: RAM figures from `mm.ram`, the bar total from the pool, the striped block
// from the budget's other guests, free from the budget. These assertions were carried over from
// the retired dashboard Memory card test.
//
// The real memory-map model runs here: only the data hooks are mocked.
import React from 'react'
import { createRoot } from 'react-dom/client'
import { act } from 'react-dom/test-utils'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

;(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true
;(globalThis as any).React = React
;(globalThis as any).FieldInfoIcon = () => null
// primitives.jsx (Modal) reads `Icons` off the window at render time.
;(globalThis as any).Icons = new Proxy({}, { get: () => null })

// Reference-box shape: occupants (30 + 12 + the 4.4 image engine = 46.4) overshoot the live
// counter (43.4), so the budget's free (72.6) is larger than the 69.6 the drawn blocks leave.
const budget = (gpuOver: Record<string, unknown> | null = {}, ramOver: Record<string, unknown> = {}) => ({
  gpu:
    gpuOver === null
      ? null
      : {
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
          other_guests_gb: 0,
          attribution_excess_gb: 3,
          free_gb: 72.6,
          ...gpuOver,
        },
  ram: { limit_gb: 105.5, used_gb: 16.1, free_gb: 89.4, source: 'container', ...ramOver },
  render_free_gb: gpuOver === null ? null : 60,
})

const SLOTS = [
  { name: 'brain', type: 'llm', device: 'gpu-rocm', model: 'm-brain', state: 'serving' },
  { name: 'agent', type: 'llm', device: 'gpu-rocm', model: 'm-agent', state: 'serving' },
  { name: 'img', type: 'image', device: 'gpu-rocm', model: 'm-img', state: 'serving' },
]

const state: { memory: any; host: any } = { memory: budget(), host: undefined }
const mutation = () => ({ mutateAsync: vi.fn(), mutate: vi.fn(), isPending: false })

vi.mock('@/api/hooks/useSlots', () => ({
  useSlots: () => ({ data: SLOTS, isPending: false, isError: false }),
  useSlotLoad: () => mutation(),
}))
vi.mock('@/api/hooks/useHardware', () => ({
  useHardware: () => ({ data: { platform_label: 'test' }, isLoading: false }),
  hasGpuAccess: () => true,
  hasNpuAccess: () => false,
}))
vi.mock('@/api/hooks/useStatsHardware', () => ({
  useStatsHardware: () => ({
    data: { memory: state.memory, per_slot: {}, host: state.host },
    isLoading: false,
  }),
  useMemoryBudget: () => ({ data: state.memory }),
}))
vi.mock('@/api/hooks/useStatsPower', () => ({ useStatsPower: () => ({ data: undefined }) }))
vi.mock('@/api/hooks/useProxmoxSettings', () => ({
  useProxmoxSettings: () => ({ data: null, isLoading: false }),
}))
vi.mock('@/api/hooks/useSystemStats', () => ({ useSystemStats: () => ({ data: undefined }) }))
vi.mock('@/api/hooks/useComfyui', () => ({
  useComfyui: () => ({ data: undefined }),
  useArbiter: () => ({ data: { arbiter: null, mode: null, skewMs: 0 } }),
  useComfyuiSwitchover: () => mutation(),
  useComfyuiPin: () => mutation(),
  useComfyuiRenderCancel: () => mutation(),
  useComfyuiRestart: () => mutation(),
}))

await import('../shell.jsx')
const { OvNowBand } = await import('../now-band.jsx')

let root: ReturnType<typeof createRoot>

function render(memory: any, pveHost?: any): HTMLElement {
  state.memory = memory
  state.host = pveHost
  const host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  act(() => {
    root.render(
      <QueryClientProvider client={new QueryClient()}>
        <OvNowBand onGo={() => {}} />
      </QueryClientProvider>,
    )
  })
  return host
}

beforeEach(() => {
  // A 1000px bar, so blocks are wide enough to carry labels.
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

const striped = (host: HTMLElement) => host.querySelector('.ov-mem-seg.sys') as HTMLElement | null
const freeLabel = (host: HTMLElement) => host.querySelector('.ov-mem-free')?.textContent
const legend = (host: HTMLElement) => host.querySelector('.ov-mem-leg')?.textContent ?? ''
const slotTitles = (host: HTMLElement) =>
  [...host.querySelectorAll('.ov-mem-seg[role="link"]')].map((e) => e.getAttribute('title') || '')

describe('Now band memory bar (ADR-0039 budget)', () => {
  it('striped block is other guests on a gpu box', () => {
    const host = render(budget({ used_gb: 60, other_guests_gb: 20, attribution_excess_gb: 0, free_gb: 56 }))
    const block = striped(host)!
    expect(block).not.toBeNull()
    expect(block.getAttribute('title')).toBe('other guests · 20.0 GB')
    // 20 of the 116 GB pool, and not a RAM-derived number (RAM used is 16.1)
    expect(parseFloat(block.style.width)).toBeCloseTo((20 / 116) * 100, 5)
    expect(legend(host)).toContain('other guests 20.0G')
    expect(legend(host)).not.toMatch(/system/)
    // its free figure is the budget's, not total minus the drawn blocks (116 - 60)
    expect(freeLabel(host)).toBe('free · 56.0 GB')
  })

  it('free label is the budget free', () => {
    const host = render(budget())
    expect(freeLabel(host)).toBe('free · 72.6 GB')
    // nothing else holds the pool on the reference box, so no striped block
    expect(striped(host)).toBeNull()
  })

  it('names the image engine and says per-slot figures are estimated', () => {
    const titles = slotTitles(render(budget()))
    expect(titles.find((t) => t.startsWith('image engine'))).toMatch(/^image engine · 4\.4 GB · /)
    expect(titles.find((t) => t.startsWith('brain'))).toMatch(/^brain · 30\.0 GB \(estimated\) · /)
  })

  it('calls the engine an estimate only when the backend says it is one', () => {
    const engineTitle = (host: HTMLElement) => slotTitles(host).find((t) => t.startsWith('image engine'))
    expect(engineTitle(render(budget()))).toMatch(/^image engine · 4\.4 GB · /)
    act(() => root.unmount())
    document.body.innerHTML = ''
    expect(engineTitle(render(budget({ image_engine_estimated: true })))).toMatch(
      /^image engine · 4\.4 GB \(estimated\) · /,
    )
  })

  it('stays RAM-based on a box without a GPU', () => {
    const host = render(budget(null))
    // the bar is this box's RAM: system + page cache = RAM in use beyond the models
    expect(striped(host)!.getAttribute('title')).toBe('system + page cache · 16.1 GB')
    expect(legend(host)).toContain('system 16.1G')
    expect(freeLabel(host)).toBe('free · 89.4 GB')
    expect(legend(host)).toContain('106 GB pool')
  })
})

describe('Now band memory bar on a Proxmox-configured GPU box', () => {
  // Host: 128 GB DIMMs, 90 GB in use, 38 GB free. This box's occupants hold
  // 30 + 12 + 4.4 = 46.4 GB of the pool, so the host's "others" are 43.6 GB:
  // that already includes the other guests' GTT.
  const pve = {
    configured: true,
    ok: true,
    host_mem_total_mb: 131072,
    host_mem_used_mb: 92160,
    host_mem_free_mb: 38912,
  }
  // Other guests hold 20 GB of this box's pool: drawn as their own block on the pool bar,
  // but inside the host's "others" once the bar is host-wide.
  const withOtherGuests = () =>
    budget({ used_gb: 66.4, other_guests_gb: 20, attribution_excess_gb: 0, free_gb: 49.6 })

  it("does not draw the pool's other guests on top of the host's others", () => {
    const h = render(withOtherGuests(), pve)
    const blocks = [...h.querySelectorAll<HTMLElement>('.ov-mem-seg.sys')]
    expect(blocks.map((b) => b.getAttribute('title'))).toEqual(['proxmox host (other tenants) · 43.6 GB'])
    expect(legend(h)).not.toMatch(/other guests/)
    expect(legend(h)).toContain('proxmox host 43.6G')
  })

  it("the free label is what the host-wide bar leaves, not the pool free", () => {
    const h = render(withOtherGuests(), pve)
    // 128 total - (46.4 occupants + 43.6 host others) = 38.0, the host's free
    expect(freeLabel(h)).toBe('free · 38.0 GB')
    expect(legend(h)).toContain('128 GB pool')
  })

  it('the same budget without a host is the pool bar, other guests drawn', () => {
    const h = render(withOtherGuests())
    expect(striped(h)!.getAttribute('title')).toBe('other guests · 20.0 GB')
    expect(freeLabel(h)).toBe('free · 49.6 GB')
  })
})
