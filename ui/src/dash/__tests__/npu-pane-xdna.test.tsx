// @vitest-environment happy-dom
//
// NPU occupancy card — xdna (ggml-xdna llama-server) slot support.
// An xdna slot shares the NPU array but is never a trio member: it gets its
// own XdnaSlotCard in the RIGHT region, carries the xdna_path/xdna_reason fit
// chip + pmode chip + npu_shared_with warning line, and a model picker scoped
// to GGUF models. The grid gets a `shared` hatch treatment whenever more than
// one holder actually claims columns.
import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { beforeEach, describe, expect, it, vi } from 'vitest'

;(globalThis as unknown as { window: typeof globalThis }).window = globalThis
;(globalThis as unknown as { React: typeof React }).React = React
;(globalThis as unknown as { Icons: unknown }).Icons = new Proxy({}, { get: () => null })
;(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const { editCalls, restartCalls, modelsBox, occBox } = vi.hoisted(() => ({
  editCalls: [] as { name: string; body: Record<string, unknown> }[],
  restartCalls: [] as string[],
  modelsBox: { current: [] as unknown[] },
  occBox: { current: { slots: [] as unknown[] } },
}))

vi.mock('@/api/hooks/useNpuOccupancy', () => ({ useNpuOccupancy: () => ({ data: occBox.current }) }))
vi.mock('@/api/hooks/useStatsHardware', () => ({ useStatsHardware: () => ({ data: {} }) }))
vi.mock('@/api/hooks/useModels', () => ({ useModels: () => ({ data: modelsBox.current }) }))
vi.mock('@/api/hooks/useModelsFeasibility', () => ({
  useModelsFeasibility: () => ({ data: undefined, mutate: () => {} }),
}))
vi.mock('@/api/hooks/useSlots', () => ({
  useSlots: () => ({ data: undefined, isLoading: false }),
  useSlotLoad: () => ({ mutate: () => {} }),
  useSlotUnload: () => ({ mutate: () => {} }),
  useSlotSwap: () => ({ mutate: () => {} }),
  useSlotConfig: () => ({ data: {} }),
  useSlotRestart: () => ({
    isPending: false,
    mutate: (name: string) => restartCalls.push(name),
  }),
  useSlotEdit: () => ({
    isPending: false,
    mutate: (
      args: { name: string; body: Record<string, unknown> },
      opts?: { onSuccess?: () => void },
    ) => {
      editCalls.push(args)
      opts?.onSuccess?.()
    },
  }),
}))

const { NpuOccupancyCard } = await import('../npu-pane.jsx')

const GGUF_A = {
  id: 'llama3.1:8b',
  longName: 'llama3.1:8b',
  capabilities: ['chat'],
  backends: ['vulkan', 'rocm', 'cpu'],
  provider_effective: 'llama-server',
  installed: true,
}
const GGUF_B = {
  id: 'qwen2.5:7b',
  longName: 'qwen2.5:7b',
  capabilities: ['chat'],
  backends: ['vulkan', 'rocm', 'cpu'],
  provider_effective: 'llama-server',
  installed: true,
}

const XDNA_SLOT = {
  name: 'xdna-0',
  type: 'llm',
  device: 'npu',
  device_class: 'npu',
  binary: 'xdna',
  modelDefault: 'llama3.1:8b',
  state: 'ready',
  xdna_path: 'fused',
  xdna_reason: 'tensors match the fused kernel layout',
  pmode: 'default',
  npu_shared_with: [],
}

function mount(slots: unknown[]) {
  const host = document.createElement('div')
  document.body.appendChild(host)
  act(() => {
    createRoot(host).render(<NpuOccupancyCard slots={slots} />)
  })
  return host
}

beforeEach(() => {
  document.body.innerHTML = ''
  editCalls.length = 0
  restartCalls.length = 0
  modelsBox.current = [GGUF_A, GGUF_B]
  occBox.current = { slots: [] }
})

describe('NPU card — xdna slot', () => {
  it('renders an XdnaSlotCard with status dot, model picker and metrics', () => {
    const host = mount([XDNA_SLOT])
    const trigger = host.querySelector('[data-testid="xdna-model-xdna-0"]') as HTMLButtonElement
    expect(trigger).not.toBeNull()
    expect(trigger.textContent).toContain('llama3.1:8b')
    expect(host.querySelector('.cslot.xdna-slot .ldot.running')).not.toBeNull()
  })

  it('renders the fit chip from xdna_path with xdna_reason as the title', () => {
    const host = mount([XDNA_SLOT])
    const chip = host.querySelector('[data-testid="xdna-fit-xdna-0"]')!
    expect(chip.textContent).toBe('fused')
    expect(chip.getAttribute('title')).toBe('tensors match the fused kernel layout')
  })

  it('renders "—" for a null xdna_path/pmode rather than fabricating one', () => {
    const slot = { ...XDNA_SLOT, xdna_path: null, xdna_reason: null, pmode: null }
    const host = mount([slot])
    expect(host.querySelector('[data-testid="xdna-fit-xdna-0"]')!.textContent).toBe('—')
    expect(host.querySelector('[data-testid="xdna-pmode-xdna-0"]')!.textContent?.trim()).toBe('—')
  })

  it('renders a shared-with warning line when npu_shared_with is non-empty', () => {
    const slot = { ...XDNA_SLOT, npu_shared_with: ['flm'] }
    const host = mount([slot])
    const warn = host.querySelector('[data-testid="xdna-shared-xdna-0"]')!
    expect(warn.textContent).toContain('shared with flm')
  })

  it('picking a model PUTs [model].default on the xdna slot itself, then restarts it', () => {
    const host = mount([XDNA_SLOT])
    const trigger = host.querySelector('[data-testid="xdna-model-xdna-0"]') as HTMLButtonElement
    act(() => trigger.dispatchEvent(new MouseEvent('click', { bubbles: true })))
    const opt = host.querySelector('[data-option-id="qwen2.5:7b"]')!
    act(() => opt.dispatchEvent(new MouseEvent('click', { bubbles: true })))
    expect(editCalls).toEqual([{ name: 'xdna-0', body: { model: { default: 'qwen2.5:7b' } } }])
    expect(restartCalls).toEqual(['xdna-0'])
  })

  it('grid gets the shared hatch treatment when an xdna slot reports npu_shared_with', () => {
    const slot = { ...XDNA_SLOT, npu_shared_with: ['flm'] }
    const host = mount([slot])
    const grid = host.querySelector('[data-testid="aie-grid"]')!
    expect(grid.getAttribute('data-shared')).toBe('true')
    expect(grid.className).toContain('shared')
  })

  it('grid has no shared hatch for a lone xdna slot with nothing to share', () => {
    const host = mount([XDNA_SLOT])
    const grid = host.querySelector('[data-testid="aie-grid"]')!
    expect(grid.getAttribute('data-shared')).toBe('false')
  })

  it('an xdna slot alongside an FLM anchor does not steal the trio pills', () => {
    const anchor = { name: 'flm', type: 'llm', device: 'npu', device_class: 'npu', modelDefault: 'qwen3:4b', state: 'ready' }
    const host = mount([anchor, XDNA_SLOT])
    expect(host.querySelector('[data-testid="npu-model-flm"]')).not.toBeNull()
    expect(host.querySelector('[data-testid="xdna-model-xdna-0"]')).not.toBeNull()
  })
})
