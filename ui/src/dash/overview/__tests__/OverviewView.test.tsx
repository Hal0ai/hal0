// @vitest-environment happy-dom
//
// OverviewView: empty-install state, and section composition.
import React from 'react'
import { createRoot } from 'react-dom/client'
import { act } from 'react-dom/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

;(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true
;(globalThis as any).React = React

let slotsData: unknown = []
vi.mock('@/api/hooks/useSlots', () => ({ useSlots: () => ({ data: slotsData }) }))
vi.mock('@/api/hooks/useHardware', () => ({
  useHardware: () => ({ data: { name: 'box', ram: { total: 64 }, npu: { present: true } } }),
}))

const SECTIONS = [
  'OvNowBand', 'OvNeedsYou', 'OvServingNow', 'OvAgents', 'OvUsage', 'OvHardware',
  'OvRecent', 'OvImageEngine', 'OvServices', 'OvMemory', 'OvVersionStrip',
]
const FILES: Record<string, string> = {
  OvNowBand: 'now-band', OvNeedsYou: 'needs-you', OvServingNow: 'serving-now', OvAgents: 'agents-panel',
  OvUsage: 'usage', OvHardware: 'hardware', OvRecent: 'recent', OvImageEngine: 'image-engine',
  OvServices: 'services', OvMemory: 'memory', OvVersionStrip: 'version-strip',
}

// Each section module is replaced by a stub that records the onGo it was given.
const seen = vi.hoisted(() => ({} as Record<string, unknown>))
for (const n of SECTIONS) {
  vi.doMock(`../${FILES[n]}.jsx`, () => ({
    [n]: (p: { onGo: unknown }) => {
      seen[n] = p.onGo
      return <div data-sec={n} />
    },
  }))
}

await import('../OverviewView.jsx')
const OverviewView = (window as any).OverviewView as React.ComponentType<{ onGo: (id: string) => void }>

let host: HTMLDivElement
let root: ReturnType<typeof createRoot>

function mount(onGo: (id: string) => void = () => {}) {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  act(() => root.render(<OverviewView onGo={onGo} />))
}

afterEach(() => {
  act(() => root.unmount())
  document.body.innerHTML = ''
})

describe('OverviewView', () => {
  it('shows the empty-install state when no slots are configured', () => {
    slotsData = []
    const onGo = vi.fn()
    mount(onGo)
    expect(host.textContent).toContain('No models configured yet')
    expect(host.querySelector('.dash-empty')).not.toBeNull()
    expect(host.querySelector('.ov-view')).toBeNull()
    act(() => (host.querySelector('.dash-empty-cta button') as HTMLButtonElement).click())
    expect(onGo).toHaveBeenCalledWith('slots')
  })

  it('lays sections out in band order with the right column classes, passing onGo', () => {
    slotsData = [{ name: 'a' }]
    const onGo = vi.fn()
    mount(onGo)
    const order = Array.from(host.querySelectorAll('[data-sec]')).map((e) => e.getAttribute('data-sec'))
    expect(order).toEqual(SECTIONS.slice(0, 2).concat(
      'OvServingNow', 'OvAgents', 'OvUsage', 'OvHardware', 'OvRecent', 'OvImageEngine', 'OvServices', 'OvMemory', 'OvVersionStrip',
    ))
    const colOf = (n: string) => host.querySelector(`[data-sec="${n}"]`)!.parentElement!
    expect(colOf('OvServingNow').classList.contains('c8')).toBe(true)
    expect(colOf('OvAgents').classList.contains('c4')).toBe(true)
    expect(colOf('OvUsage').classList.contains('c7')).toBe(true)
    const stack = colOf('OvHardware')
    expect(stack.classList.contains('c5') && stack.classList.contains('ov-stack')).toBe(true)
    expect(colOf('OvRecent')).toBe(stack)
    for (const n of ['OvImageEngine', 'OvServices', 'OvMemory']) expect(colOf(n).classList.contains('c4')).toBe(true)
    expect(host.querySelectorAll('.ov-band').length).toBe(3)
    expect(host.querySelector('.view.ov-view')).not.toBeNull()
    for (const n of SECTIONS) expect(seen[n]).toBe(onGo)
  })
})
