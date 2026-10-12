// @vitest-environment happy-dom
//
// Stack editor (ADR-0029). A stack row is placement + which model + which
// profile: slot, model, device, provider, profile and capabilities. The server's
// StackSlotEntry forbids any other key, so the editor must never send mtp,
// vision, enable_thinking, server_extra_args or binary — and must carry a
// stored row's provider through an edit instead of dropping it.
import React from 'react'
import * as ReactDOMClient from 'react-dom/client'
import { createPortal } from 'react-dom'
import { createRoot } from 'react-dom/client'
import { act } from 'react-dom/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

;(globalThis as unknown as { window: typeof globalThis }).window = globalThis
;(globalThis as unknown as { React: typeof React }).React = React
;(globalThis as unknown as { ReactDOM: unknown }).ReactDOM = { ...ReactDOMClient, createPortal }
;(globalThis as unknown as { Icons: unknown }).Icons = new Proxy({}, { get: () => null })
;(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const { updateCalls, createCalls, profilesBox, slotsBox } = vi.hoisted(() => ({
  updateCalls: [] as any[],
  createCalls: [] as any[],
  profilesBox: { current: [] as Record<string, unknown>[] },
  slotsBox: { current: [] as Record<string, unknown>[] },
}))

vi.mock('@/api/hooks/useStacks', () => ({
  useStacks: () => ({ data: { stacks: [] } }),
  useStackCreate: () => ({ mutateAsync: async (b: unknown) => void createCalls.push(b) }),
  useStackUpdate: () => ({ mutateAsync: async (b: unknown) => void updateCalls.push(b) }),
  useStackDelete: () => ({ mutateAsync: async () => undefined }),
  useStackApply: () => ({ mutateAsync: async () => ({}) }),
  useStackExport: () => ({ mutateAsync: async () => ({}) }),
  useStackImport: () => ({ mutateAsync: async () => ({}) }),
  useStackSnapshot: () => ({ mutateAsync: async () => ({}) }),
}))
vi.mock('@/api/hooks/useModels', () => ({ useModels: () => ({ data: [{ id: 'm1' }] }) }))
vi.mock('@/api/hooks/useProfiles', () => ({ useProfiles: () => ({ data: profilesBox.current }) }))
vi.mock('@/api/hooks/useSlots', () => ({ useSlots: () => ({ data: slotsBox.current }) }))
vi.mock('@/api/hooks/useRuntimes', () => ({
  useSystemInfo: () => ({
    data: { backends: { standard: { device_class: 'gpu' }, kokoro: { device_class: 'cpu' } } },
  }),
}))
vi.mock('@/api/hooks/useMeta', async () => {
  const actual = await vi.importActual<typeof import('@/api/hooks/useMeta')>('@/api/hooks/useMeta')
  return { ...actual, useMetaEnums: () => actual.resolveMetaEnums(undefined) }
})

await import('../primitives.jsx')
const { StackDrawer } = await import('../stacks.jsx')

function mount(props: Record<string, unknown>) {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const root = createRoot(host)
  act(() => root.render(React.createElement(StackDrawer as unknown as React.ComponentType<Record<string, unknown>>, { onClose: () => {}, onSaved: () => {}, ...props })))
  return { host, root }
}

const STORED = {
  slug: 'daily',
  name: 'Daily',
  description: '',
  icon: '',
  tags: [],
  slots: [
    {
      slot: 'primary', model: 'm1', device: 'gpu-rocm', provider: 'llama-server', profile: 'chat',
      capabilities: [],
      // Pre-ADR-0029 keys a stored row might still hand back.
      mtp: true, vision: false, enable_thinking: true, server_extra_args: '-fa on', binary: 'standard',
    },
  ],
}

beforeEach(() => {
  updateCalls.length = 0
  createCalls.length = 0
  profilesBox.current = [
    { name: 'chat', supported_slot_types: ['llm'] },
    { name: 'voice', supported_slot_types: ['tts'] },
    { name: 'cpu-pinned', supported_slot_types: ['llm'], runner: 'kokoro' },
  ]
  slotsBox.current = [{ name: 'primary', type: 'llm' }]
})
afterEach(() => {
  document.body.innerHTML = ''
})

describe('StackDrawer row schema (ADR-0029)', () => {
  it('renders no MTP, binary or tuning control', () => {
    const { host, root } = mount({ mode: 'edit', source: STORED })
    expect(host.querySelector('[data-testid="st-slot-mtp-0"]')).toBeNull()
    expect(host.querySelector('[data-testid="st-slot-binary-0"]')).toBeNull()
    act(() => root.unmount())
  })

  it('saves rows with exactly the StackSlotEntry keys — stale keys are dropped, provider is kept', async () => {
    const { host, root } = mount({ mode: 'edit', source: STORED })
    await act(async () => {
      host.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
      await Promise.resolve()
    })
    expect(updateCalls).toHaveLength(1)
    const row = updateCalls[0].stack.slots[0]
    expect(Object.keys(row).sort()).toEqual(['capabilities', 'device', 'model', 'profile', 'provider', 'slot'])
    expect(row.provider).toBe('llama-server')
    act(() => root.unmount())
  })

  it('offers only profiles that fit the row: slot type and pinned-runner device class', () => {
    const { host, root } = mount({ mode: 'edit', source: STORED })
    const opts = Array.from(host.querySelectorAll('[data-testid="st-slot-profile-0"] option')).map(
      (o) => (o as HTMLOptionElement).value,
    )
    // 'voice' supports tts only; 'cpu-pinned' pins a cpu runner on a gpu device.
    expect(opts).toEqual(['', 'chat'])
    act(() => root.unmount())
  })
})
