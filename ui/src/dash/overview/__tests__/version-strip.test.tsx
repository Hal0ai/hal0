// @vitest-environment happy-dom
//
// Overview v2 version strip: hal0 version · channel, FLM, auto-check, up-to-date / available, and
// the host summary on the right. A missing update state renders the topbar's ellipsis.
import React, { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

;(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true
;(globalThis as any).React = React

const m = vi.hoisted(() => ({
  updates: undefined as any,
  hw: undefined as any,
  poolGb: 0,
}))

vi.mock('@/api/hooks/useUpdates', () => ({ useUpdateState: () => ({ data: m.updates }) }))
vi.mock('@/api/hooks/useHardware', () => ({ useHardware: () => ({ data: m.hw }) }))
vi.mock('../../memory-map', () => ({ useMemoryMapModel: () => ({ pool: { totalGb: m.poolGb } }) }))

await import('../version-strip.jsx')
const OvVersionStrip = (window as any).OvVersionStrip as React.ComponentType

let host: HTMLDivElement
let root: Root

beforeEach(() => {
  m.updates = {
    hal0: { current: '1.7.1', available: null, channel: 'stable' },
    flm: { current: '0.9.12' },
    autoCheck: true,
  }
  m.hw = { name: 'hal0', cores: '12c · 24t', memoryKind: 'unified' }
  m.poolGb = 116
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})
afterEach(() => {
  act(() => root.unmount())
  host.remove()
})
const mount = () => act(() => root.render(React.createElement(OvVersionStrip)))
const text = () => host.querySelector('.ov-verstrip')!.textContent!

describe('OvVersionStrip', () => {
  it('is a plain div.ov-verstrip, not a card', () => {
    mount()
    const el = host.querySelector('.ov-verstrip')!
    expect(el.tagName).toBe('DIV')
    expect(host.querySelector('.ov-card')).toBeNull()
  })

  it('up to date: version, channel, FLM, auto-check on, host with uma', () => {
    mount()
    expect(text()).toBe('hal0 1.7.1 · stableFLM 0.9.12auto-check onup to datehost hal0 · 12 cores · 116 GB uma')
    expect(host.querySelector('.ov-verstrip b')!.textContent).toBe('1.7.1')
  })

  it('update available: shows the version in the accent instead of up to date', () => {
    m.updates.hal0.available = '1.7.2'
    mount()
    expect(text()).toContain('1.7.2 available')
    expect(text()).not.toContain('up to date')
    const b = [...host.querySelectorAll('b')].find((x) => x.textContent === '1.7.2 available')!
    expect(b.getAttribute('style')).toContain('var(--accent)')
  })

  it('auto-check off', () => {
    m.updates.autoCheck = false
    mount()
    expect(text()).toContain('auto-check off')
  })

  it('missing update state renders the ellipsis for the version, never up to date', () => {
    m.updates = undefined
    mount()
    expect(text()).toContain('hal0 …')
    expect(text()).not.toContain('up to date')
    expect(text()).not.toContain('available')
    expect(text()).toContain('FLM —')
    expect(text()).not.toContain('auto-check')
  })

  it('unknown availability (field omitted) claims neither up to date nor available', () => {
    delete m.updates.hal0.available
    mount()
    expect(text()).not.toContain('up to date')
    expect(text()).not.toContain('available')
  })

  it('omitted autoCheck renders no auto-check segment', () => {
    delete m.updates.autoCheck
    mount()
    expect(text()).not.toContain('auto-check')
  })

  it('FLM version absent renders a dash', () => {
    m.updates.flm = undefined
    mount()
    expect(text()).toContain('FLM —')
  })

  it('omits the uma word on a discrete-memory host', () => {
    m.hw.memoryKind = 'system'
    m.poolGb = 62
    mount()
    expect(text()).toContain('host hal0 · 12 cores · 62 GB')
    expect(text()).not.toContain('uma')
  })

  it('renders an em dash for host figures the probes cannot source', () => {
    m.hw = { name: '', cores: '', memoryKind: 'unified' }
    m.poolGb = 0
    mount()
    expect(text()).toContain('host — · — cores · — GB')
    expect(text()).not.toContain('0 GB')
  })

  it('hides the host summary while hardware is still loading', () => {
    m.hw = undefined
    mount()
    expect(text()).not.toContain('host')
  })
})
