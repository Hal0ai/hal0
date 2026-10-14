// @vitest-environment happy-dom
//
// Slot drawer — the Profile row (ADR-0029). The profile IS the slot's tune, so
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

const MY_TUNE = {
  name: 'my-tune', flags: '-fa on -b 2048', resolved_flags: '-fa on -b 2048',
  ctx_size: 65536, enable_thinking: false, vision: 'auto', mtp: 'auto',
  seed: false, intent: 'My tune', used_by: ['chat', 'other'], supported_slot_types: ['llm'],
}
const SEED = {
  name: 'rocm', flags: '-fa on', resolved_flags: '-fa on',
  ctx_size: 32768, enable_thinking: true, vision: 'auto', mtp: 'auto',
  seed: true, intent: 'MoE agents', used_by: [], supported_slot_types: ['llm'],
}
const SLOT = {
  name: 'chat', type: 'llm', device: 'gpu-rocm', profile: 'my-tune',
  model: 'qwen', model_id: 'qwen', model_default: 'qwen', group: 'chat', state: 'ready', port: 8092,
  n_gpu_layers: -1, threads: 0, binary: '', image_pin: null, metrics: { ctx: 0 },
  ctx_effective: 65536, ctx_source: 'profile', _configEnriched: true,
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

beforeEach(() => {
  profilesBox.current = [MY_TUNE, SEED]
  editCalls.length = 0
  restartCalls.length = 0
  createCalls.length = 0
})
afterEach(() => {
  document.body.innerHTML = ''
})

describe('slot drawer profile row (ADR-0029)', () => {
  it('shows the effective context and its source, with no ceiling input', () => {
    const { root } = mount(SLOT)
    const hint = byId('slot-ctx-effective')!
    expect(hint.textContent).toContain('65,536')
    expect(hint.textContent).toContain('set by profile my-tune')
    expect(document.body.textContent).not.toContain('Context (ceiling)')
    act(() => root.unmount())
  })

  it('names the other source kinds and says so when nothing resolved', () => {
    const native = mount({ ...SLOT, ctx_source: 'native', ctx_effective: 131072 })
    expect(byId('slot-ctx-effective')!.textContent).toContain("the model's native window")
    act(() => native.root.unmount())
    document.body.innerHTML = ''
    const none = mount({ ...SLOT, ctx_source: null, ctx_effective: null })
    expect(byId('slot-ctx-effective')!.textContent).toContain('not resolved yet')
    act(() => none.root.unmount())
  })

  it('offers "Edit profile" for a custom profile and warns when other slots share it', () => {
    const { root } = mount(SLOT)
    const edit = byId('slot-profile-edit') as HTMLButtonElement
    expect(edit.textContent).toContain('Edit profile')
    expect(edit.disabled).toBe(false)
    expect(byId('slot-profile-shared')!.textContent).toContain('other')
    act(() => edit.click())
    expect(document.querySelector('.pf-form-title')!.textContent).toBe('Edit · my-tune')
    act(() => root.unmount())
  })

  it('offers "Duplicate & edit" for a seed and opens the fork form', () => {
    const { root } = mount({ ...SLOT, profile: 'rocm', ctx_effective: 32768 })
    const edit = byId('slot-profile-edit') as HTMLButtonElement
    expect(edit.textContent).toContain('Duplicate & edit')
    act(() => edit.click())
    expect(document.querySelector('.pf-form-title')!.textContent).toBe('Edit a copy · rocm')
    expect((byId('pf-input-name') as HTMLInputElement).value).toBe('rocm-custom')
    expect(byId('slot-profile-shared')).toBeNull()
    act(() => root.unmount())
  })

  it('disables the edit button when the slot has no resolvable profile', () => {
    const { root } = mount({ ...SLOT, profile: '' })
    expect((byId('slot-profile-edit') as HTMLButtonElement).disabled).toBe(true)
    act(() => root.unmount())
  })

  it('a clean Save writes nothing and never restarts', async () => {
    const { root } = mount(SLOT)
    const save = Array.from(document.querySelectorAll('button')).find((b) => b.textContent === 'Save') as HTMLButtonElement
    await act(async () => save.click())
    expect(editCalls).toEqual([])
    expect(restartCalls).toEqual([])
    act(() => root.unmount())
  })

  it('renders none of the retired model-flag / divergence / device-switch UI', () => {
    const { root } = mount(SLOT)
    for (const id of ['slot-profile-diverges', 'slot-profile-device-switch', 'slot-profile-strands-model']) {
      expect(byId(id)).toBeNull()
    }
    expect(byId('slot-launch-tune-note')!.textContent).toContain('profile')
    act(() => root.unmount())
  })
})

describe('slot drawer conversion-blocked badge (ADR-0029)', () => {
  const FIX = 'run `hal0 profile migrate` to see why, edit the slot file, then run `sudo -u hal0 hal0 profile migrate --apply`'

  it('shows the badge in the header and the full reason, pre-conversion note and fix in the body', () => {
    const { root } = mount({
      ...SLOT,
      conversion_blocked: { reason: 'extra_args did not parse', fix: FIX, running_pre_conversion: true },
    })
    expect(byId('slot-conversion-blocked-chat')!.textContent).toBe('Conversion blocked')
    const detail = byId('slot-conversion-blocked-detail')!
    expect(detail.textContent).toContain('extra_args did not parse')
    expect(byId('slot-conversion-blocked-running')!.textContent).toContain('pre-conversion tune')
    expect(byId('slot-conversion-blocked-fix')!.textContent).toBe(FIX)
    act(() => root.unmount())
  })

  it('omits the pre-conversion note when the slot is not running its old command', () => {
    const { root } = mount({
      ...SLOT,
      conversion_blocked: { reason: 'r', fix: FIX, running_pre_conversion: false },
    })
    expect(byId('slot-conversion-blocked-detail')).not.toBeNull()
    expect(byId('slot-conversion-blocked-running')).toBeNull()
    act(() => root.unmount())
  })

  it('shows nothing on a slot the conversion did not block', () => {
    const { root } = mount(SLOT)
    expect(byId('slot-conversion-blocked-chat')).toBeNull()
    expect(byId('slot-conversion-blocked-detail')).toBeNull()
    act(() => root.unmount())
  })
})
