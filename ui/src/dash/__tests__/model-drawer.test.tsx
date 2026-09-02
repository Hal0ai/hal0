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

;(globalThis as unknown as { window: typeof globalThis }).window = globalThis
;(globalThis as unknown as { React: typeof React }).React = React
// FieldInfoIcon (primitives.jsx) escapes overflow via ReactDOM.createPortal —
// mirrors src/globals-install.ts's merge of react-dom/client + createPortal
// onto one global.
;(globalThis as unknown as { ReactDOM: unknown }).ReactDOM = { ...ReactDOMClient, createPortal }
;(globalThis as unknown as { Icons: unknown }).Icons = new Proxy({}, { get: () => null })

const { updateCalls, setDefaultCalls, setDefaultResult, slotsBox, profilesBox } = vi.hoisted(() => ({
  updateCalls: [] as unknown[],
  setDefaultCalls: [] as unknown[],
  setDefaultResult: { current: { default: true, type: 'llm' } as Record<string, unknown> },
  slotsBox: { current: [] as Record<string, unknown>[] },
  profilesBox: { current: [] as Record<string, unknown>[] },
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
}))

vi.mock('@/api/hooks/useModelSeedProfile', () => ({
  useModelSeedProfile: () => ({ mutateAsync: async () => ({}), isPending: false }),
}))

vi.mock('@/api/hooks/useChatTemplates', () => ({
  useChatTemplates: () => ({ data: [], isLoading: false }),
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
  return { ...actual, useMeta: () => ({ data: undefined }), useMetaEnums: () => actual.resolveMetaEnums(undefined) }
})

vi.mock('@/api/hooks/useSlots', () => ({
  useSlots: () => ({ data: slotsBox.current, isLoading: false }),
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
