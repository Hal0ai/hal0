// @vitest-environment happy-dom
//
// Image slots and the Model picker. ComfyUI files no longer live in the model
// registry (GET /api/models lists no image rows), so the lists the slot surfaces
// build from useModels() — the slot drawer's Model select and the slot card's swap
// popover — would be empty for an image slot. For an image slot both show the
// current selection and a link to AI Capabilities → Image, where the selection is
// made; every other slot type keeps its picker.
//
// slot-modals.jsx is a window-globals dash module, so the globals are installed
// before the dynamic import — same pattern as slot-drawer-profile-row.test.tsx.
import React, { act } from 'react'
import * as ReactDOMClient from 'react-dom/client'
import { createPortal } from 'react-dom'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'

;(globalThis as unknown as { window: typeof globalThis }).window = globalThis
;(globalThis as unknown as { React: typeof React }).React = React
;(globalThis as unknown as { ReactDOM: unknown }).ReactDOM = { ...ReactDOMClient, createPortal }
;(globalThis as unknown as { Icons: unknown }).Icons = new Proxy({}, { get: () => null })
;(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
// The swap popover's fit check reads data.jsx's window.parseSizeGB.
;(globalThis as unknown as { parseSizeGB: (s: string) => number }).parseSizeGB = (s) => parseFloat(s) || 0

const idle = { mutate: () => {}, mutateAsync: async () => ({}), isPending: false, reset: () => {} }

const { modelsBox } = vi.hoisted(() => ({ modelsBox: { current: [] as Record<string, unknown>[] } }))

vi.mock('@/api/hooks/useSlots', () => ({
  useSlotEdit: () => idle,
  useSlotImagePull: () => idle,
  useSlotRestart: () => idle,
  useSlotLoad: () => idle,
  useSlotSwap: () => idle,
  useSlotResolved: () => ({ data: undefined }),
  useSlots: () => ({ data: [], isLoading: false }),
  useSlotRename: () => idle,
}))
vi.mock('@/api/hooks/useHardware', () => ({ useHardware: () => ({ data: undefined }) }))
vi.mock('@/api/hooks/useModels', () => ({
  useModels: () => ({ data: modelsBox.current, isLoading: false }),
  usePullJob: () => ({ ...idle, start: async () => {}, state: 'idle', reset: () => {} }),
}))
vi.mock('@/api/hooks/useModelsFeasibility', () => ({
  useModelsFeasibility: () => ({ data: undefined, mutate: () => {} }),
}))
vi.mock('@/api/hooks/useLogs', () => ({ useSlotLogsStream: () => ({ lines: [] }) }))
vi.mock('@/api/hooks/useRuntimes', async () => {
  const actual = await vi.importActual<typeof import('@/api/hooks/useRuntimes')>('@/api/hooks/useRuntimes')
  return { ...actual, useSystemInfo: () => ({ data: { backends: {}, hardware: {} }, isLoading: false }) }
})
vi.mock('@/api/hooks/useRunnerImages', () => ({
  useRunnerImages: () => ({ data: { images: [], families: [] } }),
  useRunnerImagePullJob: () => ({ state: 'idle', inFlight: false, error: null, start: async () => {} }),
}))
vi.mock('@/api/hooks/useProfiles', () => ({
  useProfiles: () => ({ data: [], isLoading: false, isError: false, error: null }),
  useProfileCreate: () => ({ mutateAsync: async (b: unknown) => b }),
  useProfileUpdate: () => ({ mutateAsync: async (b: unknown) => b }),
  useProfileDelete: () => ({ mutateAsync: async () => undefined }),
  useProfileExport: () => ({ mutateAsync: async () => ({}) }),
  useProfileImport: () => ({ mutateAsync: async () => ({}) }),
}))
vi.mock('@/api/hooks/useMeta', async () => {
  const actual = await vi.importActual<typeof import('@/api/hooks/useMeta')>('@/api/hooks/useMeta')
  return { ...actual, useMeta: () => ({ data: undefined }), useMetaEnums: () => actual.resolveMetaEnums(undefined) }
})

await import('../primitives.jsx')
;(globalThis as unknown as Record<string, unknown>).ModelDrawer = () => null
;(globalThis as unknown as Record<string, unknown>).DeleteSlotDialog = () => null
await import('../slot-modals.jsx')
const { EditSlotDrawer, InlineSwapPopover } = globalThis as unknown as {
  EditSlotDrawer: React.ComponentType<Record<string, unknown>>
  InlineSwapPopover: React.ComponentType<Record<string, unknown>>
}

const IMG_SLOT = {
  name: 'img', type: 'image', device: 'gpu-rocm', profile: 'comfyui',
  model: 'ckpt/mine/sdxl-custom', model_id: 'ckpt/mine/sdxl-custom', group: 'img', state: 'ready', port: 8188,
  n_gpu_layers: -1, threads: 0, binary: '', image_pin: null, metrics: { ctx: 0 },
  ctx_effective: null, ctx_source: null, _configEnriched: true,
}
const LLM_SLOT = {
  name: 'chat', type: 'llm', device: 'gpu-rocm', profile: '',
  model: 'qwen', model_id: 'qwen', model_default: 'qwen', group: 'chat', state: 'ready', port: 8092,
  n_gpu_layers: -1, threads: 0, binary: '', image_pin: null, metrics: { ctx: 0 },
  ctx_effective: null, ctx_source: null, _configEnriched: true,
}
const QWEN = {
  id: 'qwen', name: 'qwen', ns: 'pulled', installed: true, type: 'llm', capabilities: ['chat'],
  backends: ['rocm'], size_bytes: 1_000_000_000, hf_repo: 'org/qwen',
}

function mountInQuery(element: React.ReactElement) {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const root = createRoot(host)
  act(() => {
    root.render(React.createElement(QueryClientProvider, { client: qc }, element))
  })
  return { host, root }
}

const byId = (testid: string) => document.querySelector(`[data-testid="${testid}"]`) as HTMLElement | null

afterEach(() => {
  document.body.innerHTML = ''
  modelsBox.current = []
})

describe('slot drawer Model row', () => {
  it('shows the current selection and an AI Capabilities link for an image slot', () => {
    const { root } = mountInQuery(
      React.createElement(EditSlotDrawer, { open: true, slot: IMG_SLOT, onClose: () => {} }),
    )
    expect(byId('slot-model-swap')).toBeNull()
    expect(byId('slot-image-model')!.textContent).toContain('ckpt/mine/sdxl-custom')
    const link = byId('slot-image-model-link') as HTMLAnchorElement
    expect(link.getAttribute('href')).toBe('#settings/capabilities')
    expect(link.textContent).toContain('AI Capabilities')
    act(() => root.unmount())
  })

  it('keeps the Model select for a chat slot', () => {
    modelsBox.current = [QWEN]
    const { root } = mountInQuery(
      React.createElement(EditSlotDrawer, { open: true, slot: LLM_SLOT, onClose: () => {} }),
    )
    expect(byId('slot-model-swap')).not.toBeNull()
    expect(byId('slot-image-model')).toBeNull()
    expect(byId('slot-image-model-link')).toBeNull()
    act(() => root.unmount())
  })
})

describe('slot card swap popover', () => {
  it('lists no registry models for an image slot, only the selection and the link', () => {
    const { root } = mountInQuery(
      React.createElement(InlineSwapPopover, { slot: IMG_SLOT, open: true, onClose: () => {}, onPick: () => {} }),
    )
    expect(document.querySelectorAll('.swap-pop-item')).toHaveLength(0)
    expect(byId('swap-image-model')!.textContent).toContain('ckpt/mine/sdxl-custom')
    const link = byId('swap-image-model-link') as HTMLAnchorElement
    expect(link.getAttribute('href')).toBe('#settings/capabilities')
    act(() => root.unmount())
  })

  it('still lists compatible models for a chat slot', () => {
    modelsBox.current = [QWEN]
    const { root } = mountInQuery(
      React.createElement(InlineSwapPopover, { slot: LLM_SLOT, open: true, onClose: () => {}, onPick: () => {} }),
    )
    expect(document.querySelectorAll('.swap-pop-item')).toHaveLength(1)
    expect(byId('swap-image-model')).toBeNull()
    expect(byId('swap-image-model-link')).toBeNull()
    act(() => root.unmount())
  })
})
