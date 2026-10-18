// @vitest-environment happy-dom
//
// Model drawer (ADR-0029, profile-centric config). The model is a FILE: the
// drawer edits identity, the source coordinates, and three file facts
// (top-level on the model: chat template, jinja, "the file has MTP heads"), and shows the
// native context window read-only. Every tune lives on a profile, so the flag
// editor, seed-from-profile, divergence diff, context input and the
// Thinking/Vision overrides are gone and the save body can never carry them.
//
// model-drawer.jsx is a window-globals dash module (bare `Drawer`/
// `FieldInfoIcon`/`Icons`/... identifiers resolve off `window`), so the
// globals are installed before the dynamic import — same pattern as
// profiles.test.tsx / runner-images-view.test.tsx.
import React, { act } from 'react'
import * as ReactDOMClient from 'react-dom/client'
import { createPortal } from 'react-dom'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

;(globalThis as unknown as { window: typeof globalThis }).window = globalThis
;(globalThis as unknown as { React: typeof React }).React = React
// FieldInfoIcon (primitives.jsx) escapes overflow via ReactDOM.createPortal —
// mirrors src/globals-install.ts's merge of react-dom/client + createPortal
// onto one global.
;(globalThis as unknown as { ReactDOM: unknown }).ReactDOM = { ...ReactDOMClient, createPortal }
;(globalThis as unknown as { Icons: unknown }).Icons = new Proxy({}, { get: () => null })

const { updateCalls, setDefaultCalls, setDefaultResult, slotsBox, templatesBox, enumsBox } = vi.hoisted(() => ({
  updateCalls: [] as unknown[],
  setDefaultCalls: [] as unknown[],
  setDefaultResult: { current: { default: true, type: 'llm' } as Record<string, unknown> },
  slotsBox: { current: [] as Record<string, unknown>[] },
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

// Task 13: the Drafter field's fit check (useInspectDraft) and Pull action
// (usePullJob). `draftFit.current` is what inspect-draft resolves to;
// `pullBox` is the stand-in job the drawer watches for completion;
// `updateError.current` makes the next PUT reject like the server's 422.
const { inspectDraftCalls, draftFit, pullCalls, pullBox, updateError, measureCalls } = vi.hoisted(() => ({
  inspectDraftCalls: [] as unknown[],
  measureCalls: [] as string[],
  draftFit: { current: undefined as Record<string, unknown> | undefined },
  pullCalls: [] as unknown[],
  pullBox: { current: { state: 'idle', modelId: null as string | null, jobId: null as string | null } },
  updateError: { current: undefined as Error | undefined },
}))

vi.mock('@/api/hooks/useModels', () => ({
  useMeasureSpeculation: () => ({
    mutateAsync: async (id: string) => {
      measureCalls.push(id)
      return { queued: [{}, {}] }
    },
    isPending: false,
  }),
  useModelUpdate: () => ({
    mutateAsync: async (b: unknown) => {
      updateCalls.push(b)
      if (updateError.current) throw updateError.current
      return b
    },
    isPending: false,
  }),
  useInspectDraft: () => {
    const [data, setData] = React.useState<Record<string, unknown> | undefined>()
    return {
      data,
      isPending: false,
      mutate: (body: unknown) => {
        inspectDraftCalls.push(body)
        setData(draftFit.current)
      },
      reset: () => setData(undefined),
    }
  },
  usePullJob: () => ({
    ...pullBox.current,
    inFlight: pullBox.current.state === 'queued' || pullBox.current.state === 'running',
    start: async (id: string, body: unknown) => {
      pullCalls.push({ id, body })
    },
    reset: () => {},
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

vi.mock('@/api/hooks/useChatTemplates', () => ({
  useChatTemplates: () => ({ data: templatesBox.current, isLoading: false }),
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
  templatesBox.current = []
  enumsBox.current = undefined
  inspectCalls.length = 0
  inspectHandler.current = undefined
  inspectDraftCalls.length = 0
  draftFit.current = undefined
  pullCalls.length = 0
  pullBox.current = { state: 'idle', modelId: null, jobId: null }
  updateError.current = undefined
  measureCalls.length = 0
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

const q = <T extends HTMLElement>(host: HTMLElement, testid: string) =>
  host.querySelector(`[data-testid="${testid}"]`) as T
const press = (el: HTMLElement, key: string) =>
  act(() => {
    el.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }))
  })

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

    const call = updateCalls[0] as { id: string; body: Record<string, unknown> }
    expect(call.body.chat_template).toBe('broken')
    expect(call.body).not.toHaveProperty('defaults')
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

// ─── ADR-0029 — the model carries file facts only ───────────────────────────
// Every tune moved to the profile. These pin the absence of the old controls
// and, more importantly, that the save body can never carry a tuning key (the
// server answers any of them with a 422).
const TUNING_TESTIDS = [
  'model-flags-input',
  'model-tune-raw-toggle',
  'model-seed-profile-open',
  'model-divergence-diff',
  'model-provenance-chip',
  'model-diverged-chip',
  'model-ctx-input',
  'model-ctx-error',
  'model-ctx-feasibility',
  'model-ctx-rope-warn',
  'model-cap-override-thinking',
  'model-cap-override-vision',
]
const TUNING_KEYS = [
  'extra_args',
  'context_size',
  'profile',
  'enable_thinking',
  'vision',
  'n_gpu_layers',
  'rope_freq_base',
]
// A row as the server hands it back (ADR-0029 §6): the four file facts sit
// TOP-LEVEL on the model; there is no nested `defaults` object.
const STALE_MODEL = {
  ...MODEL,
  chat_template: 'chatml',
  mtp: true,
  jinja: null,
  tokenizer_repo: 'org/tok',
}

describe('ModelDrawer facts only (ADR-0029)', () => {
  it('renders none of the old tune controls', () => {
    const { host, root } = mount(
      React.createElement(ModelDrawer, { open: true, onClose: () => {}, model: STALE_MODEL }),
    )
    for (const id of TUNING_TESTIDS) expect(host.querySelector(`[data-testid="${id}"]`)).toBeNull()
    expect(host.textContent).not.toContain('Seed from profile')
    expect(host.textContent).not.toContain('Context size')
    // The facts that remain are all there.
    expect(q(host, 'model-chat-template')).toBeTruthy()
    expect(q(host, 'model-provider-select')).toBeTruthy()
    expect(q(host, 'model-cap-override-mtp')).toBeTruthy()
    expect(q(host, 'model-tune-note').textContent).toContain('profile')
    act(() => root.unmount())
  })

  it('only offers MTP heads and Jinja as overrides', () => {
    const { host, root } = mount(
      React.createElement(ModelDrawer, { open: true, onClose: () => {}, model: MODEL }),
    )
    act(() => q<HTMLButtonElement>(host, 'model-cap-override-add').click())
    expect(q(host, 'model-cap-override-add-mtp-on')).toBeTruthy()
    expect(q(host, 'model-cap-override-add-jinja-off')).toBeTruthy()
    expect(q(host, 'model-cap-override-add-thinking-on')).toBeNull()
    expect(q(host, 'model-cap-override-add-vision-off')).toBeNull()
    act(() => root.unmount())
  })

  it('shows the native context window read-only, and hides it when the row has none', () => {
    const { host, root } = mount(
      React.createElement(ModelDrawer, { open: true, onClose: () => {}, model: MODEL }),
    )
    const native = q<HTMLElement>(host, 'model-ctx-native')
    expect(native.textContent).toContain('128K')
    expect(native.textContent).toContain('131,072')
    expect(native.tagName).not.toBe('INPUT')
    act(() => root.unmount())

    const bare = mount(
      React.createElement(ModelDrawer, { open: true, onClose: () => {}, model: { ...MODEL, metadata: {} } }),
    )
    expect(q<HTMLElement>(bare.host, 'model-ctx-native').textContent).toContain('not recorded')
    act(() => bare.root.unmount())
  })

  it('a clean drawer does not save, with the facts top-level on the row', () => {
    const { host, root } = mount(
      React.createElement(ModelDrawer, { open: true, onClose: () => {}, model: STALE_MODEL }),
    )
    expect(q<HTMLButtonElement>(host, 'model-save').disabled).toBe(true)
    act(() => root.unmount())
  })

  it('the save body carries only file facts — never a tuning key', async () => {
    const { host, root } = mount(
      React.createElement(ModelDrawer, { open: true, onClose: () => {}, model: STALE_MODEL }),
    )
    // Stage a fact change: mark jinja unsupported.
    act(() => q<HTMLButtonElement>(host, 'model-cap-override-add').click())
    act(() => q<HTMLButtonElement>(host, 'model-cap-override-add-jinja-off').click())
    const saveBtn = q<HTMLButtonElement>(host, 'model-save')
    expect(saveBtn.disabled).toBe(false)
    await act(async () => {
      saveBtn.click()
      await Promise.resolve()
      await Promise.resolve()
    })
    const call = updateCalls[0] as { id: string; body: Record<string, unknown> }
    // Top-level, and ONLY the fact that changed: untouched chat_template / mtp
    // / tokenizer_repo are not re-sent (absent = keep the stored value).
    expect(call.body).toEqual({ jinja: false })
    expect(call.body).not.toHaveProperty('defaults')
    for (const k of TUNING_KEYS) expect(call.body).not.toHaveProperty(k)
    expect(JSON.stringify(call.body)).not.toMatch(/extra_args|context_size|enable_thinking|rope_freq_base|n_gpu_layers/)
    act(() => root.unmount())
  })

  it('clearing a fact sends an explicit null, top-level', async () => {
    const { host, root } = mount(
      React.createElement(ModelDrawer, { open: true, onClose: () => {}, model: STALE_MODEL }),
    )
    // mtp starts as an explicit override (true); the chip's ✕ returns it to Auto.
    act(() =>
      (host.querySelector('[aria-label="Remove MTP heads override"]') as HTMLButtonElement).click(),
    )
    await act(async () => {
      q<HTMLButtonElement>(host, 'model-save').click()
      await Promise.resolve()
      await Promise.resolve()
    })
    const call = updateCalls[0] as { body: Record<string, unknown> }
    expect(call.body).toEqual({ mtp: null })
    act(() => root.unmount())
  })

  it('an identity-only edit ships no facts', async () => {
    const { host, root } = mount(
      React.createElement(ModelDrawer, { open: true, onClose: () => {}, model: STALE_MODEL }),
    )
    act(() => q<HTMLButtonElement>(host, 'model-title-edit').click())
    const input = q<HTMLInputElement>(host, 'model-title-input')
    act(() => typeInto(input, 'qwen-renamed'))
    press(input, 'Enter')
    await act(async () => {
      q<HTMLButtonElement>(host, 'model-save').click()
      await Promise.resolve()
      await Promise.resolve()
    })
    const call = updateCalls[0] as { body: Record<string, unknown> }
    // Identity edit only: no fact changed, so none rides along.
    expect(call.body).toEqual({ name: 'qwen-renamed' })
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
    { id: 'model-q8.gguf', size_bytes: 9771050700, size: '9.1 GB', info: 'weights' },
  ]
  const MMPROJ = [{ id: 'mmproj-Q8_0.gguf', size_bytes: 966367641, size: '0.9 GB' }]
  const withVariants = () => {
    inspectHandler.current = () => ({
      repo: 'org/qwen-gguf',
      variants: VARIANTS,
      mmproj: MMPROJ,
      drafts: [],
    })
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
      variants: [],
      mmproj: [{ id: 'UD-Q4_K_XL/mmproj-F16.gguf', size_bytes: 644245094, size: '0.6 GB' }],
      drafts: [],
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
    inspectHandler.current = () => ({
      repo: 'org/qwen-gguf',
      variants: VARIANTS,
      mmproj: [],
      drafts: [],
    })
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

  it('a clipboard that throws reports the failure instead of a false success', async () => {
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
    // The legacy execCommand fallback is unavailable too — full copy failure.
    Object.defineProperty(globalThis.document, 'execCommand', {
      value: () => false,
      configurable: true,
    })
    const { host, root } = mount(
      React.createElement(ModelDrawer, { open: true, onClose: () => {}, model: SOURCE_MODEL }),
    )
    openSource(host)
    await act(async () => q<HTMLButtonElement>(host, 'model-source-copy-path').click())
    expect(toasts).toEqual([['Copy failed — clipboard unavailable', 'err']])
    delete (globalThis as unknown as { __hal0Toast?: unknown }).__hal0Toast
    act(() => root.unmount())
  })

  it('an ASYNC clipboard rejection reports the failure too (#2214)', async () => {
    const toasts: [string, string][] = []
    ;(globalThis as unknown as { __hal0Toast: unknown }).__hal0Toast = (m: string, k: string) =>
      toasts.push([m, k])
    Object.defineProperty(globalThis.navigator, 'clipboard', {
      value: { writeText: () => Promise.reject(new Error('NotAllowedError')) },
      configurable: true,
    })
    Object.defineProperty(globalThis.document, 'execCommand', {
      value: () => false,
      configurable: true,
    })
    const { host, root } = mount(
      React.createElement(ModelDrawer, { open: true, onClose: () => {}, model: SOURCE_MODEL }),
    )
    openSource(host)
    await act(async () => q<HTMLButtonElement>(host, 'model-source-copy-path').click())
    expect(toasts).toEqual([['Copy failed — clipboard unavailable', 'err']])
    delete (globalThis as unknown as { __hal0Toast?: unknown }).__hal0Toast
    act(() => root.unmount())
  })
})

// ─── Task 10 — live staged footer ───────────────────────────────────────────
describe('footer summary', () => {
  it('clean drawer → "no changes"', () => {
    const { host, root } = mount(
      React.createElement(ModelDrawer, { open: true, onClose: () => {}, model: MODEL }),
    )
    const footer = q<HTMLElement>(host, 'model-foot-summary')
    expect(footer).toBeTruthy()
    expect(footer.textContent).toBe('no changes')
    act(() => root.unmount())
  })

  it('staged changes → summary with field names and slot restart info', () => {
    slotsBox.current = [{ name: 'agent', model_default: 'm1' }, { name: 'brain', model_default: 'm2', model: 'm1' }]
    const { host, root } = mount(
      React.createElement(ModelDrawer, { open: true, onClose: () => {}, model: MODEL }),
    )
    // Stage a fact change (jinja off) with 2 slots using this model.
    act(() => q<HTMLButtonElement>(host, 'model-cap-override-add').click())
    act(() => q<HTMLButtonElement>(host, 'model-cap-override-add-jinja-off').click())

    const footer = q<HTMLElement>(host, 'model-foot-summary')
    expect(footer).toBeTruthy()
    expect(footer.textContent).toContain('1 change')
    expect(footer.textContent).toContain('overrides')
    expect(footer.textContent).toContain('restarts 2 slots')
    expect(footer.textContent).toContain('agent')
    expect(footer.textContent).toContain('brain')
    act(() => root.unmount())
  })

  it('zero using slots → restart clause omitted', () => {
    const { host, root } = mount(
      React.createElement(ModelDrawer, { open: true, onClose: () => {}, model: MODEL }),
    )
    act(() => q<HTMLButtonElement>(host, 'model-cap-override-add').click())
    act(() => q<HTMLButtonElement>(host, 'model-cap-override-add-mtp-on').click())

    const footer = q<HTMLElement>(host, 'model-foot-summary')
    expect(footer).toBeTruthy()
    expect(footer.textContent).toContain('1 change')
    expect(footer.textContent).toContain('overrides')
    expect(footer.textContent).not.toContain('restarts')
    act(() => root.unmount())
  })
})

describe('ModelDrawer Drafter field (ADR-0031 §5)', () => {
  const MODEL_DIR = '/var/lib/hal0/models/qwen'
  const DRAFT_MODEL = {
    ...MODEL,
    path: `${MODEL_DIR}/qwen-q8.gguf`,
    hf_repo: 'org/qwen-gguf',
    hf_filename: 'qwen-q8.gguf',
    mmproj: null,
    draft: null,
    draftVariant: null,
  }
  const DRAFTS = [
    {
      id: 'Qwen-LiLiCorr-Q8.gguf',
      repo: 'org/qwen-gguf-LiLiCorr',
      kind_hint: 'dflash',
      variant_hint: 'lilicorr',
      size_bytes: 1073741824,
      size: '1.0 GB',
      source: 'companion',
    },
  ]
  const withDrafts = () => {
    inspectHandler.current = () => ({
      repo: 'org/qwen-gguf',
      variants: [],
      mmproj: [],
      drafts: DRAFTS,
    })
  }
  const openSource = (host: HTMLElement) =>
    act(() => q<HTMLElement>(host, 'model-source-disclosure').click())
  const save = async (host: HTMLElement) =>
    act(async () => {
      q<HTMLButtonElement>(host, 'model-save').click()
      await Promise.resolve()
      await Promise.resolve()
    })

  it('the picker lists the repo drafters, the stored row and a none row', () => {
    withDrafts()
    const stored = `${MODEL_DIR}/hand-placed-draft.gguf`
    const { host, root } = mount(
      React.createElement(ModelDrawer, {
        open: true,
        onClose: () => {},
        model: { ...DRAFT_MODEL, draft: stored, draftVariant: 'dflash' },
      }),
    )
    openSource(host)
    expect(q(host, 'model-draft-input')).toBeNull()
    act(() => q<HTMLButtonElement>(host, 'model-draft-select').click())
    expect((host.querySelector('[data-option-id=""]') as HTMLElement).textContent).toContain('— none —')
    const pick = host.querySelector(`[data-option-id="${MODEL_DIR}/Qwen-LiLiCorr-Q8.gguf"]`) as HTMLElement
    expect(pick.textContent).toContain('Qwen-LiLiCorr-Q8.gguf')
    expect(pick.textContent).toContain('1.0 GB')
    const row = host.querySelector(`[data-option-id="${stored}"]`) as HTMLElement
    expect(row.textContent).toContain('stored')
    act(() => root.unmount())
  })

  it('no drafters in the repo → a path input; typing a path and saving sends draft', async () => {
    inspectHandler.current = () => ({ repo: 'org/qwen-gguf', variants: [], mmproj: [], drafts: [] })
    const { host, root } = mount(
      React.createElement(ModelDrawer, { open: true, onClose: () => {}, model: DRAFT_MODEL }),
    )
    openSource(host)
    expect(q(host, 'model-draft-select')).toBeNull()
    act(() => typeInto(q<HTMLInputElement>(host, 'model-draft-input'), `  ${MODEL_DIR}/d.gguf `))
    await save(host)
    expect((updateCalls[0] as { body: { draft?: string } }).body.draft).toBe(`${MODEL_DIR}/d.gguf`)
    act(() => root.unmount())
  })

  it('a draft_mismatch refusal shows the first reason in the fit band', async () => {
    inspectHandler.current = () => ({ repo: 'org/qwen-gguf', variants: [], mmproj: [], drafts: [] })
    const err = Object.assign(new Error('mismatch'), {
      code: 'model.draft_mismatch',
      details: { reasons: ['hidden size 2048 != target 5120', 'second'] },
    })
    updateError.current = err
    const { host, root } = mount(
      React.createElement(ModelDrawer, { open: true, onClose: () => {}, model: DRAFT_MODEL }),
    )
    openSource(host)
    expect(q(host, 'model-draft-fit')).toBeNull()
    act(() => typeInto(q<HTMLInputElement>(host, 'model-draft-input'), `${MODEL_DIR}/d.gguf`))
    await save(host)
    const band = q<HTMLElement>(host, 'model-draft-fit')
    expect(band.textContent).toContain('hidden size 2048 != target 5120')
    expect(band.textContent).not.toContain('second')
    // Editing the path retires the stale verdict.
    act(() => typeInto(q<HTMLInputElement>(host, 'model-draft-input'), `${MODEL_DIR}/e.gguf`))
    expect(q(host, 'model-draft-fit')).toBeNull()
    act(() => root.unmount())
  })

  it('a repo pick is fit-checked, then pulled — never PUT', async () => {
    withDrafts()
    draftFit.current = {
      kind: 'dflash',
      variant: 'lilicorr',
      fit: { ok: true, reasons: [] },
      draft: { variant: 'lilicorr', block_size: 8, embedding_length: 5120, target_layers: [1, 2, 3, 4, 5, 6] },
    }
    const { host, root } = mount(
      React.createElement(ModelDrawer, { open: true, onClose: () => {}, model: DRAFT_MODEL }),
    )
    openSource(host)
    expect(q(host, 'model-draft-pull')).toBeNull()
    act(() => q<HTMLButtonElement>(host, 'model-draft-select').click())
    act(() =>
      (host.querySelector(`[data-option-id="${MODEL_DIR}/Qwen-LiLiCorr-Q8.gguf"]`) as HTMLElement).click(),
    )
    expect(inspectDraftCalls).toEqual([
      {
        hf_repo: 'org/qwen-gguf',
        hf_filename: 'qwen-q8.gguf',
        draft_repo: 'org/qwen-gguf-LiLiCorr',
        draft_filename: 'Qwen-LiLiCorr-Q8.gguf',
      },
    ])
    expect(q<HTMLElement>(host, 'model-draft-fit').textContent).toContain(
      'matches: hidden 5120, 6 target layers, block 8',
    )
    await act(async () => {
      q<HTMLButtonElement>(host, 'model-draft-pull').click()
      await Promise.resolve()
    })
    expect(pullCalls).toEqual([
      {
        id: 'm1',
        body: {
          hf_repo: 'org/qwen-gguf',
          hf_filename: 'qwen-q8.gguf',
          draft_filename: 'Qwen-LiLiCorr-Q8.gguf',
          draft_repo: 'org/qwen-gguf-LiLiCorr',
        },
      },
    ])
    expect(updateCalls).toEqual([])
    act(() => root.unmount())
  })

  it('a refused repo pick shows the first reason', () => {
    withDrafts()
    draftFit.current = {
      kind: 'dflash',
      variant: 'lilicorr',
      fit: { ok: false, reasons: ['hidden size 2048 != target 5120'] },
      draft: { variant: 'lilicorr' },
    }
    const { host, root } = mount(
      React.createElement(ModelDrawer, { open: true, onClose: () => {}, model: DRAFT_MODEL }),
    )
    openSource(host)
    act(() => q<HTMLButtonElement>(host, 'model-draft-select').click())
    act(() =>
      (host.querySelector(`[data-option-id="${MODEL_DIR}/Qwen-LiLiCorr-Q8.gguf"]`) as HTMLElement).click(),
    )
    expect(q<HTMLElement>(host, 'model-draft-fit').textContent).toContain('hidden size 2048 != target 5120')
    act(() => root.unmount())
  })

  it('unpair sends draft: null and keeps the drawer open', async () => {
    withDrafts()
    const { host, root } = mount(
      React.createElement(ModelDrawer, {
        open: true,
        onClose: () => {},
        model: { ...DRAFT_MODEL, draft: `${MODEL_DIR}/d.gguf`, draftVariant: 'dflash' },
      }),
    )
    openSource(host)
    await act(async () => {
      q<HTMLButtonElement>(host, 'model-draft-unpair').click()
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(updateCalls).toEqual([{ id: 'm1', body: { draft: null } }])
    expect(q(host, 'model-draft-unpair')).toBeNull()
    act(() => root.unmount())
  })

  it('renders the measure button only when a drafter is paired and a slot runs the model', async () => {
    withDrafts()
    const toasts: string[] = []
    ;(window as unknown as { __hal0Toast: (m: string) => void }).__hal0Toast = (m) => toasts.push(m)
    const paired = { ...DRAFT_MODEL, draft: `${MODEL_DIR}/d.gguf`, draftVariant: 'dflash' }
    const mountDrawer = (model: Record<string, unknown>) =>
      mount(React.createElement(ModelDrawer, { open: true, onClose: () => {}, model }))

    slotsBox.current = []
    let m = mountDrawer(paired)
    openSource(m.host)
    expect(q(m.host, 'model-draft-measure')).toBeNull() // no slot runs it
    act(() => m.root.unmount())

    slotsBox.current = [{ name: 'agent', model_default: 'm1' }]
    m = mountDrawer(DRAFT_MODEL)
    openSource(m.host)
    expect(q(m.host, 'model-draft-measure')).toBeNull() // nothing paired
    act(() => m.root.unmount())

    m = mountDrawer(paired)
    openSource(m.host)
    await act(async () => {
      q<HTMLButtonElement>(m.host, 'model-draft-measure').click()
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(measureCalls).toEqual(['m1'])
    expect(toasts).toContain('Measuring m1: two bench cells queued')
    act(() => m.root.unmount())
  })

  it('no unpair action without a pairing', () => {
    withDrafts()
    const { host, root } = mount(
      React.createElement(ModelDrawer, { open: true, onClose: () => {}, model: DRAFT_MODEL }),
    )
    openSource(host)
    expect(q(host, 'model-draft-unpair')).toBeNull()
    act(() => root.unmount())
  })

  const pickRepoDraft = (host: HTMLElement) => {
    act(() => q<HTMLButtonElement>(host, 'model-draft-select').click())
    act(() =>
      (host.querySelector(`[data-option-id="${MODEL_DIR}/Qwen-LiLiCorr-Q8.gguf"]`) as HTMLElement).click(),
    )
  }

  it('a completed pull is adopted: Pull and the pending hint go, Unpair appears', async () => {
    withDrafts()
    const { host, root } = mount(
      React.createElement(ModelDrawer, { open: true, onClose: () => {}, model: DRAFT_MODEL }),
    )
    openSource(host)
    pickRepoDraft(host)
    await act(async () => {
      q<HTMLButtonElement>(host, 'model-draft-pull').click()
      await Promise.resolve()
    })
    expect(q(host, 'model-draft-unpair')).toBeNull()
    pullBox.current = { state: 'completed', modelId: 'm1', jobId: 'j1' }
    act(() => {
      root.render(
        React.createElement(
          QueryClientProvider,
          { client: new QueryClient() },
          React.createElement(ModelDrawer, { open: true, onClose: () => {}, model: DRAFT_MODEL }),
        ),
      )
    })
    expect(q(host, 'model-draft-pull')).toBeNull()
    expect(q(host, 'model-draft-pending')).toBeNull()
    expect(q(host, 'model-draft-unpair')).toBeTruthy()
    expect(updateCalls).toEqual([])
    act(() => root.unmount())
  })

  it('an unpaired file stays pickable and re-pairs with a plain PUT, no pull', async () => {
    withDrafts()
    const kept = `${MODEL_DIR}/kept-draft.gguf`
    const { host, root } = mount(
      React.createElement(ModelDrawer, {
        open: true,
        onClose: () => {},
        model: { ...DRAFT_MODEL, draft: kept, draftVariant: 'dflash' },
      }),
    )
    openSource(host)
    await act(async () => {
      q<HTMLButtonElement>(host, 'model-draft-unpair').click()
      await Promise.resolve()
      await Promise.resolve()
    })
    updateCalls.length = 0
    act(() => q<HTMLButtonElement>(host, 'model-draft-select').click())
    const row = host.querySelector(`[data-option-id="${kept}"]`) as HTMLElement
    expect(row.textContent).toContain('on disk, not paired')
    act(() => row.click())
    expect(q(host, 'model-draft-pull')).toBeNull()
    expect(q(host, 'model-draft-pending')).toBeNull()
    await save(host)
    expect(updateCalls).toEqual([{ id: 'm1', body: { draft: kept } }])
    act(() => root.unmount())
  })

  it('the kept file also listed in the repo still reads "on disk, not paired" and re-pairs by PUT', async () => {
    withDrafts()
    const kept = `${MODEL_DIR}/Qwen-LiLiCorr-Q8.gguf`
    const { host, root } = mount(
      React.createElement(ModelDrawer, {
        open: true,
        onClose: () => {},
        model: { ...DRAFT_MODEL, draft: kept, draftVariant: 'lilicorr' },
      }),
    )
    openSource(host)
    await act(async () => {
      q<HTMLButtonElement>(host, 'model-draft-unpair').click()
      await Promise.resolve()
      await Promise.resolve()
    })
    updateCalls.length = 0
    act(() => q<HTMLButtonElement>(host, 'model-draft-select').click())
    const row = host.querySelector(`[data-option-id="${kept}"]`) as HTMLElement
    expect(row.textContent).toContain('on disk, not paired')
    act(() => row.click())
    expect(q(host, 'model-draft-pull')).toBeNull()
    await save(host)
    expect(updateCalls).toEqual([{ id: 'm1', body: { draft: kept } }])
    act(() => root.unmount())
  })

  it('pull → adopted → Unpair → re-pair by PUT → a later pull (new job) is adopted too', async () => {
    inspectHandler.current = () => ({
      repo: 'org/qwen-gguf',
      variants: [],
      mmproj: [],
      drafts: [
        DRAFTS[0],
        { ...DRAFTS[0], id: 'Qwen-Other-Q8.gguf', variant_hint: 'dflash' },
      ],
    })
    const rerender = () =>
      act(() => {
        root.render(
          React.createElement(
            QueryClientProvider,
            { client: new QueryClient() },
            React.createElement(ModelDrawer, { open: true, onClose: () => {}, model: DRAFT_MODEL }),
          ),
        )
      })
    const { host, root } = mount(
      React.createElement(ModelDrawer, { open: true, onClose: () => {}, model: DRAFT_MODEL }),
    )
    openSource(host)
    pickRepoDraft(host) // X
    await act(async () => {
      q<HTMLButtonElement>(host, 'model-draft-pull').click()
      await Promise.resolve()
    })
    pullBox.current = { state: 'completed', modelId: 'm1', jobId: 'j1' }
    rerender()
    expect(q(host, 'model-draft-pull')).toBeNull()
    await act(async () => {
      q<HTMLButtonElement>(host, 'model-draft-unpair').click()
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(q(host, 'model-draft-unpair')).toBeNull()
    // X is on disk now: picking it again is a plain re-pair, no Pull.
    pickRepoDraft(host)
    expect(q(host, 'model-draft-pull')).toBeNull()
    // A different drafter pulled under a NEW job id is adopted as well.
    pullBox.current = { state: 'idle', modelId: null, jobId: null }
    act(() => q<HTMLButtonElement>(host, 'model-draft-select').click())
    act(() =>
      (host.querySelector(`[data-option-id="${MODEL_DIR}/Qwen-Other-Q8.gguf"]`) as HTMLElement).click(),
    )
    await act(async () => {
      q<HTMLButtonElement>(host, 'model-draft-pull').click()
      await Promise.resolve()
    })
    pullBox.current = { state: 'completed', modelId: 'm1', jobId: 'j2' }
    rerender()
    expect(q(host, 'model-draft-pull')).toBeNull()
    expect(q(host, 'model-draft-unpair')).toBeTruthy()
    act(() => root.unmount())
  })

  it('a pending repo pick disables Save with a hint, even beside other edits', async () => {
    withDrafts()
    const { host, root } = mount(
      React.createElement(ModelDrawer, { open: true, onClose: () => {}, model: DRAFT_MODEL }),
    )
    openSource(host)
    pickRepoDraft(host)
    expect(q<HTMLElement>(host, 'model-draft-pending').textContent).toContain(
      'pull the drafter first (or set it back)',
    )
    expect(q<HTMLButtonElement>(host, 'model-save').disabled).toBe(true)
    act(() => typeInto(q<HTMLInputElement>(host, 'model-hffile-input'), 'other.gguf'))
    expect(q<HTMLButtonElement>(host, 'model-save').disabled).toBe(true)
    // Setting the pick back clears the block; the other edit can now save.
    act(() => q<HTMLButtonElement>(host, 'model-draft-select').click())
    act(() => (host.querySelector('[data-option-id=""]') as HTMLElement).click())
    expect(q(host, 'model-draft-pending')).toBeNull()
    expect(q<HTMLButtonElement>(host, 'model-save').disabled).toBe(false)
    act(() => root.unmount())
  })

  it('the facts band shows the drafter variant', () => {
    const { host, root } = mount(
      React.createElement(ModelDrawer, {
        open: true,
        onClose: () => {},
        model: { ...DRAFT_MODEL, draft: `${MODEL_DIR}/d.gguf`, draftVariant: 'lilicorr' },
      }),
    )
    expect(q<HTMLElement>(host, 'model-facts').textContent).toContain('LiLiCorr')
    act(() => root.unmount())
  })
})
