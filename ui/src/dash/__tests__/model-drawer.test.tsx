// @vitest-environment happy-dom
//
// Model drawer header (model-drawer-2, Task 3): inline title editor, the
// single default-toggle chip, and the read-only facts band.
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

const { updateCalls, setDefaultCalls, setDefaultResult, slotsBox } = vi.hoisted(() => ({
  updateCalls: [] as unknown[],
  setDefaultCalls: [] as unknown[],
  setDefaultResult: { current: { default: true, type: 'llm' } as Record<string, unknown> },
  slotsBox: { current: [] as Record<string, unknown>[] },
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
  useProfiles: () => ({ data: [], isLoading: false }),
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

beforeEach(() => {
  updateCalls.length = 0
  setDefaultCalls.length = 0
  setDefaultResult.current = { default: true, type: 'llm' }
  slotsBox.current = []
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
