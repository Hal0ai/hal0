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
    edits: [] as Array<{ name: string; body: unknown }>,
    pullStart: vi.fn(),
    drawer: null as null | { open: boolean; mode: string; slot?: { name: string } },
  },
}))

vi.mock('@/api/hooks/useSlots', () => ({
  useSlotLoad: () => ({ mutate: (n: string) => box.loads.push(n), mutateAsync: async (n: string) => { box.loads.push(n); return {} } }),
  useSlotUnload: () => ({ mutate: (n: string) => box.unloads.push(n) }),
  useSlotEdit: () => ({ isPending: false, mutateAsync: async (a: { name: string; body: unknown }) => { box.edits.push(a); return {} } }),
  useSlotRestart: () => ({ mutate: (n: string) => box.restarts.push(n), mutateAsync: async (n: string) => { box.restarts.push(n); return {} } }),
}))
vi.mock('@/api/hooks/useModels', () => ({
  useModels: () => ({ data: [{ id: 'qwen3-4b', type: 'llm', quant: 'Q4_K_M', installed: true }, { id: 'qwen35-0.8b', type: 'llm', installed: true }, { id: 'llama-74b', type: 'llm', installed: false }] }),
}))
vi.mock('@/api/hooks/useXdnaFit', () => ({
  useXdnaFit: () => ({
    data: {
      'qwen3-4b': { xdna_path: 'fused', reason: '' },
      'qwen35-0.8b': { xdna_path: 'per-op', reason: 'x' },
      'llama-74b': { xdna_path: 'per-op', reason: '', npu_ok: false, npu_note: '74B is too large for the NPU' },
      'bge-reranker': { xdna_path: null, reason: 'not gguf' },
    },
  }),
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
  box.edits.length = 0
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
    expect(q('ggml-npu-gguf-fit')!.textContent).toBe('fused')
    expect(q('ggml-npu-gguf-why')).toBeNull()
    expect(q('ggml-card-npu-gguf')!.textContent).toContain('1 knob')
    expect(q('ggml-mx-npu-gguf')!.textContent).toContain('21')
    expect(q('ggml-mx-npu-gguf')!.textContent).toContain('310 ms ttft')
    expect(q('ggml-mx-npu-gguf-2')!.textContent).toBe('stopped')
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

  it('a per-op card names its blocker, and "copy fix" shows only for the ssm_out case', () => {
    render()
    expect(q('ggml-npu-gguf-fix')).toBeNull()
    expect(q('ggml-npu-gguf-2-fit')!.textContent).toBe('per-op')
    expect(q('ggml-npu-gguf-2-why')!.textContent).toBe('Per-op path: ssm_out is Q4_K, not Q5_K. Correct, slower decode.')
    expect(q('ggml-npu-gguf-2-fix')).not.toBeNull()
  })

  it('each card has a model dropdown; impractical rows are hidden until "show all"', () => {
    render()
    const pick = q('ggml-model-pick-npu-gguf')!
    expect(pick).not.toBeNull()
    act(() => pick.click())
    const opts = () => Array.from(host.querySelectorAll('[role="option"]')).map((o) => o.textContent ?? '')
    expect(opts().some((t) => t.includes('qwen35-0.8b'))).toBe(true)
    expect(opts().some((t) => t.includes('llama-74b'))).toBe(false)
    expect(opts().some((t) => t.includes('bge-reranker'))).toBe(false)
    act(() => q('ggml-pick-all-npu-gguf')!.click())
    expect(opts().some((t) => t.includes('llama-74b') && t.includes('74B is too large'))).toBe(true)
    expect(q('ggml-pick-dl-llama-74b')).not.toBeNull()
  })

  it('picking a model edits the slot and restarts it when running', async () => {
    render()
    act(() => q('ggml-model-pick-npu-gguf')!.click())
    await act(async () => { host.querySelector<HTMLElement>('[data-option-id="qwen35-0.8b"]')!.click() })
    expect(box.edits).toEqual([{ name: 'npu-gguf', body: { model: { default: 'qwen35-0.8b' } } }])
    expect(box.restarts).toEqual(['npu-gguf'])
  })

  it('picking a model on a stopped slot edits without restarting', async () => {
    render()
    act(() => q('ggml-model-pick-npu-gguf-2')!.click())
    await act(async () => { host.querySelector<HTMLElement>('[data-option-id="qwen3-4b"]')!.click() })
    expect(box.edits.length).toBe(1)
    expect(box.restarts).toEqual([])
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
