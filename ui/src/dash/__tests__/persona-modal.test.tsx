// @vitest-environment happy-dom
//
// PersonaEditModal. The modal edits a Hermes persona through
// PUT /api/agents/hermes/personas/{pid}, which accepts display_name, summary,
// system_prompt, tools_allowed, memory_namespace, preferred_upstream,
// preferred_model and approval — and ignores everything else. So the modal
// loads the persona it edits (no hard-coded prompt seed), shows the four
// fields the operator can actually change, and posts exactly those four. There
// is no tone: the Hermes Persona dataclass has no such field.
//
// flow-modals.jsx is a window-globals dash module (bare `Modal` /
// `FieldInfoIcon` / `Icons` identifiers resolve off `window`), so the globals
// are installed before the dynamic import — same pattern as
// model-drawer.test.tsx. The hooks are real; only the api boundary
// (`apiGet` / `apiPut`) and the slot list are faked.
import React, { act } from 'react'
import * as ReactDOMClient from 'react-dom/client'
import { createPortal } from 'react-dom'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

;(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
;(globalThis as unknown as { window: typeof globalThis }).window = globalThis
;(globalThis as unknown as { React: typeof React }).React = React
;(globalThis as unknown as { ReactDOM: unknown }).ReactDOM = { ...ReactDOMClient, createPortal }
;(globalThis as unknown as { Icons: unknown }).Icons = new Proxy({}, { get: () => null })

const { apiGet, apiPut, slotsBox } = vi.hoisted(() => ({
  apiGet: vi.fn(),
  apiPut: vi.fn(),
  slotsBox: { current: [] as Record<string, unknown>[] },
}))

vi.mock('@/api/client', async () => {
  const actual = await vi.importActual<typeof import('@/api/client')>('@/api/client')
  return { ...actual, apiGet, apiPut }
})

vi.mock('@/api/hooks/useSlots', () => ({
  useSlots: () => ({ data: slotsBox.current, isLoading: false }),
}))

await import('../primitives.jsx')
await import('../flow-modals.jsx')
// flow-modals.jsx has no exports; it publishes its components on `window`.
const PersonaEditModal = (
  globalThis as unknown as {
    PersonaEditModal: React.ComponentType<{
      open: boolean
      onClose: () => void
      personaId?: string
    }>
  }
).PersonaEditModal

const PERSONA = {
  id: 'coder',
  display_name: 'Coder',
  summary: '',
  system_prompt: 'Be terse',
  tools_allowed: ['read_file'],
  memory_namespace: 'coder',
  preferred_upstream: '',
  preferred_model: 'agent',
  active: false,
}

// The enums route still ships `tones` (the backend catalogue predates this
// modal's rewrite); the modal must ignore them.
const ENUMS = {
  tones: [{ id: 'operator', label: 'operator', desc: 'terse + technical' }],
  tools: [
    { id: 'read_file', label: 'read_file', cap: 'fs-read' },
    { id: 'write_file', label: 'write_file', cap: 'fs-write' },
    { id: 'shell_exec', label: 'shell_exec', cap: 'shell-exec' },
  ],
}

const SLOTS = [
  { name: 'agent', type: 'llm', model: 'qwen', device: 'gpu' },
  { name: 'brain', type: 'llm', model: 'gemma', device: 'npu' },
  { name: 'embed', type: 'embedding', model: 'bge', device: 'cpu' },
]

let persona: Record<string, unknown>
let list: { agent_id: string; active: string | null; personas: unknown[] }

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

// Let react-query resolve the faked fetches and React commit the result.
async function settle() {
  for (let i = 0; i < 5; i++) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0))
    })
  }
}

function field(match: (el: HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement) => boolean) {
  return Array.from(
    document.querySelectorAll<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>(
      'input, textarea, select',
    ),
  ).find(match)
}

const byValue = (v: string) => field((el) => el.value === v)

// The checkbox inside the <label> whose text is exactly `name`.
function toolBox(name: string): HTMLInputElement {
  const label = Array.from(document.querySelectorAll('label')).find(
    (l) => l.textContent?.trim() === name,
  )
  const box = label?.querySelector<HTMLInputElement>('input[type="checkbox"]')
  if (!box) throw new Error(`no checkbox labelled ${name}`)
  return box
}

function saveButton(): HTMLButtonElement {
  const btn = Array.from(document.querySelectorAll('button')).find((b) => b.textContent === 'Save')
  if (!btn) throw new Error('no Save button')
  return btn
}

beforeEach(() => {
  persona = { ...PERSONA }
  list = { agent_id: 'hermes', active: 'coder', personas: [] }
  slotsBox.current = SLOTS
  apiGet.mockReset()
  apiPut.mockReset()
  apiGet.mockImplementation(async (path: string) => {
    if (path === '/api/agents/hermes/personas/coder') return persona
    if (path === '/api/agents/hermes/personas') return list
    if (path === '/api/agents/persona-enums') return ENUMS
    throw new Error(`unexpected GET ${path}`)
  })
  apiPut.mockImplementation(async () => persona)
})

afterEach(() => {
  document.body.innerHTML = ''
})

describe('PersonaEditModal', () => {
  it('loads the persona and saves only API fields', async () => {
    const { root } = mount(
      React.createElement(PersonaEditModal, { open: true, personaId: 'coder', onClose: () => {} }),
    )
    await settle()

    expect(byValue('Coder')).toBeTruthy()
    expect(byValue('Be terse')).toBeTruthy()
    // preferred_model 'agent' is one of the llm slots, so it is selected.
    const slot = field((el) => el.tagName === 'SELECT') as HTMLSelectElement
    expect(slot.value).toBe('agent')
    expect(Array.from(slot.options).map((o) => o.value)).toEqual(['agent', 'brain'])

    // No tone anywhere: not a label, not a control, not in the copy.
    expect(document.body.textContent).not.toMatch(/tone/i)
    expect(document.querySelector('[aria-label*="tone" i]')).toBeNull()
    expect(toolBox('read_file').checked).toBe(true)
    expect(toolBox('write_file').checked).toBe(false)

    act(() => toolBox('write_file').click())
    act(() => saveButton().click())
    await settle()

    expect(apiPut).toHaveBeenCalledTimes(1)
    expect(apiPut).toHaveBeenCalledWith('/api/agents/hermes/personas/coder', {
      display_name: 'Coder',
      system_prompt: 'Be terse',
      tools_allowed: ['read_file', 'write_file'],
      preferred_model: 'agent',
    })
    act(() => root.unmount())
  })

  it('falls back to the active persona when no personaId is passed', async () => {
    list = { agent_id: 'hermes', active: 'coder', personas: [] }
    const { root } = mount(React.createElement(PersonaEditModal, { open: true, onClose: () => {} }))
    await settle()

    expect(apiGet).toHaveBeenCalledWith('/api/agents/hermes/personas')
    expect(apiGet).toHaveBeenCalledWith('/api/agents/hermes/personas/coder')
    expect(byValue('Coder')).toBeTruthy()
    act(() => saveButton().click())
    await settle()
    expect(apiPut).toHaveBeenCalledWith(
      '/api/agents/hermes/personas/coder',
      expect.objectContaining({ display_name: 'Coder' }),
    )
    act(() => root.unmount())
  })

  it('does not load anything while closed', async () => {
    const { root } = mount(
      React.createElement(PersonaEditModal, { open: false, personaId: 'coder', onClose: () => {} }),
    )
    await settle()
    expect(apiGet).not.toHaveBeenCalled()
    expect(document.querySelector('[role="dialog"]')).toBeNull()
    act(() => root.unmount())
  })

  it('a wildcard persona shows every tool ticked and posts ["*"] unchanged', async () => {
    persona = { ...PERSONA, tools_allowed: ['*'] }
    const { root } = mount(
      React.createElement(PersonaEditModal, { open: true, personaId: 'coder', onClose: () => {} }),
    )
    await settle()

    for (const id of ['read_file', 'write_file', 'shell_exec']) {
      expect(toolBox(id).checked).toBe(true)
    }
    act(() => saveButton().click())
    await settle()
    expect(apiPut).toHaveBeenLastCalledWith(
      '/api/agents/hermes/personas/coder',
      expect.objectContaining({ tools_allowed: ['*'] }),
    )
    act(() => root.unmount())
  })

  it('unticking a tool on a wildcard persona posts the explicit remainder', async () => {
    persona = { ...PERSONA, tools_allowed: ['*'] }
    const { root } = mount(
      React.createElement(PersonaEditModal, { open: true, personaId: 'coder', onClose: () => {} }),
    )
    await settle()

    act(() => toolBox('shell_exec').click())
    expect(toolBox('shell_exec').checked).toBe(false)
    expect(toolBox('read_file').checked).toBe(true)
    act(() => saveButton().click())
    await settle()
    expect(apiPut).toHaveBeenLastCalledWith(
      '/api/agents/hermes/personas/coder',
      expect.objectContaining({ tools_allowed: ['read_file', 'write_file'] }),
    )
    act(() => root.unmount())
  })

  it('keeps a preferred_model that is not an llm slot selectable', async () => {
    persona = { ...PERSONA, preferred_model: 'retired-slot' }
    const { root } = mount(
      React.createElement(PersonaEditModal, { open: true, personaId: 'coder', onClose: () => {} }),
    )
    await settle()

    const slot = field((el) => el.tagName === 'SELECT') as HTMLSelectElement
    expect(slot.value).toBe('retired-slot')
    expect(Array.from(slot.options).map((o) => o.value)).toEqual(['agent', 'brain', 'retired-slot'])
    act(() => saveButton().click())
    await settle()
    expect(apiPut).toHaveBeenLastCalledWith(
      '/api/agents/hermes/personas/coder',
      expect.objectContaining({ preferred_model: 'retired-slot' }),
    )
    act(() => root.unmount())
  })
})
