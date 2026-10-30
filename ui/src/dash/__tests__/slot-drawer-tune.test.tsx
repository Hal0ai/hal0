// @vitest-environment happy-dom
//
// Slot drawer: the Tune block (per-slot overrides of the profile), its Save
// body, Save as profile and Reset. Same harness as slot-drawer-profile-row.
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

const { profilesBox, modelsBox, toasts, editCalls, restartCalls, createCalls } = vi.hoisted(() => ({
  profilesBox: { current: [] as Record<string, unknown>[] },
  modelsBox: { current: [] as Record<string, unknown>[] },
  toasts: [] as string[],
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


const PROFILE = {
  name: 'agent-chat', flags: '-fa on', resolved_flags: '-fa on', ctx_size: 32768,
  enable_thinking: false, vision: 'auto', speculation: 'auto', reasoning_effort: 'default', reasoning_budget: -1,
  seed: false, intent: 'chat', used_by: ['chat'], supported_slot_types: ['llm'],
}
const MODEL = { id: 'qwen', mmproj: '/m/mmproj.gguf', capabilities: ['chat', 'vision'], draft: 'd.gguf' }
const EFFECTIVE = {
  ctx_size: 32768, enable_thinking: false, reasoning_effort: 'default', reasoning_budget: -1,
  vision: 'auto', speculation: 'auto', sources: {}, reasoning_effort_unavailable: false,
}
const SLOT = {
  name: 'chat', type: 'llm', device: 'gpu-rocm', profile: 'agent-chat',
  model: 'qwen', model_id: 'qwen', model_default: 'qwen', group: 'chat', state: 'ready', port: 8092,
  n_gpu_layers: -1, threads: 0, binary: '', image_pin: null, metrics: { ctx: 0 },
  ctx_effective: 32768, ctx_source: 'profile', _configEnriched: true,
  tune: {}, tune_effective: EFFECTIVE,
}

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
const click = (testid: string) => act(() => byId(testid)!.click())
const save = async () => {
  const btn = Array.from(document.querySelectorAll('button')).find((b) => b.textContent === 'Save') as HTMLButtonElement
  await act(async () => btn.click())
}
function typeInto(testid: string, value: string) {
  const el = byId(testid) as HTMLInputElement
  act(() => {
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
    setter.call(el, value)
    el.dispatchEvent(new Event('input', { bubbles: true }))
  })
}

beforeEach(() => {
  profilesBox.current = [PROFILE]
  modelsBox.current = [MODEL]
  editCalls.length = 0
  restartCalls.length = 0
  createCalls.length = 0
  toasts.length = 0
  ;(window as any).__hal0Toast = (m: string) => toasts.push(m)
})
afterEach(() => {
  document.body.innerHTML = ''
})

describe('slot drawer Tune block', () => {
  it('shows profile value pills and marks overrides', () => {
    const { root } = mount({ ...SLOT, tune: { vision: 'off' } })
    expect(byId('tune-ctx_size-profile')!.textContent).toContain('Profile · 32K')
    expect(byId('tune-enable_thinking-profile')!.textContent).toContain('Profile · off')
    expect(byId('tune-vision-off')!.getAttribute('aria-pressed')).toBe('true')
    expect(byId('tune-vision-profile')!.getAttribute('aria-pressed')).toBe('false')
    expect(byId('tune-dot-vision')).not.toBeNull()
    expect(byId('tune-dot-ctx_size')).toBeNull()
    expect(byId('tune-summary')!.textContent).toBe('1 override on profile agent-chat')
    act(() => root.unmount())
  })

  it('save sends tune and restarts for argv keys', async () => {
    const { root } = mount(SLOT)
    expect(byId('tune-summary')!.textContent).toContain('no overrides')
    click('tune-vision-off')
    await save()
    expect((editCalls[0] as any).body.tune).toEqual({ vision: 'off' })
    expect(restartCalls).toEqual(['chat'])
    act(() => root.unmount())
  })

  it('a numeric override is sent as a number', async () => {
    const { root } = mount(SLOT)
    typeInto('tune-ctx_size-input', '65536')
    typeInto('tune-reasoning_budget-input', '2048')
    click('tune-reasoning_effort-high')
    await save()
    expect((editCalls[0] as any).body.tune).toEqual({ ctx_size: 65536, reasoning_budget: 2048, reasoning_effort: 'high' })
    act(() => root.unmount())
  })

  it('an out-of-range number blocks Save and says why', async () => {
    const { root } = mount(SLOT)
    typeInto('tune-ctx_size-input', '64')
    expect(byId('tune-ctx_size-error')!.textContent).toContain('128')
    await save()
    expect(editCalls).toEqual([])
    act(() => root.unmount())
  })

  it('thinking-only change sends tune without restart', async () => {
    const { root } = mount(SLOT)
    click('tune-enable_thinking-on')
    await save()
    expect((editCalls[0] as any).body.tune).toEqual({ enable_thinking: true })
    expect(restartCalls).toEqual([])
    expect(toasts.join(' ')).toContain('new requests')
    act(() => root.unmount())
  })

  it('reset sends tune null', async () => {
    const { root } = mount({ ...SLOT, tune: { vision: 'off', enable_thinking: true } })
    click('tune-reset')
    expect(byId('tune-summary')!.textContent).toContain('no overrides')
    await save()
    expect((editCalls[0] as any).body.tune).toBeNull()
    act(() => root.unmount())
  })

  it('returning one row to the profile keeps the rest and nulls that key', async () => {
    const { root } = mount({ ...SLOT, tune: { vision: 'off', enable_thinking: true } })
    click('tune-vision-profile')
    await save()
    expect((editCalls[0] as any).body.tune).toEqual({ enable_thinking: true, vision: null })
    act(() => root.unmount())
  })

  it('a clean Save writes nothing', async () => {
    const { root } = mount({ ...SLOT, tune: { vision: 'off' } })
    await save()
    expect(editCalls).toEqual([])
    act(() => root.unmount())
  })

  it('disables thinking row when pinned by flags', () => {
    const { root } = mount({
      ...SLOT,
      tune_effective: { ...EFFECTIVE, sources: { enable_thinking: 'profile-flags' } },
    })
    expect((byId('tune-enable_thinking-on') as HTMLButtonElement).disabled).toBe(true)
    expect(byId('tune-enable_thinking-reason')!.textContent).toContain('--reasoning')
    act(() => root.unmount())
  })

  it('disables vision, speculation and effort with their reasons', () => {
    modelsBox.current = [{ id: 'qwen', capabilities: ['chat'] }]
    const { root } = mount({ ...SLOT, tune_effective: { ...EFFECTIVE, reasoning_effort_unavailable: true } })
    expect((byId('tune-vision-off') as HTMLButtonElement).disabled).toBe(true)
    expect(byId('tune-vision-reason')!.textContent).toContain('mmproj')
    expect((byId('tune-speculation-ngram') as HTMLButtonElement).disabled).toBe(true)
    expect(byId('tune-speculation-reason')).not.toBeNull()
    expect((byId('tune-reasoning_effort-high') as HTMLButtonElement).disabled).toBe(true)
    expect(byId('tune-reasoning_effort-reason')!.textContent).toContain('--reasoning-effort')
    act(() => root.unmount())
  })

  it('save as profile clones with effective tune then rebinds and clears', async () => {
    const { root } = mount({ ...SLOT, tune: { vision: 'off' } })
    click('tune-save-as-profile')
    expect(document.querySelector('.pf-form-title')!.textContent).toBe('Save as profile')
    expect((byId('pf-input-name') as HTMLInputElement).value).toBe('chat-agent-chat')
    expect((byId('pf-select-vision') as HTMLSelectElement).value).toBe('off')
    expect((byId('pf-input-ctx') as HTMLInputElement).value).toBe('32768')
    await act(async () => byId('pf-btn-submit')!.click())
    expect(createCalls).toHaveLength(1)
    expect(createCalls[0]).toMatchObject({ name: 'chat-agent-chat', vision: 'off', cloned_from: 'agent-chat', ctx_size: 32768 })
    // Then the drawer's normal Save ran: one PUT with the new profile and tune cleared.
    expect(editCalls).toHaveLength(1)
    expect((editCalls[0] as any).body).toEqual({ profile: 'chat-agent-chat', tune: null })
    expect(restartCalls).toEqual(['chat'])
    act(() => root.unmount())
  })

  it('save as profile is disabled with no overrides', () => {
    const { root } = mount(SLOT)
    expect((byId('tune-save-as-profile') as HTMLButtonElement).disabled).toBe(true)
    expect((byId('tune-reset') as HTMLButtonElement).disabled).toBe(true)
    act(() => root.unmount())
  })

  it('hidden on npu and embedding slots', () => {
    const npu = mount({ ...SLOT, device: 'npu' })
    expect(byId('slot-tune-block')).toBeNull()
    act(() => npu.root.unmount())
    document.body.innerHTML = ''
    const emb = mount({ ...SLOT, type: 'embedding' })
    expect(byId('slot-tune-block')).toBeNull()
    act(() => emb.root.unmount())
  })
})
