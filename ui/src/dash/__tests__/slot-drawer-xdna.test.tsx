// @vitest-environment happy-dom
//
// Slot drawer — xdna vs FLM gates on device 'npu'. The profile IS the slot's tune, so
// the drawer:
//   · shows the effective context window and where it came from, read-only
//     (slot view `ctx_effective` / `ctx_source`) — there is no ceiling input;
//   · offers "Edit profile" for a custom profile and "Duplicate & edit" for a
//     read-only seed, opening the Profiles drawer in place;
//   · previews what a profile swap changes (runtime, lane, context, thinking,
//     flags) and never refers to model flags, a divergence overlay or a
//     profile-driven device switch;
//   · never sends a tuning key or touches PATCH /defaults on Save.
//
// slot-modals.jsx is a window-globals dash module (bare `Drawer`/`FieldGroup`/
// `ConfirmDialog`/... identifiers resolve off `window`), so the globals are
// installed before the dynamic import — same pattern as model-drawer.test.tsx.
import React, { act } from 'react'
import * as ReactDOMClient from 'react-dom/client'
import { createPortal } from 'react-dom'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

;(globalThis as unknown as { window: typeof globalThis }).window = globalThis
;(globalThis as unknown as { React: typeof React }).React = React
;(globalThis as unknown as { ReactDOM: unknown }).ReactDOM = { ...ReactDOMClient, createPortal }
;(globalThis as unknown as { Icons: unknown }).Icons = new Proxy({}, { get: () => null })
;(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const { profilesBox, editCalls, restartCalls, createCalls } = vi.hoisted(() => ({
  profilesBox: { current: [] as Record<string, unknown>[] },
  editCalls: [] as unknown[],
  restartCalls: [] as string[],
  createCalls: [] as unknown[],
}))

const idle = { mutate: () => {}, mutateAsync: async () => ({}), isPending: false, reset: () => {} }

vi.mock('@/api/hooks/useSlots', () => ({
  useSlotEdit: () => ({
    ...idle,
    mutateAsync: async (b: unknown) => {
      editCalls.push(b)
      return {}
    },
  }),
  useSlotImagePull: () => idle,
  useSlotRestart: () => ({
    ...idle,
    mutate: (name: string) => {
      restartCalls.push(name)
    },
  }),
  useSlotLoad: () => idle,
  useSlotSwap: () => idle,
  useSlotResolved: () => ({ data: undefined }),
  useSlots: () => ({ data: [], isLoading: false }),
  useSlotRename: () => idle,
  useSlotConfig: () => ({ data: {} }),
  useSlotCreate: () => idle,
  useSlotDelete: () => idle,
}))
vi.mock('@/api/hooks/useXdnaFit', () => ({ useXdnaFit: () => ({ data: {} }) }))
vi.mock('@/api/hooks/useXdnaKnobs', () => ({
  useXdnaKnobs: () => ({ data: { pmodes: ['turbo'], env: [], host_xrt: { doc: '' } } }),
}))
vi.mock('@/api/hooks/useHardware', () => ({ useHardware: () => ({ data: undefined }) }))
vi.mock('@/api/hooks/useModels', () => ({
  useModels: () => ({ data: [], isLoading: false }),
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
  useProfiles: () => ({ data: profilesBox.current, isLoading: false, isError: false, error: null }),
  useProfileCreate: () => ({
    mutateAsync: async (b: unknown) => {
      createCalls.push(b)
      return b
    },
  }),
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
const EditSlotDrawer = (globalThis as unknown as { EditSlotDrawer: React.ComponentType<Record<string, unknown>> })
  .EditSlotDrawer


const XDNA = {
  name: 'gguf-npu', type: 'llm', device: 'npu', device_class: 'npu', binary: 'xdna', profile: 'xdna',
  model: 'qwen', model_id: 'qwen', model_default: 'qwen', group: 'chat', state: 'ready', port: 8093,
  n_gpu_layers: -1, threads: 0, image_pin: null, metrics: { ctx: 0 }, _configEnriched: true,
}
const FLM = { ...XDNA, name: 'flm', binary: 'flm', profile: '' }

function mount(slot: Record<string, unknown>) {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const root = createRoot(host)
  act(() => {
    root.render(
      React.createElement(
        QueryClientProvider,
        { client: qc },
        React.createElement(EditSlotDrawer, { open: true, slot, onClose: () => {} }),
      ),
    )
  })
  return { host, root }
}

const byId = (testid: string) => document.querySelector(`[data-testid="${testid}"]`) as HTMLElement | null
let fetchSpy: ReturnType<typeof vi.fn>

beforeEach(() => {
  profilesBox.current = []
  fetchSpy = vi.fn(async () => new Response(JSON.stringify({ models: [] }), { status: 200 }))
  vi.stubGlobal('fetch', fetchSpy)
})
afterEach(() => {
  document.body.innerHTML = ''
  vi.unstubAllGlobals()
})

describe('slot drawer on an xdna slot', () => {
  it('does not fetch the FLM catalogue and shows no FLM capability matrix', () => {
    const { root } = mount(XDNA)
    expect(fetchSpy.mock.calls.some((c) => String(c[0]).includes('/api/slots/flm/models'))).toBe(false)
    expect(byId('npu-capability-matrix')).toBeNull()
    // The slot drawer's body for an xdna slot is the one GGML slot form (Task 9).
    expect(byId('ggml-slot-form')?.getAttribute('data-mode')).toBe('edit')
    expect(byId('slot-xdna-block')).toBeNull()
    act(() => root.unmount())
  })

  it('still renders the FLM matrix and fetches the catalogue for an FLM anchor', () => {
    const { root } = mount(FLM)
    expect(fetchSpy.mock.calls.some((c) => String(c[0]).includes('/api/slots/flm/models'))).toBe(true)
    expect(byId('npu-capability-matrix')).not.toBeNull()
    act(() => root.unmount())
  })
})
