// @vitest-environment happy-dom
//
// Overview v2 Memory card: gated off renders nothing; gated on shows the Hindsight pill, bank
// count, the chosen bank's three figures and the Open memory link.
import React, { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

;(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true
;(globalThis as any).React = React

const m = vi.hoisted(() => ({
  enabled: true,
  engine: { reachable: true, banks_total: 20 } as any,
  banks: ['hal0-mono', 'other'],
  stats: undefined as any,
  statsFor: [] as unknown[],
  engineCalls: 0,
  banksCalls: 0,
}))

vi.mock('@/api/hooks/useMemory', () => ({ useMemoryEnabled: () => m.enabled }))
vi.mock('@/api/hooks/useHindsight', () => ({
  useMemoryEngine: () => {
    m.engineCalls++
    return { data: m.engine }
  },
  useMemoryBanks: () => {
    m.banksCalls++
    return { data: { banks: m.banks.map((bank_id) => ({ bank_id })) } }
  },
  useBankStats: (bank: string | null) => {
    m.statsFor.push(bank)
    return { data: m.stats }
  },
  useAggregateBankStats: () => ({ isLoading: false, totalFacts: 0 }),
}))

await import('../shell.jsx')
await import('../memory.jsx')
const OvMemory = (window as any).OvMemory as React.ComponentType<{ onGo?: (id: string) => void }>

let host: HTMLDivElement
let root: Root
let went: string[]

beforeEach(() => {
  m.enabled = true
  m.engine = { reachable: true, banks_total: 20 }
  m.banks = ['hal0-mono', 'other']
  m.stats = {
    bank_id: 'hal0-mono',
    total_nodes: 1284,
    total_links: 5120,
    total_observations: 311,
    pending_operations: 2,
    last_consolidated_at: new Date(Date.now() - 5 * 60_000).toISOString(),
  }
  m.statsFor = []
  m.engineCalls = 0
  m.banksCalls = 0
  went = []
  try {
    localStorage.clear()
  } catch {}
  ;(window as any).happyDOM.setURL('http://localhost:9120/#overview')
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})
afterEach(() => {
  act(() => root.unmount())
  host.remove()
})
const mount = () =>
  act(() => {
    root.render(React.createElement(OvMemory, { onGo: (id: string) => went.push(id) }))
  })

describe('OvMemory', () => {
  it('renders nothing when memory is gated off', () => {
    m.enabled = false
    mount()
    expect(host.innerHTML).toBe('')
    // A gated-off box never polls /api/memory/* from the Overview.
    expect(m.engineCalls).toBe(0)
    expect(m.banksCalls).toBe(0)
    expect(m.statsFor).toEqual([])
  })

  it('shows the Hindsight pill, bank count, bank and three figures', () => {
    mount()
    const t = host.textContent!
    expect(host.querySelector('.ov-pill')!.textContent).toBe('Hindsight')
    expect(t).toContain('20 banks')
    expect(t).toContain('hal0-mono')
    expect(host.querySelector('.ov-kv')!.textContent).toBe('1,284nodes5,120links311observations')
    expect(t).toContain('2 operations pending')
    expect(t).toContain('consolidated 5 min ago')
  })

  it('pluralises the pending operations note', () => {
    m.stats = { ...m.stats, pending_operations: 1 }
    mount()
    expect(host.textContent).toContain('1 operation pending')
    expect(host.textContent).not.toContain('1 operations pending')
  })

  it('uses the stored bank over the first one', () => {
    localStorage.setItem('hal0.mem.bank', 'other')
    mount()
    expect(m.statsFor).toContain('other')
    expect(host.textContent).toContain('bank other')
  })

  it('marks the engine down instead of the Hindsight ok pill', () => {
    m.engine = { reachable: false, banks_total: null }
    mount()
    expect(host.querySelector('.ov-pill')!.className).toContain('err')
    expect(host.textContent).not.toContain('null banks')
  })

  it('Open memory calls onGo("memory")', () => {
    mount()
    const btn = [...host.querySelectorAll('button')].find((b) => /Open memory/.test(b.textContent || ''))!
    act(() => {
      btn.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })
    expect(went).toEqual(['memory'])
  })
})
