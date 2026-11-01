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
import { HALOGEN_DENIED, HALOGEN_KNOBS } from './fixtures/halogen-knobs'

;(globalThis as unknown as { window: typeof globalThis }).window = globalThis
;(globalThis as unknown as { React: typeof React }).React = React
;(globalThis as unknown as { ReactDOM: unknown }).ReactDOM = { ...ReactDOMClient, createPortal }
;(globalThis as unknown as { Icons: unknown }).Icons = new Proxy({}, { get: () => null })
;(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const { profilesBox, modelsBox, sysBox, toasts, feasCalls, editCalls, restartCalls, createCalls, updateCalls } = vi.hoisted(() => ({
  profilesBox: { current: [] as Record<string, unknown>[] },
  sysBox: { current: { backends: {}, hardware: {} } as Record<string, unknown> },
  modelsBox: { current: [] as Record<string, unknown>[] },
  toasts: [] as string[],
  feasCalls: [] as unknown[],
  editCalls: [] as unknown[],
  restartCalls: [] as string[],
  createCalls: [] as unknown[],
  updateCalls: [] as unknown[],
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
  useModelsFeasibility: () => ({ data: undefined, mutate: (b: unknown) => feasCalls.push(b) }),
}))
vi.mock('@/api/hooks/useLogs', () => ({ useSlotLogsStream: () => ({ lines: [] }) }))
vi.mock('@/api/hooks/useRuntimes', async () => {
  const actual = await vi.importActual<typeof import('@/api/hooks/useRuntimes')>('@/api/hooks/useRuntimes')
  return { ...actual, useSystemInfo: () => ({ data: sysBox.current, isLoading: false }) }
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
  useProfileUpdate: () => ({
    mutateAsync: async (b: unknown) => {
      updateCalls.push(b)
      return b
    },
  }),
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
  vision: 'auto', speculation: 'auto', sources: {}, reasoning_effort_unavailable: null,
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
  sysBox.current = { backends: {}, hardware: {} }
  editCalls.length = 0
  restartCalls.length = 0
  createCalls.length = 0
  updateCalls.length = 0
  toasts.length = 0
  feasCalls.length = 0
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

  it('can explicitly override inherited effort back to runner default', async () => {
    profilesBox.current = [{ ...PROFILE, reasoning_effort: 'high' }]
    const { root } = mount({ ...SLOT, tune_effective: { ...EFFECTIVE, reasoning_effort: 'high' } })
    click('tune-reasoning_effort-default')
    await save()
    expect((editCalls[0] as any).body.tune).toEqual({ reasoning_effort: 'default' })
    expect(restartCalls).toEqual(['chat'])
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
    // only the changed key goes out: enable_thinking is not re-sent
    expect((editCalls[0] as any).body.tune).toEqual({ vision: null })
    act(() => root.unmount())
  })

  it('Save does not re-send an unchanged enable_thinking the profile now pins', async () => {
    const { root } = mount({
      ...SLOT,
      tune: { enable_thinking: true },
      tune_effective: { ...EFFECTIVE, enable_thinking: false, sources: { enable_thinking: 'profile-flags' } },
    })
    click('tune-vision-off')
    await save()
    expect((editCalls[0] as any).body.tune).toEqual({ vision: 'off' })
    act(() => root.unmount())
  })

  it('a disabled row can still drop its own stale override, leaving the others', async () => {
    modelsBox.current = [{ id: 'qwen', capabilities: ['chat'], draft: 'd.gguf' }] // no mmproj
    const { root } = mount({ ...SLOT, tune: { vision: 'off', enable_thinking: true } })
    expect((byId('tune-vision-off') as HTMLButtonElement).disabled).toBe(true)
    const pill = byId('tune-vision-profile') as HTMLButtonElement
    expect(pill.disabled).toBe(false)
    click('tune-vision-profile')
    expect(byId('tune-summary')!.textContent).toBe('1 override on profile agent-chat')
    await save()
    expect((editCalls[0] as any).body.tune).toEqual({ vision: null })
    act(() => root.unmount())
  })

  it('a disabled row with no override keeps its Profile pill disabled', () => {
    modelsBox.current = [{ id: 'qwen', capabilities: ['chat'], draft: 'd.gguf' }]
    const { root } = mount(SLOT)
    expect((byId('tune-vision-profile') as HTMLButtonElement).disabled).toBe(true)
    act(() => root.unmount())
  })

  it('a profile-less slot shows the type seed values, not defaults', () => {
    profilesBox.current = [
      { ...PROFILE, name: 'chat', seed: true, flags: '-fa on -b 2048', ctx_size: 65536, enable_thinking: true, reasoning_effort: 'high', reasoning_budget: 512, vision: 'off' },
    ]
    const { root } = mount({ ...SLOT, profile: '', tune: { speculation: 'ngram' } })
    expect(byId('tune-ctx_size-profile')!.textContent).toContain('Profile · 64K')
    expect(byId('tune-enable_thinking-profile')!.textContent).toContain('Profile · on')
    expect(byId('tune-reasoning_effort-profile')!.textContent).toContain('Profile · high')
    expect(byId('tune-reasoning_budget-profile')!.textContent).toContain('Profile · 512')
    expect(byId('tune-summary')!.textContent).toBe('1 override on profile chat')
    act(() => root.unmount())
  })

  it('with no profile row at all, pills fall back to tune_effective profile-sourced values', () => {
    profilesBox.current = []
    const { root } = mount({
      ...SLOT,
      profile: '',
      tune_effective: { ...EFFECTIVE, ctx_size: 131072, reasoning_effort: 'low', sources: { ctx_size: 'profile', reasoning_effort: 'profile' } },
    })
    expect(byId('tune-ctx_size-profile')!.textContent).toContain('Profile · 128K')
    expect(byId('tune-reasoning_effort-profile')!.textContent).toContain('Profile · low')
    act(() => root.unmount())
  })

  it('Save as profile on a profile-less slot clones the type seed, keeping its flags', async () => {
    profilesBox.current = [{ ...PROFILE, name: 'chat', seed: true, flags: '-fa on -b 2048', ctx_size: 65536 }]
    const { root } = mount({ ...SLOT, name: 'agent', profile: '', tune: { vision: 'off' } })
    click('tune-save-as-profile')
    expect((byId('pf-input-name') as HTMLInputElement).value).toBe('agent-chat')
    expect((byId('pf-input-flags') as HTMLTextAreaElement).value).toBe('-fa on -b 2048')
    await act(async () => byId('pf-btn-submit')!.click())
    expect(createCalls[0]).toMatchObject({ name: 'agent-chat', flags: '-fa on -b 2048', cloned_from: 'chat', vision: 'off', ctx_size: 65536 })
    act(() => root.unmount())
  })

  it('Save as profile falls back to the type seed when the named profile row is gone', () => {
    profilesBox.current = [{ ...PROFILE, name: 'chat', seed: true, flags: '-fa on -b 2048' }]
    const { root } = mount({ ...SLOT, name: 'agent', profile: 'deleted-one', tune: { vision: 'off' } })
    click('tune-save-as-profile')
    expect((byId('pf-input-flags') as HTMLTextAreaElement).value).toBe('-fa on -b 2048')
    act(() => root.unmount())
  })

  it('Save as profile is disabled while the profile list has no usable row', () => {
    profilesBox.current = []
    const { root } = mount({ ...SLOT, profile: '', tune: { vision: 'off' } })
    expect((byId('tune-save-as-profile') as HTMLButtonElement).disabled).toBe(true)
    act(() => root.unmount())
  })

  it('feasibility probe sends the staged ctx override (wins), else the slot resolver', () => {
    const { root } = mount(SLOT)
    click('slot-model-swap')
    expect(feasCalls[0]).toEqual({ models: [{ model_id: 'qwen', profile: 'agent-chat', slot: 'chat' }] })
    act(() => root.unmount())
    document.body.innerHTML = ''
    feasCalls.length = 0
    const staged = mount({ ...SLOT, tune: { ctx_size: 8192 } })
    click('slot-model-swap')
    expect(feasCalls[0]).toEqual({ models: [{ model_id: 'qwen', ctx: 8192, profile: 'agent-chat', slot: 'chat' }] })
    act(() => staged.root.unmount())
  })

  it('feasibility probe ignores an invalid staged ctx and still uses the slot resolver', () => {
    const { root } = mount({ ...SLOT, profile: '', tune: {} })
    typeInto('tune-ctx_size-input', '64')
    click('slot-model-swap')
    expect(feasCalls[0]).toEqual({ models: [{ model_id: 'qwen', profile: 'chat', slot: 'chat' }] })
    act(() => root.unmount())
  })

  it('profile swap preview does not announce a context change the slot overrides', () => {
    profilesBox.current = [
      PROFILE,
      { ...PROFILE, name: 'big', ctx_size: 131072, enable_thinking: true },
    ]
    const { root } = mount({ ...SLOT, tune: { ctx_size: 8192 } })
    // pick the other profile through the profile select
    act(() => (document.querySelector('[data-testid="slot-profile"]') as HTMLElement).click())
    act(() => (document.querySelector('[data-option-id="big"]') as HTMLElement).click())
    const preview = byId('slot-profile-preview')!
    expect(preview.textContent).toContain('thinking')
    expect(preview.textContent).not.toContain('context')
    act(() => root.unmount())
  })

  it('a clean Save writes nothing', async () => {
    const { root } = mount({ ...SLOT, tune: { vision: 'off' } })
    await save()
    expect(editCalls).toEqual([])
    act(() => root.unmount())
  })

  it('disables thinking row when pinned by flags', () => {
    profilesBox.current = [{ ...PROFILE, flags: '--reasoning off' }]
    const { root } = mount({
      ...SLOT,
      tune_effective: { ...EFFECTIVE, sources: { enable_thinking: 'profile-flags' } },
    })
    expect((byId('tune-enable_thinking-on') as HTMLButtonElement).disabled).toBe(true)
    expect(byId('tune-enable_thinking-reason')!.textContent).toContain('--reasoning')
    act(() => root.unmount())
  })

  it('updates the thinking lock from the profile staged in the drawer', () => {
    profilesBox.current = [
      { ...PROFILE, flags: '--reasoning off' },
      { ...PROFILE, name: 'open', flags: '-fa on' },
    ]
    const { root } = mount({
      ...SLOT,
      tune_effective: { ...EFFECTIVE, sources: { enable_thinking: 'profile-flags' } },
    })
    expect((byId('tune-enable_thinking-on') as HTMLButtonElement).disabled).toBe(true)
    act(() => (document.querySelector('[data-testid="slot-profile"]') as HTMLElement).click())
    act(() => (document.querySelector('[data-option-id="open"]') as HTMLElement).click())
    expect((byId('tune-enable_thinking-on') as HTMLButtonElement).disabled).toBe(false)
    expect(byId('tune-enable_thinking-reason')).toBeNull()
    act(() => root.unmount())
  })

  it('disables vision and effort with their reasons, and leaves speculation open', () => {
    modelsBox.current = [{ id: 'qwen', capabilities: ['chat'] }]
    const { root } = mount({ ...SLOT, tune_effective: { ...EFFECTIVE, reasoning_effort_unavailable: 'runner build 11000 predates --reasoning-effort (needs b11840)' } })
    expect((byId('tune-vision-off') as HTMLButtonElement).disabled).toBe(true)
    expect(byId('tune-vision-reason')!.textContent).toContain('mmproj')
    // N-gram is model-free: a model with no drafter and no MTP heads can still take it.
    expect((byId('tune-speculation-ngram') as HTMLButtonElement).disabled).toBe(false)
    expect(byId('tune-speculation-reason')).toBeNull()
    expect((byId('tune-reasoning_effort-high') as HTMLButtonElement).disabled).toBe(true)
    // the server's own reason text, not a canned one
    expect(byId('tune-reasoning_effort-reason')!.textContent).toBe('runner build 11000 predates --reasoning-effort (needs b11840)')
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

  it('the drawer model picker trigger carries the thinking glyph and vision-off eye', () => {
    const { root } = mount({
      ...SLOT,
      tune: { enable_thinking: true, vision: 'off' },
      tune_effective: { ...EFFECTIVE, enable_thinking: true, vision: 'off' },
    })
    const trigger = byId('slot-model-swap')!
    expect(trigger.querySelector('[data-testid="thinking-mark"]')).not.toBeNull()
    expect(trigger.querySelector('.modality-icon.off')).not.toBeNull()
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

// ── halogen (Flash-Next) slots: family-aware picker + speculation row ──────
const RUNNERS = {
  standard: {
    title: 'Standard', tier: 'default', device_class: 'gpu', supported_backends: ['rocm', 'vulkan'],
    runtime_family: 'llama-server', state: 'installed',
  },
  halogen: {
    title: 'halogen (Flash-Next)', tier: 'optional', device_class: 'gpu', supported_backends: ['rocm'],
    runtime_family: 'halogen', state: 'installed', knobs: HALOGEN_KNOBS, knob_denied: HALOGEN_DENIED,
  },
}
const HALO_MODEL = { id: 'qwen-halo', capabilities: ['chat'], backends: ['halogen'], installed: true }
const LLAMA_MODEL = { id: 'qwen', capabilities: ['chat'], backends: ['rocm', 'vulkan'], installed: true }
const HALO_PROFILE = { ...PROFILE, name: 'halogen-chat', runner: 'halogen', used_by: [] }
const HALO_SLOT = { ...SLOT, binary: 'halogen', profile: 'halogen-chat', model: 'qwen-halo', model_id: 'qwen-halo' }

const specIds = () =>
  Array.from(document.querySelectorAll('[data-testid^="tune-speculation-"]'))
    .map((el) => el.getAttribute('data-testid')!.replace('tune-speculation-', ''))
    .filter((id) => id !== 'profile' && id !== 'reason')
const pickerOptionIds = () => {
  act(() => byId('slot-model-swap')!.click())
  return Array.from(document.querySelectorAll('[data-option-id]')).map((el) => el.getAttribute('data-option-id'))
}

describe('slot drawer on a halogen runtime', () => {
  beforeEach(() => {
    sysBox.current = { backends: RUNNERS, hardware: { rocm: true } }
    profilesBox.current = [PROFILE, HALO_PROFILE]
    modelsBox.current = [LLAMA_MODEL, HALO_MODEL]
  })

  it('offers a halogen slot only halogen models', () => {
    const { root } = mount(HALO_SLOT)
    const ids = pickerOptionIds()
    expect(ids).toContain('qwen-halo')
    expect(ids).not.toContain('qwen')
    act(() => root.unmount())
  })

  it('keeps halogen models out of a llama slot', () => {
    const { root } = mount({ ...SLOT, binary: 'standard' })
    const ids = pickerOptionIds()
    expect(ids).toContain('qwen')
    expect(ids).not.toContain('qwen-halo')
    act(() => root.unmount())
  })

  it('reads the family from the lane default when no runtime is pinned', () => {
    const { root } = mount({ ...SLOT, binary: '' })
    const ids = pickerOptionIds()
    expect(ids).toContain('qwen')
    expect(ids).not.toContain('qwen-halo')
    act(() => root.unmount())
  })

  it('follows the runner the profile carries when the slot pins none', () => {
    const { root } = mount({ ...HALO_SLOT, binary: '' })
    expect(pickerOptionIds()).not.toContain('qwen')
    expect(specIds()).toEqual(['auto', 'off'])
    act(() => root.unmount())
  })

  it('drops the n-gram speculation pill for halogen, keeps it for llama', () => {
    const halo = mount(HALO_SLOT)
    expect(specIds()).toEqual(['auto', 'off'])
    act(() => halo.root.unmount())
    document.body.innerHTML = ''
    const llama = mount({ ...SLOT, binary: 'standard' })
    expect(specIds()).toEqual(['auto', 'off', 'ngram'])
    act(() => llama.root.unmount())
  })

  it("shows the server's reason and disables speculation when tune_unavailable names it", () => {
    const reason = 'ngram: this engine has no n-gram drafter (auto or off)'
    const { root } = mount({
      ...HALO_SLOT,
      tune_effective: { ...EFFECTIVE, tune_unavailable: { speculation: reason } },
    })
    expect(byId('tune-speculation-reason')!.textContent).toBe(reason)
    expect((byId('tune-speculation-auto') as HTMLButtonElement).disabled).toBe(true)
    act(() => root.unmount())
  })

  it('shows the server reason even before the catalogue says the family', () => {
    sysBox.current = { backends: {}, hardware: {} }
    const reason = 'ngram: this engine has no n-gram drafter (auto or off)'
    const { root } = mount({ ...SLOT, tune_effective: { ...EFFECTIVE, tune_unavailable: { speculation: reason } } })
    expect(byId('tune-speculation-reason')!.textContent).toBe(reason)
    act(() => root.unmount())
  })
})

// ── halogen engine settings: the profile's HALOGEN_* flags, edited in place ─
const engineBlock = () => byId('slot-engine-block')
const engineField = (env: string) => byId(`engine-input-${env}`) as HTMLInputElement | null

describe('slot drawer engine settings (halogen)', () => {
  const CUSTOM = { ...HALO_PROFILE, flags: 'HALOGEN_KV_SLOTS=4', resolved_flags: 'HALOGEN_KV_SLOTS=4' }
  const SEED = { ...CUSTOM, name: 'halogen-seed', seed: true }
  beforeEach(() => {
    sysBox.current = { backends: RUNNERS, hardware: { rocm: true } }
    modelsBox.current = [LLAMA_MODEL, HALO_MODEL]
  })

  it('shows a seed profile read-only and offers to duplicate it', () => {
    profilesBox.current = [PROFILE, SEED]
    const { root } = mount({ ...HALO_SLOT, profile: 'halogen-seed' })
    expect(byId('pf-engine-settings')).not.toBeNull()
    expect(engineField('HALOGEN_KV_SLOTS')!.value).toBe('4')
    expect(engineField('HALOGEN_KV_SLOTS')!.disabled).toBe(true)
    expect(byId('slot-engine-apply')).toBeNull()
    expect(byId('slot-engine-duplicate')).not.toBeNull()
    act(() => root.unmount())
  })

  it('Duplicate on the engine block opens the profile drawer on a custom copy of the seed', () => {
    profilesBox.current = [PROFILE, SEED]
    const { root } = mount({ ...HALO_SLOT, profile: 'halogen-seed' })
    click('slot-engine-duplicate')
    expect((byId('pf-input-name') as HTMLInputElement).value).toBe('halogen-seed-custom')
    act(() => root.unmount())
  })

  it('saves a custom profile edit through the profile update, never the slot', async () => {
    profilesBox.current = [PROFILE, CUSTOM]
    const { root } = mount(HALO_SLOT)
    expect(engineField('HALOGEN_KV_SLOTS')!.disabled).toBe(false)
    expect(byId('slot-engine-duplicate')).toBeNull()
    expect((byId('slot-engine-apply') as HTMLButtonElement).disabled).toBe(true)
    typeInto('engine-input-HALOGEN_KV_SLOTS', '8')
    expect(updateCalls).toHaveLength(0)
    await act(async () => byId('slot-engine-apply')!.click())
    expect(updateCalls).toEqual([{ name: 'halogen-chat', body: { flags: 'HALOGEN_KV_SLOTS=8' } }])
    expect(editCalls).toHaveLength(0)
    act(() => root.unmount())
  })

  it('writes a toggle and an enum choice into the same flags text', async () => {
    profilesBox.current = [PROFILE, CUSTOM]
    const { root } = mount(HALO_SLOT)
    click('engine-opt-HALOGEN_MAX_TOK-32768')
    click('engine-switch-HALOGEN_KV8')
    await act(async () => byId('slot-engine-apply')!.click())
    expect((updateCalls[0] as { body: { flags: string } }).body.flags).toBe(
      'HALOGEN_KV_SLOTS=4 HALOGEN_MAX_TOK=32768 HALOGEN_KV8=1',
    )
    act(() => root.unmount())
  })

  it('names a rejected value in the block and holds the save', async () => {
    profilesBox.current = [PROFILE, CUSTOM]
    const { root } = mount(HALO_SLOT)
    typeInto('engine-input-HALOGEN_KV_SLOTS', '100')
    expect(byId('pf-engine-error')!.textContent).toContain('HALOGEN_KV_SLOTS')
    expect((byId('slot-engine-apply') as HTMLButtonElement).disabled).toBe(true)
    await act(async () => byId('slot-engine-apply')!.click())
    expect(updateCalls).toHaveLength(0)
    act(() => root.unmount())
  })

  it('keeps an unsaved engine edit out of the slot Save', async () => {
    profilesBox.current = [PROFILE, CUSTOM]
    const { root } = mount(HALO_SLOT)
    typeInto('engine-input-HALOGEN_KV_SLOTS', '8')
    await save()
    expect(editCalls).toHaveLength(0)
    act(() => root.unmount())
  })

  it('renders no block for a llama slot, nor for a halogen runner that serves no knobs', () => {
    profilesBox.current = [PROFILE, CUSTOM]
    const llama = mount({ ...SLOT, binary: 'standard' })
    expect(engineBlock()).toBeNull()
    expect(byId('pf-engine-settings')).toBeNull()
    act(() => llama.root.unmount())
    document.body.innerHTML = ''
    sysBox.current = {
      backends: { ...RUNNERS, halogen: { ...RUNNERS.halogen, knobs: [] } },
      hardware: { rocm: true },
    }
    const bare = mount(HALO_SLOT)
    expect(engineBlock()).toBeNull()
    act(() => bare.root.unmount())
  })
})
