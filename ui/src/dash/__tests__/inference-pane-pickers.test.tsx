// @vitest-environment happy-dom
//
// Inference pane slot-card pickers (card-dropdowns Tasks 1 + 2):
//   Task 1 — the card model picker (ModelPicker) migrated from a native
//   <select> to RichSelect: multi-line rows (quant tag, modality tag, size +
//   used-by + engine desc line) and a lazy GTT fit-chip batch on first open.
//   Task 2 — the card profile pill migrated to RichSelect: rich profile rows
//   filtered to the slot type, an "✎ Edit slot…" row keeping the old
//   open-the-editor gesture, and a consequence confirm that owns the only
//   write. Driven through SlotCards, since the confirm is mounted by the card
//   LIST (it must not sit inside a `.scard`) — so these tests exercise the
//   real card→pane wiring, not a card in isolation.
//
// inference-pane.jsx is a window-globals dash module (bare `React.Fragment`/
// `React.cloneElement` identifiers resolve off `window` under vitest's
// classic-JSX esbuild config) — same pattern as model-drawer.test.tsx.
import React from 'react'
import { createRoot } from 'react-dom/client'
import { act } from 'react-dom/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

;(globalThis as unknown as { window: typeof globalThis }).window = globalThis
;(globalThis as unknown as { React: typeof React }).React = React
// primitives.jsx's Modal (the apply confirm's shell) reads `Icons` for its
// close glyph at render time.
;(globalThis as unknown as { Icons: unknown }).Icons = new Proxy({}, { get: () => null })
// React's act() contract — without it every act() call warns that the
// environment is unconfigured.
;(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

// GTT feasibility probe — a real useState-backed mutation stand-in so calling
// `mutate()` from ModelPicker's onOpenChange handler genuinely re-renders the
// component, the same way react-query's own mutation state does (idiom lifted
// from model-drawer.test.tsx's feasibility mock).
const { feasibilityCalls, feasibilityHandler } = vi.hoisted(() => ({
  feasibilityCalls: [] as unknown[],
  feasibilityHandler: {
    current: undefined as
      | ((body: { models: { model_id: string; ctx?: number }[] }) => { results: unknown[] } | undefined)
      | undefined,
  },
}))

vi.mock('@/api/hooks/useModelsFeasibility', () => ({
  useModelsFeasibility: () => {
    const [data, setData] = React.useState<{ results: unknown[] } | undefined>(undefined)
    return {
      data,
      mutate: (body: { models: { model_id: string; ctx?: number }[] }) => {
        feasibilityCalls.push(body)
        const resp = feasibilityHandler.current?.(body)
        if (resp) setData(resp)
      },
    }
  },
}))

// Profile-picker seams (Task 2). `useProfiles`/`useSystemInfo` are read boxes;
// `useSlotEdit`/`useSlotRestart` are the TWO mutations the verified apply path
// fires, recorded so a test can assert that Cancel fires neither and Apply
// fires both, in order.
const { profilesBox, systemInfoBox, editCalls, restartCalls } = vi.hoisted(() => ({
  profilesBox: { current: undefined as unknown[] | undefined },
  systemInfoBox: { current: undefined as unknown },
  editCalls: [] as { name: string; body: Record<string, unknown> }[],
  restartCalls: [] as string[],
}))

vi.mock('@/api/hooks/useProfiles', () => ({
  useProfiles: () => ({ data: profilesBox.current }),
}))

// Only `useSystemInfo` is stubbed — `deviceBackend` stays the real one, since
// profileApplyPreview's lane lines are derived through it.
vi.mock('@/api/hooks/useRuntimes', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  useSystemInfo: () => ({ data: systemInfoBox.current }),
}))

vi.mock('@/api/hooks/useSlots', () => ({
  useSlots: () => ({ data: undefined, isLoading: false }),
  useSlotLoad: () => ({ mutate: () => {} }),
  useSlotUnload: () => ({ mutate: () => {} }),
  useSlotSwap: () => ({ mutate: () => {} }),
  useSlotRestart: () => ({
    mutate: (name: string) => {
      restartCalls.push(name)
    },
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
    mutateAsync: async (args: { name: string; body: Record<string, unknown> }) => {
      editCalls.push(args)
    },
  }),
}))

const { ModelPicker, SlotCards } = await import('../inference-pane.jsx')

function mount(el: React.ReactElement) {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const root = createRoot(host)
  act(() => {
    root.render(el)
  })
  return { host, root }
}

const q = (host: HTMLElement, testid: string) =>
  host.querySelector(`[data-testid="${testid}"]`) as HTMLElement | null

function trigger(host: HTMLElement, name: string) {
  return q(host, `infer-model-${name}`)!
}

function openMenu(host: HTMLElement, name: string) {
  act(() => {
    trigger(host, name).dispatchEvent(new MouseEvent('click', { bubbles: true }))
  })
}

function optionEls(host: HTMLElement) {
  return Array.from(host.querySelectorAll('.rsel-listbox [role="option"]')) as HTMLElement[]
}

function optionById(host: HTMLElement, id: string) {
  return host.querySelector(`[data-option-id="${id}"]`) as HTMLElement | null
}

// m1: current model of NO slot in the base fixture set (used-by = "other")
// so its used-by text reads as a plain count, matching the brief's literal
// "used by 1 slot" example rather than the "used by this slot" branch.
const M1 = {
  id: 'm1',
  longName: 'Qwen3.6-8B',
  quant: 'Q8_0',
  size_bytes: 2_791_728_742, // (2_791_728_742 / 1024**3).toFixed(1) === "2.6"
  type: 'llm',
  capabilities: ['chat'],
  installed: true,
  provider_effective: 'llama-server',
}
const M2 = {
  id: 'm2',
  longName: 'Ornith-Vision',
  quant: 'Q4_K',
  size_bytes: 9_223_372_036, // ~8.6 GB
  type: 'llm',
  capabilities: ['chat', 'vision'],
  installed: true,
  provider_effective: 'vllm',
}
// upstream-advertised, not locally installed — excluded from `opts` by the
// same rule slot-modals' compatibleModels() applies.
const M3_UPSTREAM = {
  id: 'm3',
  longName: 'Upstream-Only',
  type: 'llm',
  origin: 'upstream',
  installed: false,
}
const MODELS = [M1, M2, M3_UPSTREAM]

const SLOT = { name: 'chat', type: 'llm', model_id: '', ctx_max: 32768 }
// slotsUsingModel (model-usage.js) matches on `model`/`model_default`, not
// `model_id` — the slot roster shape it was written against.
const ALL_SLOTS = [
  { name: 'chat', model: '' },
  { name: 'other', model: 'm1' },
]

// ── profile-picker fixtures (Task 2) ────────────────────────────────────────
// One dual-lane default runtime and one single-lane ROCm runtime, so a pick
// can move the slot's lane (Vulkan → ROCm) and the preview has a real lane
// line to render.
const BACKENDS = {
  rocmfpx: {
    title: 'Standard',
    blurb: 'Runs everything.',
    is_default: true,
    supported_backends: ['rocm', 'vulkan'],
    device_class: 'gpu',
    runtime_family: 'llama-server',
    state: 'installed',
  },
  promptforge: {
    title: 'PromptForge',
    blurb: 'Accelerated PF.',
    is_default: false,
    supported_backends: ['rocm'],
    device_class: 'gpu',
    runtime_family: 'llama-server',
    state: 'installed',
  },
}
// P_CUR is the slot's persisted profile (no runner → AUTO chip); P_PF pins a
// single-lane ROCm runtime; P_TTS is type-fenced away from an llm slot.
const P_CUR = {
  name: 'rocm',
  flags: '-fa auto',
  intent: 'general chat tune',
  supported_slot_types: ['llm'],
}
const P_PF = {
  name: 'brainy',
  flags: '--temp 0.2 --top-p 0.9 --jinja',
  runner: 'promptforge',
  intent: 'low-temp reasoning tune',
  supported_slot_types: ['llm'],
}
const P_TTS = {
  name: 'voice',
  flags: '',
  intent: 'kokoro synth tune',
  supported_slot_types: ['tts'],
}
// A serving slot (container running + healthy → slotCtrlPhase "running") and
// a stopped one, so the confirm's lifecycle line has both branches to take.
const PROFILE_SLOT = {
  name: 'primary',
  type: 'llm',
  device: 'gpu-vulkan',
  profile: 'rocm',
  binary: '',
  container_status: 'running',
  container_health: true,
}
const OFF_SLOT = {
  name: 'spare',
  type: 'llm',
  device: 'gpu-vulkan',
  profile: 'rocm',
  binary: '',
  container_status: 'exited',
  container_health: false,
}

// The profile picker lives on the CARD but its apply confirm is owned by the
// card LIST (it is a fixed overlay and `.scard` both clips and, on hover,
// becomes its containing block), so every profile test drives the real
// SlotCards wiring rather than a card in isolation.
function mountCards(slots: Record<string, unknown>[]) {
  const handlers = {
    onSwap: () => {},
    onStart: () => {},
    onStop: () => {},
    onRestart: () => {},
    onLogs: () => {},
    onEdit: vi.fn(),
    onSetDefault: () => {},
  }
  const el = () => (
    <SlotCards
      rows={slots.map((s) => ({ s, ind: { cls: 'offline', tooltip: '' } }))}
      full={false}
      models={MODELS}
      allSlots={slots}
      busyName={null}
      handlers={handlers}
      loading={false}
      modelRows={() => ({ id: 'm1', longName: 'Qwen3.6-8B', defaults: { extra_args: '' } })}
    />
  )
  const mounted = mount(el())
  // Re-render — how a landing /api/profiles poll reaches an already-open
  // confirm. A FRESH element each time: React bails out of reconciling a
  // referentially identical one, which would silently no-op the re-render.
  const rerender = () =>
    act(() => {
      mounted.root.render(el())
    })
  return { ...mounted, handlers, rerender }
}

function profileTrigger(host: HTMLElement, name: string) {
  return q(host, `infer-profile-${name}`)!
}

function openProfileMenu(host: HTMLElement, name: string) {
  act(() => {
    profileTrigger(host, name).dispatchEvent(new MouseEvent('click', { bubbles: true }))
  })
}

// The open listbox is the one hanging off the trigger we just clicked — with
// more than one card mounted, `data-option-id` is not unique across the page.
function openListbox(host: HTMLElement, name: string) {
  const id = profileTrigger(host, name).getAttribute('aria-controls')!
  return host.querySelector(`[id="${id}"]`) as HTMLElement
}

function pickProfile(host: HTMLElement, name: string, id: string) {
  act(() => {
    openListbox(host, name)
      .querySelector(`[data-option-id="${id}"]`)!
      .dispatchEvent(new MouseEvent('click', { bubbles: true }))
  })
}

function footButton(label: string) {
  return Array.from(document.querySelectorAll('.modal-foot button')).find(
    (b) => (b.textContent || '').trim() === label,
  ) as HTMLElement | undefined
}

beforeEach(() => {
  feasibilityCalls.length = 0
  feasibilityHandler.current = undefined
  profilesBox.current = [P_CUR, P_PF, P_TTS]
  systemInfoBox.current = { backends: BACKENDS }
  editCalls.length = 0
  restartCalls.length = 0
})

afterEach(() => {
  document.body.innerHTML = ''
})

describe('ModelPicker (card-dropdowns Task 1)', () => {
  it('renders rich rows: quant tag, size desc, used-by', () => {
    const { host } = mount(
      <ModelPicker s={SLOT} models={MODELS} allSlots={ALL_SLOTS} disabled={false} onSwap={() => {}} />,
    )
    openMenu(host, 'chat')
    const row = optionById(host, 'm1')!
    expect(row.textContent).toContain('Q8_0')
    expect(row.querySelector('.rsel-option-desc')!.textContent).toContain('2.6 GB')
    expect(row.querySelector('.rsel-option-desc')!.textContent).toContain('used by 1 slot')
    expect(row.querySelector('.rsel-option-desc')!.textContent).toContain('other')
    // upstream-only row never appears at all.
    expect(optionById(host, 'm3')).toBeNull()
  })

  it('fires one lazy feasibility batch on first open with slot ctx, not on render', () => {
    const { host } = mount(
      <ModelPicker s={SLOT} models={MODELS} allSlots={ALL_SLOTS} disabled={false} onSwap={() => {}} />,
    )
    expect(feasibilityCalls.length).toBe(0)
    openMenu(host, 'chat')
    expect(feasibilityCalls.length).toBe(1)
    expect(feasibilityCalls[0]).toEqual({
      models: [
        { model_id: 'm1', ctx: 32768 },
        { model_id: 'm2', ctx: 32768 },
      ],
    })
    // Closing and reopening does not re-fire — first open only.
    act(() => {
      document.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))
    })
    openMenu(host, 'chat')
    expect(feasibilityCalls.length).toBe(1)
  })

  it('verdict chips render per feasibilityHint tone; unknown renders no chip', () => {
    feasibilityHandler.current = () => ({
      results: [
        { model_id: 'm1', verdict: 'fits', needed_mb: 2662, gtt_free_mb: 8192, gtt_total_mb: 16384 },
        // m2 has no result row at all → unknown → no chip.
      ],
    })
    const { host } = mount(
      <ModelPicker s={SLOT} models={MODELS} allSlots={ALL_SLOTS} disabled={false} onSwap={() => {}} />,
    )
    openMenu(host, 'chat')
    const row1 = optionById(host, 'm1')!
    expect(row1.querySelector('.rsel-option-right')!.textContent).toContain('fits')
    const row2 = optionById(host, 'm2')!
    expect(row2.querySelector('.rsel-option-right')).toBeNull()
  })

  it('pick calls onSwap(id); picking current is a no-op', () => {
    const onSwap = vi.fn()
    const cur = { ...SLOT, model_id: 'm1' }
    const { host } = mount(
      <ModelPicker s={cur} models={MODELS} allSlots={ALL_SLOTS} disabled={false} onSwap={onSwap} />,
    )
    openMenu(host, 'chat')
    act(() => {
      optionById(host, 'm1')!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })
    expect(onSwap).not.toHaveBeenCalled()
    openMenu(host, 'chat')
    act(() => {
      optionById(host, 'm2')!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })
    expect(onSwap).toHaveBeenCalledWith('m2')
  })

  it('current out-of-vocab model renders as its own row', () => {
    const cur = { ...SLOT, model_id: 'ghost-7b', model: 'ghost-7b' }
    const { host } = mount(
      <ModelPicker s={cur} models={MODELS} allSlots={ALL_SLOTS} disabled={false} onSwap={() => {}} />,
    )
    openMenu(host, 'chat')
    const row = optionById(host, 'ghost-7b')
    expect(row).not.toBeNull()
    expect(row!.textContent).toContain('ghost-7b')
    expect(optionEls(host).length).toBe(3) // ghost-7b + m1 + m2
  })

  it('non-llm slot keeps the static line', () => {
    const util = { name: 'embed', type: 'embedding', model: 'bge-m3' }
    const { host } = mount(
      <ModelPicker s={util} models={MODELS} allSlots={ALL_SLOTS} disabled={false} onSwap={() => {}} />,
    )
    expect(host.querySelector('.smodel')!.textContent).toBe('bge-m3')
    expect(q(host, 'infer-model-embed')).toBeNull()
    expect(feasibilityCalls.length).toBe(0)
  })
})

describe('card profile picker + apply confirm (card-dropdowns Task 2)', () => {
  it("profile pill opens rich rows filtered to the slot's type, current ticked", () => {
    const { host } = mountCards([PROFILE_SLOT])
    // the pill still surfaces the slot's profile name (the e2e claim)
    expect(profileTrigger(host, 'primary').textContent).toContain('rocm')
    openProfileMenu(host, 'primary')
    const listbox = openListbox(host, 'primary')
    // the tts-only profile is fenced out by supported_slot_types.
    expect(listbox.querySelector('[data-option-id="voice"]')).toBeNull()
    expect(listbox.querySelector('[data-option-id="rocm"]')).not.toBeNull()
    expect(listbox.querySelector('[data-option-id="brainy"]')).not.toBeNull()
    // current is the ticked row — RichSelect's own selected affordance.
    expect(
      listbox.querySelector('[data-option-id="rocm"]')!.getAttribute('aria-selected'),
    ).toBe('true')
    expect(
      listbox.querySelector('[data-option-id="brainy"]')!.getAttribute('aria-selected'),
    ).toBe('false')
  })

  it('rows show runtime chip + blurb; Edit slot row present and calls onProfile', () => {
    const { host, handlers } = mountCards([PROFILE_SLOT])
    openProfileMenu(host, 'primary')
    const listbox = openListbox(host, 'primary')
    const pf = listbox.querySelector('[data-option-id="brainy"]') as HTMLElement
    // one solid chip per lane (runtimeChips), and the profile's intent line.
    expect(Array.from(pf.querySelectorAll('.pf-be')).map((c) => c.textContent)).toEqual(['ROCm'])
    expect(pf.querySelector('.rsel-option-desc')!.textContent).toBe('low-temp reasoning tune')
    // an unpinned profile chips AUTO rather than claiming a lane.
    expect(
      Array.from(
        listbox.querySelector('[data-option-id="rocm"]')!.querySelectorAll('.pf-be'),
      ).map((c) => c.textContent),
    ).toEqual(['AUTO'])
    // the last row keeps today's gesture: the pill opened the slot editor.
    const edit = listbox.querySelector('[data-option-id="__edit_slot__"]') as HTMLElement
    expect(edit.textContent).toContain('Edit slot')
    pickProfile(host, 'primary', '__edit_slot__')
    expect(handlers.onEdit).toHaveBeenCalledTimes(1)
    // …and it is NOT a profile pick: no confirm, no write.
    expect(q(host, 'infer-profile-preview')).toBeNull()
    expect(editCalls).toEqual([])
  })

  it('pick opens the consequence confirm with flag/runtime/restart lines', () => {
    const { host } = mountCards([PROFILE_SLOT])
    openProfileMenu(host, 'primary')
    pickProfile(host, 'primary', 'brainy')
    const box = q(host, 'infer-profile-preview')!
    expect(box).not.toBeNull()
    const text = box.textContent || ''
    // runtime → the runner the profile pins, named by the system-info catalog.
    expect(text).toContain('PromptForge')
    // lane → a single-lane ROCm runtime moves this Vulkan slot's lane.
    expect(text).toContain('Vulkan')
    expect(text).toContain('ROCm')
    // flags → the 3 pairs in brainy's tune the slot does not already launch
    // with (its current tune is rocm's `-fa auto`).
    expect(text).toContain('replaces 3 flags')
    // …and the lifecycle line for a RUNNING slot.
    expect(text).toContain('restarts')
    // nothing has been written yet.
    expect(editCalls).toEqual([])
    expect(restartCalls).toEqual([])
  })

  it('the confirm is mounted by the card LIST — one dialog for N cards', () => {
    const { host } = mountCards([PROFILE_SLOT, OFF_SLOT])
    expect(host.querySelectorAll('.scard').length).toBe(2)
    expect(host.querySelectorAll('.rsel-trigger.profile-pill').length).toBe(2)
    openProfileMenu(host, 'primary')
    pickProfile(host, 'primary', 'brainy')
    // exactly ONE confirm in the tree…
    expect(host.querySelectorAll('[data-testid="infer-profile-preview"]').length).toBe(1)
    // …and it is a sibling of the card grid, NOT inside a `.scard` (which
    // clips with overflow:hidden and becomes the containing block for fixed
    // descendants on hover).
    const box = q(host, 'infer-profile-preview')!
    expect(box.closest('.scard')).toBeNull()
    expect(box.closest('.scards')).toBeNull()
    // it names the slot the pick came from.
    expect(box.textContent).toContain('primary')
  })

  it('a stopped slot is told it will START and load the model, not restart', () => {
    const { host } = mountCards([OFF_SLOT])
    openProfileMenu(host, 'spare')
    pickProfile(host, 'spare', 'brainy')
    const text = q(host, 'infer-profile-preview')!.textContent || ''
    // SlotManager.restart is unload+load, so an off slot STARTS.
    expect(text).toContain('starts')
    expect(text).toContain('loads the model')
    expect(text).not.toContain('restarts')
  })

  it('the lifecycle line renders even when the picked profile no longer resolves', () => {
    const { host, rerender } = mountCards([PROFILE_SLOT])
    openProfileMenu(host, 'primary')
    pickProfile(host, 'primary', 'brainy')
    // the catalogue drops the profile out from under the OPEN confirm (a 5s
    // /api/profiles poll landing mid-decision).
    profilesBox.current = [P_CUR, P_TTS]
    rerender()
    const box = q(host, 'infer-profile-preview')!
    expect(box).not.toBeNull()
    // no derived lines (nothing true left to say)…
    expect(box.textContent).not.toContain('runtime →')
    // …but the consequence of pressing Apply is still stated.
    expect(box.textContent).toContain('restarts')
  })

  it('cancel writes nothing', () => {
    const { host } = mountCards([PROFILE_SLOT])
    openProfileMenu(host, 'primary')
    pickProfile(host, 'primary', 'brainy')
    act(() => {
      footButton('Cancel')!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })
    expect(q(host, 'infer-profile-preview')).toBeNull()
    expect(editCalls).toEqual([])
    expect(restartCalls).toEqual([])
    // the pill still reads the PERSISTED profile — a cancelled pick leaves no
    // staged state behind either.
    expect(profileTrigger(host, 'primary').textContent).toContain('rocm')
  })

  it('apply follows the verified path: PUT /config {profile} then a background restart', () => {
    const { host } = mountCards([PROFILE_SLOT])
    openProfileMenu(host, 'primary')
    pickProfile(host, 'primary', 'brainy')
    act(() => {
      footButton('Apply profile')!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })
    // Branch (a) of the plan's riddle, verified from slot-modals.jsx:1013/1023
    // + :1036 — the drawer's own two-call save, reachable as one gesture.
    expect(editCalls).toEqual([{ name: 'primary', body: { profile: 'brainy' } }])
    expect(restartCalls).toEqual(['primary'])
    // the confirm closes on apply; the slots poll re-renders the pill.
    expect(q(host, 'infer-profile-preview')).toBeNull()
  })

  it('a slot with no profile keeps the "default" word as a listed row', () => {
    const bare = { ...PROFILE_SLOT, profile: '' }
    const { host } = mountCards([bare])
    expect(profileTrigger(host, 'primary').textContent).toContain('default')
    openProfileMenu(host, 'primary')
    expect(
      openListbox(host, 'primary').querySelector('[data-option-id=""]')!.getAttribute('aria-selected'),
    ).toBe('true')
  })
})
