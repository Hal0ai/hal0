// @vitest-environment happy-dom
//
// Model drawer (model-drawer-2):
//   · Task 3 — inline title editor, the single default-toggle chip, and the
//     read-only facts band.
//   · Task 4 — the structured tune editor: grouped pills over the flags
//     string, per-pill revert/remove/restore, the value popover, the add-flag
//     denylist gate, and the raw⇄pills toggle.
//
// model-drawer.jsx is a window-globals dash module (bare `Drawer`/
// `FieldInfoIcon`/`Icons`/... identifiers resolve off `window`), so the
// globals are installed before the dynamic import — same pattern as
// profiles.test.tsx / runner-images-view.test.tsx.
import React from 'react'
import * as ReactDOMClient from 'react-dom/client'
import { createPortal } from 'react-dom'
import { createRoot } from 'react-dom/client'
import { act } from 'react-dom/test-utils'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { feasibilityHint } from '../feasibility-copy'

;(globalThis as unknown as { window: typeof globalThis }).window = globalThis
;(globalThis as unknown as { React: typeof React }).React = React
// FieldInfoIcon (primitives.jsx) escapes overflow via ReactDOM.createPortal —
// mirrors src/globals-install.ts's merge of react-dom/client + createPortal
// onto one global.
;(globalThis as unknown as { ReactDOM: unknown }).ReactDOM = { ...ReactDOMClient, createPortal }
;(globalThis as unknown as { Icons: unknown }).Icons = new Proxy({}, { get: () => null })

const { updateCalls, setDefaultCalls, setDefaultResult, slotsBox, profilesBox, templatesBox, enumsBox } = vi.hoisted(() => ({
  updateCalls: [] as unknown[],
  setDefaultCalls: [] as unknown[],
  setDefaultResult: { current: { default: true, type: 'llm' } as Record<string, unknown> },
  slotsBox: { current: [] as Record<string, unknown>[] },
  profilesBox: { current: [] as Record<string, unknown>[] },
  templatesBox: { current: [] as Record<string, unknown>[] },
  // Task 6: override hook for the one engine test that needs a
  // runtime_families entry ENGINE_BLURBS doesn't know about — every other
  // test leaves this undefined so useMetaEnums keeps resolving the same
  // fallback table it always has.
  enumsBox: { current: undefined as Record<string, unknown> | undefined },
}))

// Task 9: the Source disclosure's lazy repo probe. `useModelInspect` is a
// react-query MUTATION the drawer fires from an effect on the disclosure's
// first open, so the stand-in is useState-backed (same reasoning as the
// feasibility mock below) — `mutate()` must genuinely re-render the component
// for the picker to appear. `inspectHandler.current` decides what a probe
// resolves to; returning an Error models the route failing (→ the text-input
// fallback), leaving it unset models a probe still in flight.
const { inspectCalls, inspectHandler } = vi.hoisted(() => ({
  inspectCalls: [] as unknown[],
  inspectHandler: {
    current: undefined as
      | ((body: { hf_repo?: string }) => Record<string, unknown> | Error | undefined)
      | undefined,
  },
}))

vi.mock('@/api/hooks/useModels', () => ({
  useModelUpdate: () => ({
    mutateAsync: async (b: unknown) => {
      updateCalls.push(b)
      return b
    },
    isPending: false,
  }),
  useModelSetDefault: () => ({
    mutateAsync: async (b: unknown) => {
      setDefaultCalls.push(b)
      return setDefaultResult.current
    },
    isPending: false,
  }),
  useModelInspect: () => {
    const [state, setState] = React.useState<{
      data?: Record<string, unknown>
      isError: boolean
    }>({ isError: false })
    return {
      data: state.data,
      isError: state.isError,
      isSuccess: !!state.data,
      isPending: false,
      mutate: (body: { hf_repo?: string }) => {
        inspectCalls.push(body)
        const out = inspectHandler.current?.(body)
        if (out instanceof Error) setState({ isError: true })
        else if (out) setState({ data: out, isError: false })
      },
      reset: () => setState({ isError: false }),
    }
  },
}))

// Task 8: seedCalls records every seedProfile.mutateAsync invocation so a
// test can assert the exact {id, profile} the confirm's onConfirm sent.
// seedHandler.current lets one test simulate a rejection (the "failed seed
// keeps the dialog open for retry" path) without touching every other test's
// happy-path default (a response shaped like the real POST — echoes the
// requested profile name as the new provenance/extra_args).
const { seedCalls, seedHandler } = vi.hoisted(() => ({
  seedCalls: [] as unknown[],
  seedHandler: {
    current: undefined as
      | ((body: { id: string; profile: string }) => unknown)
      | undefined,
  },
}))

vi.mock('@/api/hooks/useModelSeedProfile', () => ({
  useModelSeedProfile: () => ({
    mutateAsync: async (body: { id: string; profile: string }) => {
      seedCalls.push(body)
      if (seedHandler.current) return seedHandler.current(body)
      return { defaults: { profile: body.profile, extra_args: '' } }
    },
    isPending: false,
  }),
}))

vi.mock('@/api/hooks/useChatTemplates', () => ({
  useChatTemplates: () => ({ data: templatesBox.current, isLoading: false }),
}))

vi.mock('@/api/hooks/useProfiles', () => ({
  useProfiles: () => ({ data: profilesBox.current, isLoading: false }),
}))

// useMetaEnums resolves the static device taxonomy over the live payload; the
// fallback is all this suite needs, and mocking it keeps the run off the
// network (the real hook fires a fetch at localhost) — same idiom as
// profiles.test.tsx.
vi.mock('@/api/hooks/useMeta', async () => {
  const actual = await vi.importActual<typeof import('@/api/hooks/useMeta')>('@/api/hooks/useMeta')
  return {
    ...actual,
    useMeta: () => ({ data: undefined }),
    useMetaEnums: () => actual.resolveMetaEnums(enumsBox.current),
  }
})

vi.mock('@/api/hooks/useSlots', () => ({
  useSlots: () => ({ data: slotsBox.current, isLoading: false }),
}))

// GTT feasibility probe (Task 7) — a real useState-backed mutation stand-in
// so calling `mutate()` from inside model-drawer.jsx's debounced effect
// genuinely re-renders the component under test, the same way react-query's
// own mutation state does. `feasibilityHandler.current` lets each test decide
// what (if anything) a probe resolves to; leaving it unset means `mutate()`
// records the call but never produces a hint, matching an in-flight/never-
// responded probe.
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

await import('../primitives.jsx')
const { ModelDrawer } = await import('../model-drawer.jsx')

const MODEL = {
  id: 'm1',
  name: 'qwen',
  type: 'llm',
  quant: 'Q8_0',
  size_bytes: 9771050700,
  architecture: 'qwen3vl',
  metadata: {
    context_length: 131072,
    sha256: 'a3f19c2e77bb4a8e9e6d5f4c3b2a19080706050403020100090807060504030',
  },
  capabilities: ['chat'],
  defaults: {},
}

function mount(el: React.ReactElement) {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const root = createRoot(host)
  act(() => {
    root.render(React.createElement(QueryClientProvider, { client: qc }, el))
  })
  return { host, root }
}

// React installs a value tracker on a controlled input's `value` property, so
// a plain `input.value = x` assignment is invisible to onChange (the tracker
// sees no drift between "last known" and "current"). Going through the
// prototype's native setter first — the standard RTL/React testing
// workaround — is what actually exercises onChange here.
const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
  window.HTMLInputElement.prototype,
  'value',
)!.set!
function typeInto(input: HTMLInputElement, value: string) {
  nativeInputValueSetter.call(input, value)
  input.dispatchEvent(new Event('input', { bubbles: true }))
}
const nativeAreaValueSetter = Object.getOwnPropertyDescriptor(
  window.HTMLTextAreaElement.prototype,
  'value',
)!.set!
function typeIntoArea(area: HTMLTextAreaElement, value: string) {
  nativeAreaValueSetter.call(area, value)
  area.dispatchEvent(new Event('input', { bubbles: true }))
}

beforeEach(() => {
  updateCalls.length = 0
  setDefaultCalls.length = 0
  setDefaultResult.current = { default: true, type: 'llm' }
  slotsBox.current = []
  profilesBox.current = []
  templatesBox.current = []
  enumsBox.current = undefined
  feasibilityCalls.length = 0
  feasibilityHandler.current = undefined
  seedCalls.length = 0
  seedHandler.current = undefined
  inspectCalls.length = 0
  inspectHandler.current = undefined
})

afterEach(() => {
  document.body.innerHTML = ''
})

describe('ModelDrawer header (Task 3)', () => {
  it('renders facts band cells and hides absent facts', () => {
    const { host, root } = mount(
      React.createElement(ModelDrawer, { open: true, onClose: () => {}, model: MODEL }),
    )
    const facts = host.querySelector('[data-testid="model-facts"]') as HTMLElement
    expect(facts).toBeTruthy()
    expect(facts.textContent).toContain('Q8_0')
    expect(facts.textContent).toContain('9.1 GB')
    expect(facts.textContent).toContain('qwen3vl')
    expect(facts.textContent).toContain('128K')
    expect(facts.textContent).toContain('a3f19c2e')

    act(() => {
      root.render(
        React.createElement(
          QueryClientProvider,
          { client: new QueryClient({ defaultOptions: { queries: { retry: false } } }) },
          React.createElement(ModelDrawer, {
            open: true,
            onClose: () => {},
            model: { ...MODEL, metadata: {} },
          }),
        ),
      )
    })
    const factsAfter = host.querySelector('[data-testid="model-facts"]') as HTMLElement
    expect(factsAfter.textContent).not.toContain('128K')
    expect(factsAfter.textContent).not.toContain('a3f19c2e')
    act(() => root.unmount())
  })

  it('title edits inline; Escape reverts without committing', () => {
    const { host, root } = mount(
      React.createElement(ModelDrawer, { open: true, onClose: () => {}, model: MODEL }),
    )
    const editBtn = host.querySelector('[data-testid="model-title-edit"]') as HTMLButtonElement
    expect(editBtn).toBeTruthy()
    act(() => editBtn.click())

    const input = host.querySelector('[data-testid="model-title-input"]') as HTMLInputElement
    expect(input).toBeTruthy()
    expect(input.value).toBe('qwen')

    act(() => {
      typeInto(input, 'x')
    })
    act(() => {
      input.dispatchEvent(
        new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }),
      )
    })

    expect(host.querySelector('[data-testid="model-title-input"]')).toBeNull()
    expect(host.textContent).toContain('qwen')
    expect(host.textContent).not.toContain('qwenx')

    const saveBtn = host.querySelector('[data-testid="model-save"]') as HTMLButtonElement
    expect(saveBtn.disabled).toBe(true)
    act(() => root.unmount())
  })

  it('Enter commits the new name into form state', () => {
    const { host, root } = mount(
      React.createElement(ModelDrawer, { open: true, onClose: () => {}, model: MODEL }),
    )
    const editBtn = host.querySelector('[data-testid="model-title-edit"]') as HTMLButtonElement
    act(() => editBtn.click())

    const input = host.querySelector('[data-testid="model-title-input"]') as HTMLInputElement
    act(() => {
      typeInto(input, 'qwen2')
    })
    act(() => {
      input.dispatchEvent(
        new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }),
      )
    })

    expect(host.querySelector('[data-testid="model-title-input"]')).toBeNull()
    expect(host.textContent).toContain('qwen2')

    const saveBtn = host.querySelector('[data-testid="model-save"]') as HTMLButtonElement
    expect(saveBtn.disabled).toBe(false)
    act(() => root.unmount())
  })

  it('default chip toggles via useModelSetDefault', async () => {
    const { host, root } = mount(
      React.createElement(ModelDrawer, { open: true, onClose: () => {}, model: MODEL }),
    )
    const toggle = host.querySelector('[data-testid="model-default-toggle"]') as HTMLButtonElement
    expect(toggle).toBeTruthy()
    expect(toggle.textContent).toBe('llm default')

    setDefaultResult.current = { default: true, type: 'llm' }
    await act(async () => {
      toggle.click()
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(setDefaultCalls).toEqual([{ id: 'm1', default: true }])
    const toggleAfter = host.querySelector('[data-testid="model-default-toggle"]') as HTMLButtonElement
    expect(toggleAfter.textContent).toBe('✓ llm default')
    act(() => root.unmount())
  })

  it('used-by cell lists slot names from useSlots', () => {
    slotsBox.current = [{ name: 'agent', model_default: 'm1' }]
    const { host, root } = mount(
      React.createElement(ModelDrawer, { open: true, onClose: () => {}, model: MODEL }),
    )
    const usedBy = host.querySelector('[data-testid="model-facts-usedby"]') as HTMLElement
    expect(usedBy).toBeTruthy()
    expect(usedBy.textContent).toContain('1 slot')
    expect(usedBy.textContent).toContain('agent')
    act(() => root.unmount())
  })
})

// ─── Task 4 — structured tune editor ────────────────────────────────────────
// The flags STRING is the single source of truth; the pills are a pure render
// of it and every interaction splices that string. So each assertion below
// reads the string back through the raw textarea rather than trusting the
// pills' own labels — a pill that lies about the string it produced fails.

const TUNE_MODEL = {
  ...MODEL,
  defaults: { extra_args: '-fa auto --temp 0.4', profile: 'chat' },
}
const CHAT_PROFILE = { name: 'chat', flags: '-fa auto --temp 0.7 --repeat-penalty 1.1' }

const q = <T extends HTMLElement>(host: HTMLElement, testid: string) =>
  host.querySelector(`[data-testid="${testid}"]`) as T
const press = (el: HTMLElement, key: string) =>
  act(() => {
    el.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }))
  })

// Read the flags string as the drawer actually holds it: flip to raw, read the
// textarea, flip back. Round-tripping through the toggle on every read also
// keeps the toggle itself under test in every case.
function rawFlags(host: HTMLElement) {
  act(() => q<HTMLButtonElement>(host, 'model-tune-raw-toggle').click())
  const value = q<HTMLTextAreaElement>(host, 'model-flags-input').value
  act(() => q<HTMLButtonElement>(host, 'model-tune-raw-toggle').click())
  return value
}

describe('ModelDrawer tune pills (Task 4)', () => {
  it('renders pills grouped, with divergence classes', () => {
    profilesBox.current = [CHAT_PROFILE]
    const { host, root } = mount(
      React.createElement(ModelDrawer, { open: true, onClose: () => {}, model: TUNE_MODEL }),
    )

    const temp = q(host, 'model-tune-pill---temp')
    expect(temp).toBeTruthy()
    expect(temp.getAttribute('data-divergence')).toBe('changed')
    expect(temp.className).toContain('fpill-chg')

    // -fa canonicalises onto --flash-attn, and matches the profile verbatim.
    const fa = q(host, 'model-tune-pill---flash-attn')
    expect(fa.getAttribute('data-divergence')).toBe('unchanged')

    // Grouped by category, not one flat row.
    expect(q(host, 'model-tune-group-sampling').contains(temp)).toBe(true)
    expect(q(host, 'model-tune-group-cache-kv').contains(fa)).toBe(true)

    // In the profile, missing from the text → a ghost pill offering restore.
    expect(q(host, 'model-tune-pill-restore---repeat-penalty')).toBeTruthy()

    // 0 added + 1 changed + 1 removed.
    expect(q(host, 'model-diverged-chip').textContent).toBe('◆ 2 diverged')
    expect(q(host, 'model-provenance-chip').textContent).toContain('chat')
    act(() => root.unmount())
  })

  it('per-pill revert restores the profile value', () => {
    profilesBox.current = [CHAT_PROFILE]
    const { host, root } = mount(
      React.createElement(ModelDrawer, { open: true, onClose: () => {}, model: TUNE_MODEL }),
    )
    act(() => q<HTMLButtonElement>(host, 'model-tune-pill-revert---temp').click())
    expect(rawFlags(host)).toBe('-fa auto --temp 0.7')
    expect(q(host, 'model-tune-pill---temp').getAttribute('data-divergence')).toBe('unchanged')
    act(() => root.unmount())
  })

  it('revert falls back to remove+add for a boolean⇄valued flag', () => {
    // The model dropped -fa's value, so there is no value token to splice onto
    // — spliceFlagValue is a documented no-op there. Revert has to rebuild the
    // pair, and the profile's value must come back regardless of position.
    profilesBox.current = [CHAT_PROFILE]
    const { host, root } = mount(
      React.createElement(ModelDrawer, {
        open: true,
        onClose: () => {},
        model: { ...TUNE_MODEL, defaults: { extra_args: '-fa --temp 0.7', profile: 'chat' } },
      }),
    )
    expect(q(host, 'model-tune-pill---flash-attn').getAttribute('data-divergence')).toBe('changed')
    act(() => q<HTMLButtonElement>(host, 'model-tune-pill-revert---flash-attn').click())
    expect(rawFlags(host)).toBe('--temp 0.7 -fa auto')
    expect(q(host, 'model-tune-pill---flash-attn').getAttribute('data-divergence')).toBe(
      'unchanged',
    )
    act(() => root.unmount())
  })

  it('remove and restore splice the string', () => {
    profilesBox.current = [CHAT_PROFILE]
    const { host, root } = mount(
      React.createElement(ModelDrawer, { open: true, onClose: () => {}, model: TUNE_MODEL }),
    )
    act(() => q<HTMLButtonElement>(host, 'model-tune-pill-remove---temp').click())
    expect(rawFlags(host)).toBe('-fa auto')
    expect(q(host, 'model-tune-pill---temp')).toBeNull()

    act(() => q<HTMLButtonElement>(host, 'model-tune-pill-restore---repeat-penalty').click())
    expect(rawFlags(host)).toBe('-fa auto --repeat-penalty 1.1')
    act(() => root.unmount())
  })

  it('value popover edits one flag; Esc reverts', () => {
    profilesBox.current = [CHAT_PROFILE]
    const { host, root } = mount(
      React.createElement(ModelDrawer, { open: true, onClose: () => {}, model: TUNE_MODEL }),
    )
    act(() => q<HTMLButtonElement>(host, 'model-tune-pill-value---temp').click())
    let input = q<HTMLInputElement>(host, 'model-tune-value-input')
    expect(input).toBeTruthy()
    expect(input.value).toBe('0.4')
    act(() => typeInto(input, '0.2'))
    press(input, 'Enter')
    expect(q(host, 'model-tune-value-input')).toBeNull()
    expect(rawFlags(host)).toBe('-fa auto --temp 0.2')

    act(() => q<HTMLButtonElement>(host, 'model-tune-pill-value---temp').click())
    input = q<HTMLInputElement>(host, 'model-tune-value-input')
    act(() => typeInto(input, '9'))
    press(input, 'Escape')
    expect(q(host, 'model-tune-value-input')).toBeNull()
    expect(rawFlags(host)).toBe('-fa auto --temp 0.2')
    act(() => root.unmount())
  })

  it('add flag guards the managed denylist', () => {
    profilesBox.current = [CHAT_PROFILE]
    const { host, root } = mount(
      React.createElement(ModelDrawer, { open: true, onClose: () => {}, model: TUNE_MODEL }),
    )
    act(() => q<HTMLButtonElement>(host, 'model-tune-add-flag').click())
    const input = q<HTMLInputElement>(host, 'model-tune-add-input')
    expect(input).toBeTruthy()
    act(() => typeInto(input, '--port 9'))
    press(input, 'Enter')

    expect(rawFlags(host)).toBe('-fa auto --temp 0.4 --port 9')
    const port = q(host, 'model-tune-pill---port')
    expect(port).toBeTruthy()
    expect(port.getAttribute('data-divergence')).toBe('denied')
    expect(port.className).toContain('fpill-deny')
    const err = q(host, 'model-flags-error')
    expect(err).toBeTruthy()
    expect(err.textContent).toContain('--port')
    expect(q<HTMLButtonElement>(host, 'model-save').disabled).toBe(true)
    act(() => root.unmount())
  })

  // Repeating a flag is legitimate llama-server usage (-ot / --override-kv /
  // --lora). A canon alone does not identify a pill, so every affordance
  // carries the pill's occurrence index — without it, ✕ on the second pill
  // removed the first and the popover edited the wrong pair.
  describe('repeated flags address their own pill', () => {
    const REPEATED = {
      ...MODEL,
      defaults: { extra_args: '-ot ffn=CPU -ot attn=GPU --temp 0.4' },
    }

    it('removing the second occurrence leaves the first intact', () => {
      const { host, root } = mount(
        React.createElement(ModelDrawer, { open: true, onClose: () => {}, model: REPEATED }),
      )
      // Occurrence 0 keeps the plain testid; the repeat earns a 1-based suffix.
      expect(q(host, 'model-tune-pill--ot')).toBeTruthy()
      expect(q(host, 'model-tune-pill--ot-2')).toBeTruthy()

      act(() => q<HTMLButtonElement>(host, 'model-tune-pill-remove--ot-2').click())
      expect(rawFlags(host)).toBe('-ot ffn=CPU --temp 0.4')
      expect(q(host, 'model-tune-pill--ot-2')).toBeNull()
      act(() => root.unmount())
    })

    it('editing the second occurrence edits the second', () => {
      const { host, root } = mount(
        React.createElement(ModelDrawer, { open: true, onClose: () => {}, model: REPEATED }),
      )
      act(() => q<HTMLButtonElement>(host, 'model-tune-pill-value--ot-2').click())
      const input = q<HTMLInputElement>(host, 'model-tune-value-input')
      expect(input.value).toBe('attn=GPU')
      act(() => typeInto(input, 'attn=CPU'))
      press(input, 'Enter')
      expect(rawFlags(host)).toBe('-ot ffn=CPU -ot attn=CPU --temp 0.4')
      act(() => root.unmount())
    })

    it('only one value input is open at a time', () => {
      const { host, root } = mount(
        React.createElement(ModelDrawer, { open: true, onClose: () => {}, model: REPEATED }),
      )
      act(() => q<HTMLButtonElement>(host, 'model-tune-pill-value--ot').click())
      expect(host.querySelectorAll('[data-testid="model-tune-value-input"]').length).toBe(1)

      // Opening the sibling pill's popover moves it, never duplicates it.
      act(() => q<HTMLButtonElement>(host, 'model-tune-pill-value--ot-2').click())
      const open = host.querySelectorAll(
        '[data-testid="model-tune-value-input"]',
      ) as NodeListOf<HTMLInputElement>
      expect(open.length).toBe(1)
      expect(open[0].value).toBe('attn=GPU')
      act(() => root.unmount())
    })
  })

  it('unparseable text forces raw mode', () => {
    profilesBox.current = [CHAT_PROFILE]
    const { host, root } = mount(
      React.createElement(ModelDrawer, {
        open: true,
        onClose: () => {},
        model: { ...TUNE_MODEL, defaults: { extra_args: '--chat-template "broken', profile: 'chat' } },
      }),
    )
    expect(q(host, 'model-flags-input')).toBeTruthy()
    expect(q(host, 'model-tune-pills')).toBeNull()
    expect(host.querySelector('[data-testid^="model-tune-pill-"]')).toBeNull()
    expect(q(host, 'model-flags-error').textContent).toContain('unbalanced quote')
    act(() => root.unmount())
  })

  it('no seeding profile → no divergence markers, no ghost pills', () => {
    profilesBox.current = [CHAT_PROFILE]
    const { host, root } = mount(
      React.createElement(ModelDrawer, {
        open: true,
        onClose: () => {},
        model: { ...TUNE_MODEL, defaults: { extra_args: '-fa auto --temp 0.4' } },
      }),
    )
    // Only the pills themselves carry data-divergence (the affordances inside
    // them share the testid prefix but not the attribute).
    const pills = Array.from(host.querySelectorAll('[data-divergence]')) as HTMLElement[]
    expect(pills.length).toBe(2)
    for (const p of pills) expect(p.getAttribute('data-divergence')).toBe('none')
    expect(host.querySelector('[data-testid^="model-tune-pill-restore-"]')).toBeNull()
    expect(q(host, 'model-diverged-chip')).toBeNull()
    act(() => root.unmount())
  })

  it('raw toggle round-trips', () => {
    profilesBox.current = [CHAT_PROFILE]
    const { host, root } = mount(
      React.createElement(ModelDrawer, { open: true, onClose: () => {}, model: TUNE_MODEL }),
    )
    act(() => q<HTMLButtonElement>(host, 'model-tune-raw-toggle').click())
    const area = q<HTMLTextAreaElement>(host, 'model-flags-input')
    expect(area.value).toBe('-fa auto --temp 0.4')
    // The diff panel is raw-mode-only; the pills carry the same facts inline.
    expect(q(host, 'model-divergence-diff')).toBeTruthy()

    act(() => typeIntoArea(area, '-fa auto --temp 0.4 -ctk q8_0'))
    act(() => q<HTMLButtonElement>(host, 'model-tune-raw-toggle').click())

    expect(q(host, 'model-flags-input')).toBeNull()
    expect(q(host, 'model-divergence-diff')).toBeNull()
    const ctk = q(host, 'model-tune-pill---cache-type-k')
    expect(ctk).toBeTruthy()
    expect(ctk.getAttribute('data-divergence')).toBe('added')
    expect(q(host, 'model-diverged-chip').textContent).toBe('◆ 3 diverged')
    act(() => root.unmount())
  })
})

// ─── Task 5 — chat template RichSelect ──────────────────────────────────────
// useChatTemplates rows are {id, label, valid, error} (chat_templates.py's
// _entry); a `valid: false` row carries the render-lint error string. The
// native <select> became a RichSelect (rich-select.jsx) so a broken template
// can chip its warning inline instead of hiding it behind a plain <option>
// label.
describe('ModelDrawer chat template (Task 5)', () => {
  const TEMPLATES = [
    { id: 'auto', label: 'Auto (GGUF embedded)', valid: true, error: null },
    { id: 'broken', label: 'broken', valid: false, error: 'TemplateSyntaxError: x' },
  ]

  it('renders rich rows with lint chips', () => {
    templatesBox.current = TEMPLATES
    const { host, root } = mount(
      React.createElement(ModelDrawer, { open: true, onClose: () => {}, model: MODEL }),
    )
    act(() => q<HTMLButtonElement>(host, 'model-chat-template').click())

    const autoRow = host.querySelector('[data-option-id="auto"]') as HTMLElement
    expect(autoRow.textContent).toContain('GGUF embedded')
    expect(autoRow.textContent).toContain('use the template the model file ships')

    const brokenRow = host.querySelector('[data-option-id="broken"]') as HTMLElement
    expect(brokenRow.textContent).toContain('⚠ broken')
    expect(brokenRow.textContent).toContain('TemplateSyntaxError: x')
    act(() => root.unmount())
  })

  it('picking a template updates form state', async () => {
    templatesBox.current = TEMPLATES
    const { host, root } = mount(
      React.createElement(ModelDrawer, { open: true, onClose: () => {}, model: MODEL }),
    )
    act(() => q<HTMLButtonElement>(host, 'model-chat-template').click())
    act(() => (host.querySelector('[data-option-id="broken"]') as HTMLElement).click())

    const saveBtn = q<HTMLButtonElement>(host, 'model-save')
    expect(saveBtn.disabled).toBe(false)

    await act(async () => {
      saveBtn.click()
      await Promise.resolve()
      await Promise.resolve()
    })

    const call = updateCalls[0] as { id: string; body: { defaults: Record<string, unknown> } }
    expect(call.body.defaults.chat_template).toBe('broken')
    act(() => root.unmount())
  })
})

// ─── Task 6 — engine RichSelect ──────────────────────────────────────────────
// The Engine select (Runner compatibility) was a native <select> keyed off
// enums.runtime_families; families still come ONLY from that enum (never
// hand-listed), but the native <select> becomes a RichSelect so the Auto row
// can preview the server-derived engine (model.provider_effective) instead of
// a bare "derive from tags" label, and each family row can carry a blurb.
describe('ModelDrawer engine (Task 6)', () => {
  it('engine renders rich rows; Auto previews the derived engine', () => {
    const { host, root } = mount(
      React.createElement(ModelDrawer, {
        open: true,
        onClose: () => {},
        model: { ...MODEL, provider: '', provider_effective: 'llama-server' },
      }),
    )
    const trigger = q<HTMLButtonElement>(host, 'model-provider-select')
    // Closed control: "Auto → llama-server".
    expect(trigger.textContent).toContain('Auto')
    expect(trigger.textContent).toContain('llama-server')

    act(() => trigger.click())

    const autoRow = host.querySelector('[data-option-id=""]') as HTMLElement
    expect(autoRow).toBeTruthy()
    expect(autoRow.textContent).toContain('derived')

    // Every family from enums.runtime_families renders, each with its blurb.
    const blurbs: Record<string, string> = {
      'llama-server': 'the default engine',
      flm: 'FastLLM',
      kokoro: 'TTS voices',
      qwen3tts: 'Qwen',
      moonshine: 'streaming ASR',
      comfyui: 'ComfyUI catalog',
    }
    for (const [rf, snippet] of Object.entries(blurbs)) {
      const row = host.querySelector(`[data-option-id="${rf}"]`) as HTMLElement
      expect(row, `missing row for ${rf}`).toBeTruthy()
      expect(row.textContent).toContain(snippet)
    }
    act(() => root.unmount())
  })

  it('a runtime family absent from ENGINE_BLURBS still renders, with no desc', () => {
    enumsBox.current = { runtime_families: ['llama-server', 'newfamily'] }
    const { host, root } = mount(
      React.createElement(ModelDrawer, {
        open: true,
        onClose: () => {},
        model: { ...MODEL, provider: '', provider_effective: 'llama-server' },
      }),
    )
    act(() => q<HTMLButtonElement>(host, 'model-provider-select').click())
    const row = host.querySelector('[data-option-id="newfamily"]') as HTMLElement
    expect(row).toBeTruthy()
    expect(row.querySelector('.rsel-option-desc')).toBeNull()
    act(() => root.unmount())
  })

  it('falls back to llama-server when a mock fixture lacks provider_effective', () => {
    const { host, root } = mount(
      React.createElement(ModelDrawer, { open: true, onClose: () => {}, model: MODEL }),
    )
    const trigger = q<HTMLButtonElement>(host, 'model-provider-select')
    expect(trigger.textContent).toContain('llama-server')
    act(() => root.unmount())
  })

  it('picking an engine ships provider in the save body', async () => {
    const { host, root } = mount(
      React.createElement(ModelDrawer, {
        open: true,
        onClose: () => {},
        model: { ...MODEL, provider: '', provider_effective: 'llama-server' },
      }),
    )
    act(() => q<HTMLButtonElement>(host, 'model-provider-select').click())
    act(() => (host.querySelector('[data-option-id="flm"]') as HTMLElement).click())

    const saveBtn = q<HTMLButtonElement>(host, 'model-save')
    expect(saveBtn.disabled).toBe(false)
    await act(async () => {
      saveBtn.click()
      await Promise.resolve()
      await Promise.resolve()
    })

    const call = updateCalls[0] as { id: string; body: { provider?: string | null } }
    expect(call.body.provider).toBe('flm')
    act(() => root.unmount())
  })

  it('picking Auto on a changed row ships provider: null', async () => {
    const { host, root } = mount(
      React.createElement(ModelDrawer, {
        open: true,
        onClose: () => {},
        model: { ...MODEL, provider: 'flm', provider_effective: 'flm' },
      }),
    )
    act(() => q<HTMLButtonElement>(host, 'model-provider-select').click())
    act(() => (host.querySelector('[data-option-id=""]') as HTMLElement).click())

    const saveBtn = q<HTMLButtonElement>(host, 'model-save')
    await act(async () => {
      saveBtn.click()
      await Promise.resolve()
      await Promise.resolve()
    })

    const call = updateCalls[0] as { id: string; body: { provider?: string | null } }
    expect(call.body.provider).toBeNull()
    act(() => root.unmount())
  })
})

describe('ModelDrawer context intelligence (Task 7)', () => {
  it('native chip renders from metadata.context_length; hidden when absent', () => {
    const { host, root } = mount(
      React.createElement(ModelDrawer, { open: true, onClose: () => {}, model: MODEL }),
    )
    expect(q(host, 'model-ctx-native-chip').textContent).toBe('native 128K')
    act(() => root.unmount())

    const noNative = { ...MODEL, metadata: { sha256: MODEL.metadata.sha256 } }
    const mounted2 = mount(
      React.createElement(ModelDrawer, { open: true, onClose: () => {}, model: noNative }),
    )
    expect(mounted2.host.querySelector('[data-testid="model-ctx-native-chip"]')).toBeNull()
    act(() => mounted2.root.unmount())
  })

  it('over-native value flips the chip to warn and shows the rope warnbox; Save stays enabled', () => {
    const { host, root } = mount(
      React.createElement(ModelDrawer, { open: true, onClose: () => {}, model: MODEL }),
    )
    const input = q<HTMLInputElement>(host, 'model-ctx-input')
    // MODEL's metadata.context_length is 131072 ("native 128K") — 262144 is
    // over-native and must never touch saveBlocked (#1378's gate is
    // untouched by this advisory signal).
    act(() => typeInto(input, '262144'))

    expect(q(host, 'model-ctx-native-chip').textContent).toBe('> native 128K')
    const warnBox = q(host, 'model-ctx-rope-warn')
    expect(warnBox.textContent).toContain('native 128K')
    expect(warnBox.textContent).toContain('Saves anyway.')
    expect(host.querySelector('[data-testid="model-ctx-error"]')).toBeNull()
    expect(q<HTMLButtonElement>(host, 'model-save').disabled).toBe(false)

    act(() => root.unmount())
  })

  it('feasibility line renders the hint for a verdict and nothing for unknown/empty', async () => {
    feasibilityHandler.current = (body) => ({
      results: [
        {
          model_id: body.models[0].model_id,
          verdict: 'tight',
          needed_mb: 20000,
          gtt_free_mb: 15000,
          gtt_total_mb: 24000,
        },
      ],
    })

    const { host, root } = mount(
      React.createElement(ModelDrawer, { open: true, onClose: () => {}, model: MODEL }),
    )

    // MODEL carries no defaults.context_size, so the ctx input seeds empty —
    // an empty ctx must never fire a probe at all.
    await act(async () => {
      await new Promise((r) => setTimeout(r, 450))
    })
    expect(feasibilityCalls.length).toBe(0)
    expect(host.querySelector('[data-testid="model-ctx-feasibility"]')).toBeNull()

    const input = q<HTMLInputElement>(host, 'model-ctx-input')
    await act(async () => {
      typeInto(input, '4096')
      await new Promise((r) => setTimeout(r, 450))
    })

    expect(feasibilityCalls.length).toBe(1)
    expect(feasibilityCalls[0]).toEqual({ models: [{ model_id: MODEL.id, ctx: 4096 }] })
    const expected = feasibilityHint({
      verdict: 'tight',
      needed_mb: 20000,
      gtt_free_mb: 15000,
      gtt_total_mb: 24000,
    })
    expect(q(host, 'model-ctx-feasibility').textContent).toBe(expected.text)

    act(() => root.unmount())
  })
})

// ─── Task 8 — seed consequence preview + provider-aware filter (#2205) ─────
// EVERY profile pick now routes through the consequence preview confirm; the
// old wouldClobber shortcut that skipped confirm when the current flags were
// already empty/matching is gone. The preview IS the confirm's message.
function confirmButton(host: HTMLElement, label: string) {
  return Array.from(host.querySelectorAll('button')).find(
    (b) => b.textContent === label,
  ) as HTMLButtonElement | undefined
}

describe('ModelDrawer seed consequence preview (Task 8, #2205)', () => {
  it('seed confirm shows the diff and restart consequence', async () => {
    profilesBox.current = [
      { name: 'brain', flags: '-fa auto --temp 0.2 --reasoning-budget 0' },
    ]
    slotsBox.current = [
      { name: 'agent', model_default: 'm1' },
      { name: 'brain', model_default: 'm1' },
    ]
    const model = {
      ...MODEL,
      defaults: { extra_args: '-fa auto --temp 0.4 --top-p 0.9' },
    }
    const { host, root } = mount(
      React.createElement(ModelDrawer, { open: true, onClose: () => {}, model }),
    )

    act(() => q<HTMLButtonElement>(host, 'model-seed-profile-open').click())
    act(() => q<HTMLButtonElement>(host, 'model-seed-profile-option-brain').click())

    const preview = q(host, 'model-seed-preview')
    expect(preview).toBeTruthy()
    expect(preview.textContent).toContain('1 changed')
    expect(preview.textContent).toContain('--temp 0.4→0.2')
    expect(preview.textContent).toContain('1 added')
    expect(preview.textContent).toContain('--reasoning-budget 0')
    expect(preview.textContent).toContain('1 removed')
    expect(preview.textContent).toContain('--top-p')
    expect(preview.textContent).toContain('seeded from brain')
    expect(preview.textContent).toContain('2 slots restart')
    expect(preview.textContent).toContain('agent')
    expect(preview.textContent).toContain('brain')

    const confirmBtn = confirmButton(host, 'Stamp tune')
    expect(confirmBtn).toBeTruthy()
    await act(async () => {
      confirmBtn!.click()
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(seedCalls).toEqual([{ id: 'm1', profile: 'brain' }])
    act(() => root.unmount())
  })

  it("no using slots → 'no slots currently load this model'", () => {
    profilesBox.current = [{ name: 'brain', flags: '--temp 0.2' }]
    slotsBox.current = []
    const model = { ...MODEL, defaults: { extra_args: '--temp 0.4' } }
    const { host, root } = mount(
      React.createElement(ModelDrawer, { open: true, onClose: () => {}, model }),
    )

    act(() => q<HTMLButtonElement>(host, 'model-seed-profile-open').click())
    act(() => q<HTMLButtonElement>(host, 'model-seed-profile-option-brain').click())

    expect(q(host, 'model-seed-preview').textContent).toContain(
      'no slots currently load this model',
    )
    act(() => root.unmount())
  })

  it('seed always previews — even from empty flags', () => {
    profilesBox.current = [{ name: 'brain', flags: '--temp 0.2' }]
    slotsBox.current = []
    const model = { ...MODEL, defaults: { extra_args: '' } }
    const { host, root } = mount(
      React.createElement(ModelDrawer, { open: true, onClose: () => {}, model }),
    )

    act(() => q<HTMLButtonElement>(host, 'model-seed-profile-open').click())
    act(() => q<HTMLButtonElement>(host, 'model-seed-profile-option-brain').click())

    // Picking still opens the preview confirm — no direct doSeed POST fires
    // before the operator hits "Stamp tune".
    expect(q(host, 'model-seed-preview')).toBeTruthy()
    expect(seedCalls).toEqual([])
    act(() => root.unmount())
  })

  it('failed seed keeps the dialog open for retry', async () => {
    profilesBox.current = [{ name: 'brain', flags: '--temp 0.2' }]
    slotsBox.current = []
    const model = { ...MODEL, defaults: { extra_args: '--temp 0.4' } }
    seedHandler.current = () => {
      throw new Error('network blip')
    }
    const { host, root } = mount(
      React.createElement(ModelDrawer, { open: true, onClose: () => {}, model }),
    )

    act(() => q<HTMLButtonElement>(host, 'model-seed-profile-open').click())
    act(() => q<HTMLButtonElement>(host, 'model-seed-profile-option-brain').click())

    const confirmBtn = confirmButton(host, 'Stamp tune')
    await act(async () => {
      confirmBtn!.click()
      await Promise.resolve()
      await Promise.resolve()
    })

    // The confirm dialog is still up for a retry — the preview box is proof.
    expect(q(host, 'model-seed-preview')).toBeTruthy()
    act(() => root.unmount())
  })

  it('profile menu is provider-aware (#2205)', () => {
    profilesBox.current = [
      { name: 'chat', flags: '--temp 0.7', runtime_family: 'llama-server' },
      { name: 'kokoro', flags: '--voice en', runtime_family: 'kokoro' },
    ]
    const model = { ...MODEL, provider_effective: 'kokoro', defaults: {} }
    const { host, root } = mount(
      React.createElement(ModelDrawer, { open: true, onClose: () => {}, model }),
    )

    act(() => q<HTMLButtonElement>(host, 'model-seed-profile-open').click())
    expect(q(host, 'model-seed-profile-option-kokoro')).toBeTruthy()
    expect(host.querySelector('[data-testid="model-seed-profile-option-chat"]')).toBeNull()
    act(() => root.unmount())
  })

  it('provider filter keeps the current provenance profile even if unfitting', () => {
    profilesBox.current = [
      { name: 'chat', flags: '--temp 0.7', runtime_family: 'llama-server' },
      { name: 'kokoro', flags: '--voice en', runtime_family: 'kokoro' },
    ]
    const model = {
      ...MODEL,
      provider_effective: 'kokoro',
      defaults: { extra_args: '--temp 0.7', profile: 'chat' },
    }
    const { host, root } = mount(
      React.createElement(ModelDrawer, { open: true, onClose: () => {}, model }),
    )

    act(() => q<HTMLButtonElement>(host, 'model-seed-profile-open').click())
    // "chat" doesn't fit kokoro's provider, but it's the current provenance —
    // the existing rule (fitProfiles, :762) keeps it offered regardless.
    expect(q(host, 'model-seed-profile-option-chat')).toBeTruthy()
    expect(q(host, 'model-seed-profile-option-kokoro')).toBeTruthy()
    act(() => root.unmount())
  })
})

// ─── Task 9 — Source disclosure + repo-fed mmproj picker ────────────────────
// The Source rows (MMProj / HF repo / HF filename) live behind a disclosure
// now, and MMProj is a RichSelect fed by POST /api/models/inspect whenever the
// row carries an hf_repo the probe can list projectors for.
//
// THE STORED SHAPE (pinned by "picking a variant stores…" below): `model.mmproj`
// is an ABSOLUTE host path, never a bare filename — see the mmprojDirOf comment
// in model-drawer.jsx for the source citations. The picker therefore offers
// repo FILENAMES but writes `<the model's directory>/<filename>`.
describe('ModelDrawer source disclosure (Task 9)', () => {
  const MODEL_DIR = '/var/lib/hal0/models/qwen'
  const SOURCE_MODEL = {
    ...MODEL,
    path: `${MODEL_DIR}/qwen-q8.gguf`,
    hf_repo: 'org/qwen-gguf',
    hf_filename: 'qwen-q8.gguf',
    mmproj: null,
  }
  const VARIANTS = [
    { id: 'mmproj-Q8_0.gguf', size_bytes: 966367641, size: '0.9 GB', info: 'mmproj' },
    { id: 'model-q8.gguf', size_bytes: 9771050700, size: '9.1 GB', info: 'weights' },
  ]
  const withVariants = () => {
    inspectHandler.current = () => ({ repo: 'org/qwen-gguf', variants: VARIANTS })
  }
  const openSource = (host: HTMLElement) =>
    act(() => q<HTMLElement>(host, 'model-source-disclosure').click())

  it('source collapses to a disclosure; opens on click', () => {
    const { host, root } = mount(
      React.createElement(ModelDrawer, { open: true, onClose: () => {}, model: SOURCE_MODEL }),
    )
    const disc = q<HTMLElement>(host, 'model-source-disclosure')
    expect(disc).toBeTruthy()
    expect(disc.textContent).toContain('Source')
    expect(disc.textContent).toContain('repo · file · mmproj · paths')
    expect(disc.getAttribute('aria-expanded')).toBe('false')
    // Collapsed: no source control is mounted, and no probe has fired.
    expect(q(host, 'model-hfrepo-input')).toBeNull()
    expect(q(host, 'model-hffile-input')).toBeNull()
    expect(q(host, 'model-source-paths')).toBeNull()
    expect(inspectCalls).toEqual([])

    openSource(host)
    expect(q<HTMLElement>(host, 'model-source-disclosure').getAttribute('aria-expanded')).toBe('true')
    // The two coord inputs keep their testids and stay editable inside.
    const repo = q<HTMLInputElement>(host, 'model-hfrepo-input')
    const file = q<HTMLInputElement>(host, 'model-hffile-input')
    expect(repo.value).toBe('org/qwen-gguf')
    expect(file.value).toBe('qwen-q8.gguf')
    act(() => typeInto(repo, 'org/other'))
    expect(q<HTMLInputElement>(host, 'model-hfrepo-input').value).toBe('org/other')
    act(() => root.unmount())
  })

  it('mmproj picker lists repo mmproj variants + none row', () => {
    withVariants()
    const { host, root } = mount(
      React.createElement(ModelDrawer, { open: true, onClose: () => {}, model: SOURCE_MODEL }),
    )
    openSource(host)
    // Lazy: the probe fires on the disclosure's first open, not on mount.
    expect(inspectCalls).toEqual([{ hf_repo: 'org/qwen-gguf' }])

    const trigger = q<HTMLButtonElement>(host, 'model-mmproj-select')
    expect(trigger).toBeTruthy()
    // The picker replaces the free-text absolute-path input entirely.
    expect(q(host, 'model-mmproj-input')).toBeNull()

    act(() => trigger.click())
    const none = host.querySelector('[data-option-id=""]') as HTMLElement
    expect(none.textContent).toContain('— none —')
    const pick = host.querySelector(
      `[data-option-id="${MODEL_DIR}/mmproj-Q8_0.gguf"]`,
    ) as HTMLElement
    expect(pick).toBeTruthy()
    expect(pick.textContent).toContain('mmproj-Q8_0.gguf')
    expect(pick.textContent).toContain('0.9 GB')
    // The repo's non-projector variant is not a projector choice.
    expect(host.textContent).not.toContain('model-q8.gguf')
    act(() => root.unmount())
  })

  it('picking a variant stores the absolute path under the model directory', async () => {
    withVariants()
    const { host, root } = mount(
      React.createElement(ModelDrawer, { open: true, onClose: () => {}, model: SOURCE_MODEL }),
    )
    openSource(host)
    act(() => q<HTMLButtonElement>(host, 'model-mmproj-select').click())
    act(() =>
      (host.querySelector(`[data-option-id="${MODEL_DIR}/mmproj-Q8_0.gguf"]`) as HTMLElement).click(),
    )

    // The resolved path is what the paths block reads back…
    expect(q<HTMLElement>(host, 'model-source-paths').textContent).toContain(
      `${MODEL_DIR}/mmproj-Q8_0.gguf`,
    )
    // …and what the PUT carries — an absolute host path, never the filename.
    const saveBtn = q<HTMLButtonElement>(host, 'model-save')
    expect(saveBtn.disabled).toBe(false)
    await act(async () => {
      saveBtn.click()
      await Promise.resolve()
      await Promise.resolve()
    })
    const call = updateCalls[0] as { body: { mmproj?: string | null } }
    expect(call.body.mmproj).toBe(`${MODEL_DIR}/mmproj-Q8_0.gguf`)
    act(() => root.unmount())
  })

  it('a variant nested in a repo subdirectory is basenamed onto the model directory', async () => {
    // Inspect variant ids are repo-RELATIVE PATHS (huggingface.py:357 `"id": rel`,
    // recursive tree walk), and nested projector/quant layouts are routine. The
    // pull worker basenames before persisting (pull.py:1071), so joining the id
    // verbatim would write a path no pull ever produces — and one that fails
    // screen_vision_mmproj's on-disk check.
    inspectHandler.current = () => ({
      repo: 'org/qwen-gguf',
      variants: [
        {
          id: 'UD-Q4_K_XL/mmproj-F16.gguf',
          size_bytes: 644245094,
          size: '0.6 GB',
          info: 'mmproj sidecar',
        },
      ],
    })
    const { host, root } = mount(
      React.createElement(ModelDrawer, { open: true, onClose: () => {}, model: SOURCE_MODEL }),
    )
    openSource(host)
    act(() => q<HTMLButtonElement>(host, 'model-mmproj-select').click())

    // The row still shows the full repo-relative id (two projectors that
    // flatten onto one filename stay distinguishable)…
    const pick = host.querySelector(
      `[data-option-id="${MODEL_DIR}/mmproj-F16.gguf"]`,
    ) as HTMLElement
    expect(pick).toBeTruthy()
    expect(pick.textContent).toContain('UD-Q4_K_XL/mmproj-F16.gguf')
    // …but the directory segment never reaches the stored value.
    expect(host.querySelector(`[data-option-id="${MODEL_DIR}/UD-Q4_K_XL/mmproj-F16.gguf"]`)).toBeNull()

    act(() => pick.click())
    await act(async () => {
      q<HTMLButtonElement>(host, 'model-save').click()
      await Promise.resolve()
      await Promise.resolve()
    })
    const call = updateCalls[0] as { body: { mmproj?: string | null } }
    expect(call.body.mmproj).toBe(`${MODEL_DIR}/mmproj-F16.gguf`)
    act(() => root.unmount())
  })

  it('stored mmproj value missing from the listing renders as its own row', () => {
    withVariants()
    const stored = '/srv/projectors/mmproj-handpicked.gguf'
    const { host, root } = mount(
      React.createElement(ModelDrawer, {
        open: true,
        onClose: () => {},
        model: { ...SOURCE_MODEL, mmproj: stored },
      }),
    )
    openSource(host)
    const trigger = q<HTMLButtonElement>(host, 'model-mmproj-select')
    // The closed control shows the stored value's own row — not a rewrite.
    expect(trigger.textContent).toContain('mmproj-handpicked.gguf')

    act(() => trigger.click())
    const own = host.querySelector(`[data-option-id="${stored}"]`) as HTMLElement
    expect(own).toBeTruthy()
    expect(own.getAttribute('aria-selected')).toBe('true')
    // The repo's own projector is offered beside it, resolved against the
    // STORED projector's directory (it owns the pairing, not model.path).
    expect(
      host.querySelector('[data-option-id="/srv/projectors/mmproj-Q8_0.gguf"]'),
    ).toBeTruthy()
    // Nothing was saved: an untouched picker leaves the row unchanged.
    expect(q<HTMLButtonElement>(host, 'model-save').disabled).toBe(true)

    // The stored row survives a pick away from it — otherwise "— none —" is a
    // one-way door and the only way back is closing the drawer, losing every
    // other unsaved edit with it.
    act(() => (host.querySelector('[data-option-id=""]') as HTMLElement).click())
    act(() => q<HTMLButtonElement>(host, 'model-mmproj-select').click())
    const again = host.querySelector(`[data-option-id="${stored}"]`) as HTMLElement
    expect(again).toBeTruthy()
    act(() => again.click())
    expect(q<HTMLButtonElement>(host, 'model-mmproj-select').textContent).toContain(
      'mmproj-handpicked.gguf',
    )
    expect(q<HTMLButtonElement>(host, 'model-save').disabled).toBe(true)
    act(() => root.unmount())
  })

  it('picking none on a vision model keeps the save gate', () => {
    withVariants()
    const { host, root } = mount(
      React.createElement(ModelDrawer, {
        open: true,
        onClose: () => {},
        model: {
          ...SOURCE_MODEL,
          capabilities: ['chat', 'vision'],
          mmproj: `${MODEL_DIR}/mmproj-Q8_0.gguf`,
        },
      }),
    )
    openSource(host)
    expect(q(host, 'model-mmproj-error')).toBeNull()

    act(() => q<HTMLButtonElement>(host, 'model-mmproj-select').click())
    act(() => (host.querySelector('[data-option-id=""]') as HTMLElement).click())

    const err = q<HTMLElement>(host, 'model-mmproj-error')
    expect(err).toBeTruthy()
    expect(err.textContent).toContain('mmproj')
    expect(q<HTMLButtonElement>(host, 'model-save').disabled).toBe(true)

    // Re-pairing the projector releases the gate again.
    act(() => q<HTMLButtonElement>(host, 'model-mmproj-select').click())
    act(() =>
      (host.querySelector(`[data-option-id="${MODEL_DIR}/mmproj-Q8_0.gguf"]`) as HTMLElement).click(),
    )
    expect(q(host, 'model-mmproj-error')).toBeNull()
    act(() => root.unmount())
  })

  it('no hf_repo → text input fallback, and no probe fires', () => {
    withVariants()
    const { host, root } = mount(
      React.createElement(ModelDrawer, {
        open: true,
        onClose: () => {},
        model: { ...SOURCE_MODEL, hf_repo: '' },
      }),
    )
    openSource(host)
    expect(inspectCalls).toEqual([])
    expect(q(host, 'model-mmproj-select')).toBeNull()
    const input = q<HTMLInputElement>(host, 'model-mmproj-input')
    expect(input).toBeTruthy()
    act(() => typeInto(input, '/srv/mmproj-typed.gguf'))
    expect(q<HTMLInputElement>(host, 'model-mmproj-input').value).toBe('/srv/mmproj-typed.gguf')
    act(() => root.unmount())
  })

  it('inspect failure → text input fallback', () => {
    inspectHandler.current = () => new Error('inspect unreachable')
    const { host, root } = mount(
      React.createElement(ModelDrawer, { open: true, onClose: () => {}, model: SOURCE_MODEL }),
    )
    openSource(host)
    expect(inspectCalls).toEqual([{ hf_repo: 'org/qwen-gguf' }])
    expect(q(host, 'model-mmproj-select')).toBeNull()
    expect(q(host, 'model-mmproj-input')).toBeTruthy()
    act(() => root.unmount())
  })

  it('a repo with no projector files falls back to the text input too', () => {
    inspectHandler.current = () => ({ repo: 'org/qwen-gguf', variants: [VARIANTS[1]] })
    const { host, root } = mount(
      React.createElement(ModelDrawer, { open: true, onClose: () => {}, model: SOURCE_MODEL }),
    )
    openSource(host)
    expect(q(host, 'model-mmproj-select')).toBeNull()
    expect(q(host, 'model-mmproj-input')).toBeTruthy()
    act(() => root.unmount())
  })

  it('paths render read-only and copyable', () => {
    const copied: string[] = []
    Object.defineProperty(globalThis.navigator, 'clipboard', {
      value: { writeText: (t: string) => copied.push(t) },
      configurable: true,
    })
    const stored = `${MODEL_DIR}/mmproj-Q8_0.gguf`
    const { host, root } = mount(
      React.createElement(ModelDrawer, {
        open: true,
        onClose: () => {},
        model: { ...SOURCE_MODEL, mmproj: stored },
      }),
    )
    openSource(host)
    const paths = q<HTMLElement>(host, 'model-source-paths')
    expect(paths).toBeTruthy()
    expect(paths.textContent).toContain(`${MODEL_DIR}/qwen-q8.gguf`)
    expect(paths.textContent).toContain(stored)
    // Read-only: the block holds no input/textarea at all.
    expect(paths.querySelector('input')).toBeNull()
    expect(paths.querySelector('textarea')).toBeNull()

    act(() => q<HTMLButtonElement>(host, 'model-source-copy-path').click())
    act(() => q<HTMLButtonElement>(host, 'model-source-copy-mmproj').click())
    expect(copied).toEqual([`${MODEL_DIR}/qwen-q8.gguf`, stored])
    act(() => root.unmount())
  })

  it('the mmproj path line flags an unsaved pick instead of implying on-disk truth', () => {
    withVariants()
    const { host, root } = mount(
      React.createElement(ModelDrawer, { open: true, onClose: () => {}, model: SOURCE_MODEL }),
    )
    openSource(host)
    // Nothing picked yet: the line is a plain readback of the stored value.
    expect(q<HTMLElement>(host, 'model-source-paths').textContent).not.toContain('unsaved')

    act(() => q<HTMLButtonElement>(host, 'model-mmproj-select').click())
    act(() =>
      (host.querySelector(`[data-option-id="${MODEL_DIR}/mmproj-Q8_0.gguf"]`) as HTMLElement).click(),
    )
    const paths = q<HTMLElement>(host, 'model-source-paths')
    expect(paths.textContent).toContain(`${MODEL_DIR}/mmproj-Q8_0.gguf`)
    expect(paths.textContent).toContain('unsaved selection')
    act(() => root.unmount())
  })

  it('a clipboard that throws reports the failure instead of a false success', () => {
    const toasts: [string, string][] = []
    ;(globalThis as unknown as { __hal0Toast: unknown }).__hal0Toast = (m: string, k: string) =>
      toasts.push([m, k])
    Object.defineProperty(globalThis.navigator, 'clipboard', {
      value: {
        writeText: () => {
          throw new Error('NotAllowedError')
        },
      },
      configurable: true,
    })
    const { host, root } = mount(
      React.createElement(ModelDrawer, { open: true, onClose: () => {}, model: SOURCE_MODEL }),
    )
    openSource(host)
    act(() => q<HTMLButtonElement>(host, 'model-source-copy-path').click())
    expect(toasts).toEqual([['Copy failed — clipboard unavailable', 'err']])
    delete (globalThis as unknown as { __hal0Toast?: unknown }).__hal0Toast
    act(() => root.unmount())
  })
})
