// @vitest-environment happy-dom
// NPU Models panel: GGUF rows with NPU fit. The FLM catalogue list is gone (the FLM lane's
// model dropdown covers download and launch).
import React, { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
;(globalThis as unknown as { window: typeof globalThis }).window = globalThis
;(globalThis as unknown as { React: typeof React }).React = React
;(globalThis as unknown as { Icons: unknown }).Icons = new Proxy({}, { get: () => null })
;(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const { box } = vi.hoisted(() => ({
  box: {
    drawer: null as null | { open: boolean; mode: string; initialModel?: string; slot?: { name: string } },
    toasts: [] as string[],
  },
}))

vi.mock('@/api/hooks/useXdnaFit', () => ({
  useXdnaFit: () => ({
    data: {
      'qwen3-4b': { xdna_path: 'fused', reason: 'all ops fit' },
      'qwen35-0.8b': { xdna_path: 'per-op', reason: 'blk.0.ssm_out.weight is Q4_K, not Q5_K' },
      'gemma4-e4b': { xdna_path: 'per-op', reason: "architecture 'gemma4' is not 'qwen35'" },
    },
  }),
}))
vi.mock('@/api/hooks/useModels', () => ({
  useModels: () => ({
    data: [
      { id: 'qwen3-4b', type: 'llm', size: '2.5 GB', architecture: 'granite' },
      { id: 'qwen35-0.8b', type: 'llm', size: '0.6 GB' },
      { id: 'gemma4-e4b', type: 'llm', size: '6.9 GB', architecture: 'gemma4' },
    ],
  }),
}))
vi.mock('../npu/GgmlSlotDrawer.jsx', () => ({
  GgmlSlotDrawer: (p: { open: boolean; mode: string; initialModel?: string; slot?: { name: string } }) => {
    box.drawer = p
    return p.open ? <div data-testid="drawer" data-mode={p.mode} data-model={p.initialModel ?? ''} data-slot={p.slot?.name ?? ''} /> : null
  },
}))

const { NpuModelsPanel } = await import('../npu/NpuModelsPanel')

const slots = [
  { name: 'flm', binary: '', device_class: 'npu', type: 'llm' },
  { name: 'npu-gguf', binary: 'xdna', device_class: 'npu', model: 'qwen3-4b' },
]
let host: HTMLDivElement
let root: Root
const q = (id: string) => host.querySelector(`[data-testid="${id}"]`) as HTMLElement | null
let occNow: unknown

function Harness() {
  return <NpuModelsPanel slots={slots as never} occ={occNow as never} />
}

beforeEach(() => {
  box.toasts.length = 0
  occNow = undefined
  ;(window as unknown as { __hal0Toast: (m: string) => void }).__hal0Toast = (m) => box.toasts.push(m)
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  act(() => root.render(<Harness />))
})
afterEach(() => {
  act(() => root.unmount())
  host.remove()
})

describe('NpuModelsPanel', () => {
  it('no longer lists the FLM catalogue: no per-model download or filter controls', () => {
    expect(q('flm-models')).toBeNull()
    expect(host.querySelector('[data-testid^="flm-download-"]')).toBeNull()
    expect(host.querySelector('[data-testid^="flm-filter-"]')).toBeNull()
    expect(q('gguf-models')).not.toBeNull()
  })

  it('shows the GGUF architecture from the registry row', () => {
    expect(q('gguf-arch-qwen3-4b')!.textContent).toBe('granite')
    expect(q('gguf-arch-qwen35-0.8b')!.textContent).toBe('—')
  })

  it('GGUF rows show fit, size, used-by with an edit link; "New slot" opens create with the model', () => {
    expect(q('gguf-qwen3-4b-fit')!.textContent).toBe('✓ fused')
    expect(q('gguf-qwen3-4b-why')).toBeNull()
    expect(q('gguf-row-qwen3-4b')!.textContent).toContain('2.5 GB')
    const link = q('gguf-used-qwen3-4b-npu-gguf') as HTMLAnchorElement
    expect(link.getAttribute('href')).toBe('#slots/npu-gguf')
    act(() => q('gguf-new-qwen3-4b')!.click())
    expect(box.drawer).toMatchObject({ open: true, mode: 'create', initialModel: 'qwen3-4b' })
    expect(q('drawer')!.getAttribute('data-model')).toBe('qwen3-4b')
  })

  it('a per-op row names its blocker in one chip; only a requant-fixable row gets "copy fix"', async () => {
    expect(q('gguf-qwen35-0.8b-fit')!.textContent).toBe('per-op')
    expect(q('gguf-qwen35-0.8b-why')!.textContent).toBe('ssm_out q4_k')
    expect(q('gguf-gemma4-e4b-why')!.textContent).toBe('gemma4 arch')
    expect(q('gguf-gemma4-e4b-fix')).toBeNull()
    expect(host.textContent).not.toContain('is not')
    const copied: string[] = []
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: (t: string) => { copied.push(t); return Promise.resolve() } } })
    await act(async () => {
      q('gguf-qwen35-0.8b-fix')!.click()
    })
    expect(copied[0]).toContain('llama-quantize --tensor-type ssm_out=q5_K')
  })

  it('a used-by xdna slot opens the GGML drawer in edit mode in the page instead of navigating', () => {
    window.location.hash = '#slots/npu/models'
    act(() => q('gguf-used-qwen3-4b-npu-gguf')!.click())
    expect(box.drawer).toMatchObject({ open: true, mode: 'edit' })
    expect(q('drawer')!.getAttribute('data-slot')).toBe('npu-gguf')
    expect(window.location.hash).toBe('#slots/npu/models')
  })

  it('"New slot" is disabled, with the reason, while the ggml-xdna image is not pulled', () => {
    occNow = { npu_engines: { xdna: { pulled: false } } }
    act(() => root.render(<Harness />))
    const btn = q('gguf-new-qwen3-4b') as HTMLButtonElement
    expect(btn.disabled).toBe(true)
    expect(btn.title).toBe('The ggml-xdna runner image is not pulled yet.')
    occNow = { npu_engines: { xdna: { pulled: null } } }
    act(() => root.render(<Harness />))
    expect((q('gguf-new-qwen3-4b') as HTMLButtonElement).disabled).toBe(false)
  })
})
