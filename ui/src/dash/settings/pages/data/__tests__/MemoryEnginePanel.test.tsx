// @vitest-environment happy-dom
// The engine panel of Settings > Memory: the engine choices match what the schema accepts and
// is built on, the Memory switch (memory.enabled) says what it does, and a save sends only what
// changed.
import React, { act } from 'react'
import ReactDOM from 'react-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { cleanup, click, mount } from '../../../../memory-area/shared/test-harness'

;(globalThis as any).React = React
;(globalThis as any).ReactDOM = ReactDOM
;(globalThis as any).Icons = new Proxy({}, { get: () => null })

const state: { live: any; sent: any[] } = { live: null, sent: [] }

vi.mock('@/api/hooks/useMemory', () => ({
  useMemoryGraphStatus: () => ({ data: null }),
  useRetryFailedExtractions: () => ({ isPending: false, mutateAsync: async () => ({}) }),
  useUpdateMemoryGraph: () => ({ isPending: false, mutateAsync: async () => ({}) }),
}))

vi.mock('../../../data/settingsClient.js', () => ({
  useSettingsClient: () => ({
    settings: { data: state.live, isError: false },
    schema: { data: null },
    registry: {},
    update: {
      isPending: false,
      mutateAsync: async (body: unknown) => {
        state.sent.push(body)
        return {}
      },
    },
  }),
}))

const { MemoryEnginePanel } = await import('../MemoryPage.jsx')
// primitives.jsx installs a portal FieldInfoIcon on import; the stub goes in after it.
;(globalThis as any).FieldInfoIcon = ({ description }: any) => <span data-sub="">{description}</span>
const render = () => mount(<MemoryEnginePanel registry={{}} />).host
const q = (host: HTMLElement, id: string) => host.querySelector(`[data-testid="${id}"]`) as HTMLInputElement | null
const save = (host: HTMLElement) =>
  [...host.querySelectorAll('button')].find((b) => b.textContent === 'Save engine') as HTMLButtonElement

beforeEach(() => {
  state.live = { memory: { engine: 'hindsight', enabled: true } }
  state.sent = []
  ;(window as any).__hal0Toast = vi.fn()
})
afterEach(cleanup)

describe('MemoryEnginePanel', () => {
  it('offers the engines hal0 runs, and says mem0 is accepted but not implemented', () => {
    const host = render()
    const select = host.querySelector('select') as HTMLSelectElement
    expect([...select.options].map((o) => o.value)).toEqual(['hindsight', 'pgvector'])
    expect(host.textContent).toContain('mem0 is accepted in hal0.toml but not implemented')
  })

  it('has a Memory switch that says what turning memory off does', () => {
    const host = render()
    expect(q(host, 'memory-enabled')!.checked).toBe(true)
    expect(host.textContent).toContain('/mcp/memory is not mounted')
    expect(host.textContent).toContain('/api/memory/* answers 503')
    expect(host.textContent).toContain('The engine service keeps running and nothing stored is deleted')
    expect(host.textContent).toContain('Applies when hal0-api restarts')
  })

  const dialog = () => document.querySelector('[data-testid="memory-off-confirm"]')
  const button = (label: string) =>
    [...document.querySelectorAll('button')].find((b) => b.textContent === label) as HTMLButtonElement

  it('asks before turning memory off, and says what follows', async () => {
    const host = render()
    click(q(host, 'memory-enabled')!)
    click(save(host))
    expect(state.sent).toEqual([])
    expect(dialog()).not.toBeNull()
    const text = dialog()!.textContent!
    expect(text).toContain('memory.enabled = false')
    expect(text).toContain('/mcp/memory is not mounted')
    expect(text).toContain('/api/memory/* answers 503')
    expect(text).toContain('The engine service keeps running, and nothing already stored is deleted')
    expect(text).toContain('restart ends any request in flight')
    click(button('Turn memory off'))
    await Promise.resolve()
    await Promise.resolve()
    expect(state.sent).toEqual([{ memory: { enabled: false } }])
    // Not the engine message.
    expect((window as any).__hal0Toast).toHaveBeenCalledWith(expect.stringContaining('Memory will turn off'), 'warn')
    expect((window as any).__hal0Toast).not.toHaveBeenCalledWith(expect.stringContaining('Memory engine saved'), 'warn')
  })

  it('cancelling leaves memory on and sends nothing', () => {
    const host = render()
    click(q(host, 'memory-enabled')!)
    click(save(host))
    click(button('Cancel'))
    expect(dialog()).toBeNull()
    expect(state.sent).toEqual([])
  })

  it('an engine change alone needs no confirmation and keeps the engine message', async () => {
    const host = render()
    const select = host.querySelector('select') as HTMLSelectElement
    const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')!.set!
    act(() => {
      setter.call(select, 'pgvector')
      select.dispatchEvent(new Event('change', { bubbles: true }))
    })
    click(save(host))
    await Promise.resolve()
    expect(dialog()).toBeNull()
    expect(state.sent).toEqual([{ memory: { engine: 'pgvector' } }])
    expect((window as any).__hal0Toast).toHaveBeenCalledWith(expect.stringContaining('Memory engine saved'), 'warn')
  })

  it('shows the switch off when memory is off, and turning it on needs no confirmation', async () => {
    state.live = { memory: { engine: 'hindsight', enabled: false } }
    const host = render()
    expect(q(host, 'memory-enabled')!.checked).toBe(false)
    click(q(host, 'memory-enabled')!)
    click(save(host))
    await Promise.resolve()
    expect(dialog()).toBeNull()
    expect(state.sent).toEqual([{ memory: { enabled: true } }])
    expect((window as any).__hal0Toast).toHaveBeenCalledWith(expect.stringContaining('Memory will turn on'), 'warn')
  })
})
