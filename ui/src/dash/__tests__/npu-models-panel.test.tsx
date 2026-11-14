// @vitest-environment happy-dom
// NPU Models panel: FLM catalog actions and GGUF rows with NPU fit.
import React, { act, useState } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
;(globalThis as unknown as { window: typeof globalThis }).window = globalThis
;(globalThis as unknown as { React: typeof React }).React = React
;(globalThis as unknown as { Icons: unknown }).Icons = new Proxy({}, { get: () => null })
;(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const { box } = vi.hoisted(() => ({
  box: {
    pullStart: vi.fn(),
    pullState: 'idle',
    drawer: null as null | { open: boolean; mode: string; initialModel?: string },
    toasts: [] as string[],
  },
}))

vi.mock('@/api/hooks/useSlots', () => ({
  useSlotConfig: () => ({ data: { npu: { chat: true }, model: { default: 'gemma4-it:e4b' } } }),
}))
vi.mock('@/api/hooks/useFlmCatalog', () => ({
  useFlmCatalog: () => ({
    data: {
      probe_ok: true,
      models: [
        { model: 'gemma4-it:e4b', installed: true, capabilities: ['chat'], family: 'gemma4' },
        { model: 'qwen3:8b', installed: false, capabilities: ['chat'], family: 'qwen3' },
        { model: 'whisper-v3:turbo', installed: true, capabilities: ['stt'], family: 'whisper' },
        { model: 'embed-gemma:300m', installed: true, capabilities: ['embed'], family: 'embed' },
      ],
    },
  }),
}))
vi.mock('@/api/hooks/useXdnaFit', () => ({
  useXdnaFit: () => ({
    data: {
      'qwen3-4b': { xdna_path: 'fused', reason: 'all ops fit' },
      'qwen35-0.8b': { xdna_path: 'per-op', reason: 'blk.0.ssm_out.weight is Q4_K, not Q5_K' },
    },
  }),
}))
vi.mock('@/api/hooks/useModels', () => ({
  useModels: () => ({
    data: [
      { id: 'qwen3-4b', type: 'llm', size: '2.5 GB', architecture: 'qwen3' },
      { id: 'qwen35-0.8b', type: 'llm', size: '0.6 GB' },
    ],
  }),
  usePullJob: () => ({ start: box.pullStart, state: box.pullState, modelId: null, pct: null, inFlight: box.pullState === 'running', error: null }),
}))
vi.mock('../npu/GgmlSlotDrawer.jsx', () => ({
  GgmlSlotDrawer: (p: { open: boolean; mode: string; initialModel?: string }) => {
    box.drawer = p
    return p.open ? <div data-testid="drawer" data-mode={p.mode} data-model={p.initialModel ?? ''} /> : null
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
const switched: string[] = []
let draftNow: Record<string, unknown> = {}

function Harness() {
  const [draft, setDraft] = useState<Record<string, unknown>>({})
  draftNow = draft
  return <NpuModelsPanel slots={slots as never} setFlmDraft={setDraft as never} onSwitchTab={(t: string) => switched.push(t)} />
}

beforeEach(() => {
  box.pullState = 'idle'
  box.pullStart.mockReset()
  box.pullStart.mockResolvedValue(undefined)
  box.toasts.length = 0
  switched.length = 0
  draftNow = {}
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
  it('tags usage: anchor chat model, bundled stt and embed rows', () => {
    expect(q('flm-row-gemma4-it:e4b')!.textContent).toContain('flm chat')
    expect(q('flm-row-whisper-v3:turbo')!.textContent).toContain('flm stt')
    expect(q('flm-row-embed-gemma:300m')!.textContent).toContain('flm embed')
    expect(q('flm-row-qwen3:8b')!.textContent).not.toContain('flm ')
  })

  it('"Use for chat" stages the model, switches to Lanes and toasts', () => {
    act(() => q('flm-use-qwen3:8b')!.click())
    expect(draftNow).toEqual({ chatModel: 'qwen3:8b' })
    expect(switched).toEqual(['lanes'])
    expect(box.toasts.length).toBe(1)
  })

  it('the current chat model has no "Use for chat"; chat-less rows never do', () => {
    expect(q('flm-use-gemma4-it:e4b')).toBeNull()
    expect(q('flm-use-whisper-v3:turbo')).toBeNull()
  })

  it('Download starts a pull for an uninstalled row only', () => {
    expect(q('flm-download-gemma4-it:e4b')).toBeNull()
    act(() => q('flm-download-qwen3:8b')!.click())
    expect(box.pullStart).toHaveBeenCalledWith('qwen3:8b')
  })

  it('disables every Download while a pull is in flight', () => {
    box.pullState = 'running'
    act(() => root.render(<Harness />))
    expect((q('flm-download-qwen3:8b') as HTMLButtonElement).disabled).toBe(true)
  })

  it('shows the GGUF architecture from the registry row', () => {
    expect(q('gguf-row-qwen3-4b')!.textContent).toContain('qwen3')
  })

  it('filters by installed and capability', () => {
    act(() => q('flm-filter-installed')!.click())
    expect(q('flm-row-qwen3:8b')).toBeNull()
    act(() => q('flm-cap-stt')!.click())
    expect(q('flm-row-whisper-v3:turbo')).not.toBeNull()
    expect(q('flm-row-gemma4-it:e4b')).toBeNull()
  })

  it('GGUF rows show fit, size, used-by with an edit link; "New slot" opens create with the model', () => {
    expect(q('gguf-fit-qwen3-4b')!.textContent).toBe('fused')
    expect(q('gguf-row-qwen3-4b')!.textContent).toContain('2.5 GB')
    const link = q('gguf-used-qwen3-4b-npu-gguf') as HTMLAnchorElement
    expect(link.getAttribute('href')).toBe('#slots/npu-gguf')
    act(() => q('gguf-new-qwen3-4b')!.click())
    expect(box.drawer).toMatchObject({ open: true, mode: 'create', initialModel: 'qwen3-4b' })
    expect(q('drawer')!.getAttribute('data-model')).toBe('qwen3-4b')
  })

  it('shows the requant command once, for per-op rows, and Copy falls back to selecting on rejection', async () => {
    expect(q('gguf-reason-qwen35-0.8b')!.textContent).toContain('Q4_K')
    const cmd = q('npu-requant-cmd')!
    expect(cmd.textContent).toContain('llama-quantize')
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: () => Promise.reject(new Error('denied')) } })
    await act(async () => {
      q('npu-requant-copy')!.click()
    })
    expect(window.getSelection()!.toString()).toContain('llama-quantize')
  })
})
