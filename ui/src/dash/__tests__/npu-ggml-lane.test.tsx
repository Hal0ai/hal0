// @vitest-environment happy-dom
// GGML lane: one card per xdna slot, image-pull gate, pmode mismatch, Add opens the create drawer.
import React, { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
;(globalThis as unknown as { window: typeof globalThis }).window = globalThis
;(globalThis as unknown as { React: typeof React }).React = React
;(globalThis as unknown as { Icons: unknown }).Icons = new Proxy({}, { get: () => null })
;(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const { box } = vi.hoisted(() => ({
  box: {
    loads: [] as string[],
    unloads: [] as string[],
    restarts: [] as string[],
    pullStart: vi.fn(),
    drawer: null as null | { open: boolean; mode: string; slot?: { name: string } },
  },
}))

vi.mock('@/api/hooks/useSlots', () => ({
  useSlotLoad: () => ({ mutate: (n: string) => box.loads.push(n) }),
  useSlotUnload: () => ({ mutate: (n: string) => box.unloads.push(n) }),
  useSlotRestart: () => ({ mutate: (n: string) => box.restarts.push(n) }),
}))
vi.mock('@/api/hooks/useRunnerImages', () => ({
  useRunnerImagePullJob: () => ({ start: box.pullStart, state: 'idle', pct: null, error: null, inFlight: false }),
}))
vi.mock('../npu/GgmlSlotDrawer.jsx', () => ({
  GgmlSlotDrawer: (p: { open: boolean; mode: string; slot?: { name: string } }) => {
    box.drawer = p
    return p.open ? <div data-testid="drawer" data-mode={p.mode} data-slot={p.slot?.name ?? ''} /> : null
  },
}))

const { GgmlLane } = await import('../npu/GgmlLane')

const slots = [
  { name: 'npu-gguf', binary: 'xdna', device_class: 'npu', device: 'npu', type: 'llm', state: 'serving', port: 8101, model: 'qwen3-4b', profile: 'xdna', xdna_path: 'fused', xdna_reason: 'all ops fit', pmode: 'turbo', tune: { ctx_size: 16384 }, metrics: { toks: 21.4, ttft: 310, mem: 3.2 } },
  { name: 'npu-gguf-2', binary: 'xdna', device_class: 'npu', device: 'npu', type: 'llm', state: 'off', port: 8102, model: 'qwen35-0.8b', profile: 'xdna', xdna_path: 'per-op', xdna_reason: 'blk.0.ssm_out.weight is Q4_K, not Q5_K', pmode: null, tune: {}, metrics: {} },
  { name: 'flm', binary: '', device_class: 'npu', type: 'llm' },
  { name: 'gpu', binary: 'llama-server', device_class: 'gpu', type: 'llm' },
]
let host: HTMLDivElement
let root: Root
const q = (id: string) => host.querySelector(`[data-testid="${id}"]`) as HTMLElement | null
const render = (props: Record<string, unknown> = {}) =>
  act(() => root.render(<GgmlLane slots={slots as never} occ={{ npu_engines: { xdna: { pulled: true } } } as never} hw={{ npu_pmode: 'turbo' } as never} {...props} />))

beforeEach(() => {
  box.loads.length = 0
  box.unloads.length = 0
  box.restarts.length = 0
  box.pullStart.mockReset()
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})
afterEach(() => {
  act(() => root.unmount())
  host.remove()
})

describe('GgmlLane', () => {
  it('renders one card per xdna slot and none for FLM or GPU slots', () => {
    render()
    expect(host.querySelectorAll('[data-testid^="ggml-card-"]').length).toBe(2)
    expect(q('ggml-card-npu-gguf')!.textContent).toContain(':8101')
    expect(q('ggml-card-npu-gguf')!.textContent).toContain('qwen3-4b')
    expect(q('ggml-fit-npu-gguf')!.textContent).toBe('fused')
    expect(q('ggml-fit-npu-gguf')!.getAttribute('title')).toBe('all ops fit')
    expect(q('ggml-card-npu-gguf')!.textContent).toContain('1 override')
    expect(q('ggml-card-npu-gguf')!.textContent).toContain('21')
  })

  it('pulled:false disables Add and shows the pull banner; Pull starts the xdna image', () => {
    render({ occ: { npu_engines: { xdna: { pulled: false } } } })
    expect((q('ggml-add') as HTMLButtonElement).disabled).toBe(true)
    act(() => q('ggml-pull')!.click())
    expect(box.pullStart).toHaveBeenCalledWith('xdna')
  })

  it('pulled:null is unknown: Add enabled, no banner', () => {
    render({ occ: { npu_engines: { xdna: { pulled: null } } } })
    expect((q('ggml-add') as HTMLButtonElement).disabled).toBe(false)
    expect(q('ggml-pull')).toBeNull()
  })

  it('a slot pmode different from the host pmode shows "wants <pmode>"', () => {
    render({ hw: { npu_pmode: 'balanced' } })
    expect(q('ggml-pmode-npu-gguf')!.textContent).toContain('wants turbo')
    expect(q('ggml-pmode-npu-gguf-2')).toBeNull()
  })

  it('Get the fused path links to the Models tab only for the ssm_out case', () => {
    render()
    expect(q('ggml-fused-npu-gguf')).toBeNull()
    const a = q('ggml-fused-npu-gguf-2') as HTMLAnchorElement
    expect(a.getAttribute('href')).toBe('#slots/npu/models')
  })

  it('Add opens the drawer in create mode; Edit opens it in edit mode with the slot', () => {
    render()
    act(() => q('ggml-add')!.click())
    expect(q('drawer')!.getAttribute('data-mode')).toBe('create')
    act(() => q('ggml-card-npu-gguf')!.querySelector<HTMLElement>('[title="Edit"]')!.click())
    expect(q('drawer')!.getAttribute('data-mode')).toBe('edit')
    expect(q('drawer')!.getAttribute('data-slot')).toBe('npu-gguf')
  })

  it('empty lane still offers Add', () => {
    render({ slots: [] })
    expect(host.querySelectorAll('[data-testid^="ggml-card-"]').length).toBe(0)
    expect(q('ggml-add')).not.toBeNull()
  })
})
