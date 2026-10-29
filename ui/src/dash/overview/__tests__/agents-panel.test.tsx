// @vitest-environment happy-dom
//
// OvAgents: Hermes row, Pi row, delegated runs, and the footer links.
import React from 'react'
import { createRoot } from 'react-dom/client'
import { act } from 'react-dom/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { OV_HERMES, OV_PI, RUN } from './agents-fixtures'

;(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true
;(globalThis as any).React = React

let overviews: Record<string, any> = {}
let runs: any[] | undefined = []
let clients: any[] | undefined = []
let boardStats: any = { total: 7, by_status: { in_progress: 2, done: 5 } }
let slots: any[] = []
let runsState: { isError?: boolean; isPending?: boolean } = {}

vi.mock('@/api/hooks/useAgents', () => ({
  useAgents: () => ({ data: { agents: [] } }),
  useAgentOverview: (name: string) => ({ data: overviews[name] ?? null }),
}))
vi.mock('@/api/hooks/useSlots', () => ({ useSlots: () => ({ data: slots }) }))
vi.mock('@/api/hooks/useBridge', () => ({
  useBridgeRuns: () => ({ data: runs ? { items: runs } : undefined, isError: false, isPending: false, ...runsState }),
  useBridgeClients: () => ({ data: clients ? { items: clients } : undefined }),
}))
vi.mock('@/api/hooks/useBoard', () => ({ useBoardStats: () => ({ data: boardStats }) }))

await import('../shell.jsx')
await import('../agents-panel.jsx')
const OvAgents = (window as any).OvAgents as React.ComponentType<{ onGo: (id: string) => void }>

let host: HTMLDivElement
let root: ReturnType<typeof createRoot>

function mount(onGo: (id: string) => void = () => {}) {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  act(() => root.render(<OvAgents onGo={onGo} />))
}

const NOW = Math.floor(Date.now() / 1000)

beforeEach(() => {
  overviews = { hermes: OV_HERMES, pi: OV_PI }
  runs = []
  clients = [{ name: 'claude-code' }]
  boardStats = { total: 7, by_status: { in_progress: 2, done: 5 } }
  slots = []
  runsState = {}
  ;(window as any).__hal0UseAgentPersonas = () => ({ data: { active: 'Hermes', personas: [] } })
  ;(window as any).__hal0PrimarySlot = (s: any[]) => s[0] ?? null
  ;(window as any).__hal0DeriveCardModel = undefined
})

afterEach(() => {
  act(() => root.unmount())
  document.body.innerHTML = ''
})

describe('OvAgents', () => {
  it('shows the Hermes row: pill, version, profile dots and the serving caption', () => {
    ;(window as any).__hal0DeriveCardModel = () => ({ phase: 'serving' })
    mount()
    const rows = host.querySelectorAll('.ov-agent')
    expect(rows).toHaveLength(2)
    const hermes = rows[0]
    expect(hermes.querySelector('.ov-pill')?.textContent).toBe('serving')
    expect(hermes.querySelector('.ov-sub')?.textContent).toContain('v0.22.4')
    expect(hermes.querySelectorAll('.ov-profiles .ov-p')).toHaveLength(5)
    expect(hermes.querySelector('.ov-profiles')?.textContent).toContain('5 profiles serving')
    expect(hermes.textContent).toContain('persona Hermes')
    expect(hermes.textContent).toContain('approvals smart')
    expect([...hermes.querySelectorAll('.ov-chip')].map((c) => c.textContent)).toEqual(['telegram', 'webhook', 'api'])
  })

  it('fills the ctx bar from the primary slot', () => {
    slots = [{ metrics: { ctx: 33000 }, ctx_effective: 66000 }]
    mount()
    const row = host.querySelector('.ov-ctxrow')!
    expect(row.querySelector('.ov-track i')?.getAttribute('style')).toContain('width: 50%')
    expect(row.textContent).toContain('33k/66k')
  })

  it('shows ctx as a dash without a primary slot', () => {
    mount()
    expect(host.querySelector('.ov-ctxrow')?.textContent).toContain('ctx —')
  })

  it('shows the Pi row as installed', () => {
    mount()
    const pi = host.querySelectorAll('.ov-agent')[1]
    expect(pi.querySelector('.ov-avatar.pi')).not.toBeNull()
    expect(pi.querySelector('.ov-pill')?.textContent).toBe('installed')
    expect(pi.textContent).toContain('v0.9.1')
  })

  it('says a down Hermes is down and drops the serving caption when no profile is served', () => {
    overviews.hermes = { ...OV_HERMES, units: { agent: 'inactive' }, api_server: { profiles: [] } }
    mount()
    const hermes = host.querySelectorAll('.ov-agent')[0]
    expect(hermes.querySelector('.ov-pill.err')?.textContent).toBe('down')
    expect(hermes.querySelector('.ov-profiles')).toBeNull()
  })

  it('names the empty runs line with the paired client count', () => {
    mount()
    expect(host.querySelectorAll('.ov-run')).toHaveLength(0)
    expect(host.querySelector('.ov-empty')?.textContent).toBe('No delegated runs in the last 24 h · 1 bridge client paired')
  })

  it('does not claim an empty list when the runs read failed', () => {
    runs = undefined
    runsState = { isError: true }
    mount()
    expect(host.querySelector('.ov-empty')?.textContent).toBe('delegated runs unavailable')
    expect(host.textContent).not.toContain('No delegated runs')
    expect(host.textContent).toContain('— running')
  })

  it('shows a dash while the runs read is pending', () => {
    runs = undefined
    runsState = { isPending: true }
    mount()
    expect(host.querySelector('.ov-empty')?.textContent).toBe('—')
    expect(host.textContent).toContain('— running')
  })

  it('pluralises clients and drops the count when clients are unread', () => {
    clients = [{ name: 'a' }, { name: 'b' }]
    mount()
    expect(host.querySelector('.ov-empty')?.textContent).toContain('2 bridge clients paired')
    act(() => root.unmount())
    document.body.innerHTML = ''
    clients = undefined
    mount()
    expect(host.querySelector('.ov-empty')?.textContent).toBe('No delegated runs in the last 24 h')
  })

  it('renders one .ov-run per run, with the running header and board count', () => {
    runs = [
      { ...RUN, run_id: 'a', status: 'running', created_at: NOW - 90 },
      { ...RUN, run_id: 'b', status: 'completed', updated_at: NOW - 7300 },
    ]
    mount()
    const rows = host.querySelectorAll('.ov-run')
    expect(rows).toHaveLength(2)
    expect(rows[0].classList.contains('running')).toBe(true)
    expect(rows[0].querySelector('.ov-what')?.textContent).toBe('Fix the flaky test')
    expect(rows[0].querySelector('.ov-who')?.textContent).toBe('claude-code → hermes/coder')
    expect(rows[0].querySelector('.ov-t')?.textContent).toMatch(/^running · 1m 3\ds$/)
    expect(rows[1].querySelector('.ov-t')?.textContent).toBe('done · 2 h ago')
    expect(host.textContent).toContain('1 running')
    expect(host.textContent).toContain('board · 2 in progress')
    expect(host.querySelector('.ov-empty')).toBeNull()
  })

  it('shows the board count as a dash while stats are unread', () => {
    boardStats = undefined
    mount()
    expect(host.textContent).toContain('board · —')
  })

  it('Open Agent Chat dispatches the topbar event and Kanban goes to the board', () => {
    const onGo = vi.fn()
    const seen = vi.fn()
    window.addEventListener('hal0:toggle-agent-chat', seen)
    mount(onGo)
    const byText = (t: string) =>
      [...host.querySelectorAll('button')].find((b) => b.textContent?.includes(t)) as HTMLButtonElement
    act(() => byText('Open Agent Chat').click())
    expect(seen).toHaveBeenCalledTimes(1)
    act(() => byText('Kanban').click())
    expect(onGo).toHaveBeenCalledWith('board')
    act(() => byText('Agents →').click())
    expect(onGo).toHaveBeenCalledWith('agents')
    window.removeEventListener('hal0:toggle-agent-chat', seen)
  })
})
