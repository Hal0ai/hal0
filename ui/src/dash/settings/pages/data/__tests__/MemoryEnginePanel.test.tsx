// @vitest-environment happy-dom
// The engine panel of Settings > Memory: the engine choices match what the schema accepts and
// is built on, the Memory switch (memory.enabled) says what it does, and a save sends only what
// changed.
import React from 'react'
import ReactDOM from 'react-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { cleanup, click, mount } from '../../../../memory-area/shared/test-harness'

;(globalThis as any).React = React
;(globalThis as any).ReactDOM = ReactDOM

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
    expect(host.textContent).toContain('Off removes /mcp/memory')
    expect(host.textContent).toContain('nothing already stored is deleted')
    expect(host.textContent).toContain('Applies after a hal0-api restart')
  })

  it('saves only memory.enabled when only the switch changed', async () => {
    const host = render()
    expect(save(host).disabled).toBe(true)
    click(q(host, 'memory-enabled')!)
    expect(save(host).disabled).toBe(false)
    click(save(host))
    await Promise.resolve()
    expect(state.sent).toEqual([{ memory: { enabled: false } }])
  })

  it('shows the switch off when memory is off, and turning it on saves enabled: true', async () => {
    state.live = { memory: { engine: 'hindsight', enabled: false } }
    const host = render()
    expect(q(host, 'memory-enabled')!.checked).toBe(false)
    click(q(host, 'memory-enabled')!)
    click(save(host))
    await Promise.resolve()
    expect(state.sent).toEqual([{ memory: { enabled: true } }])
  })
})
