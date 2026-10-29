// @vitest-environment happy-dom
//
// The Hermes card's console gains a "Delegated" tab (the runs bridge clients handed to Hermes,
// bridge-runs.jsx); the Pi card's console keeps its four tabs. A real render of LiveAgentCard over
// a model derived by the real deriveCardModel; the runs list itself is a stand-in here (it has its
// own test), so what is pinned is the gating and the tab strip's keyboard order.
import React, { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { deriveCardModel } from '../agent-phase'

;(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
;(globalThis as unknown as { React: typeof React }).React = React

await import('../agent-card.jsx')

type W = Record<string, unknown>
const w = window as unknown as W

const identity = (id: string) => ({
  id,
  name: id === 'pi' ? 'Pi' : 'Hermes',
  model: 'test',
  role: 'test',
  rarity: 2,
  el: '#888',
  elGlow: 'rgba(0,0,0,0.1)',
  logo: '',
  abilities: [],
})

function modelFor(agent: string) {
  const m = deriveCardModel({
    agent,
    record: { name: agent, status: 'installed', unit_active: true },
    overview: null,
    slot: null,
    job: null,
    caps: null,
    urls: null,
  })
  return { ...m, overview: null, health: { tput: null, ctxUsed: null, ctxMax: null, ctxPct: 0 } }
}

let root: Root | null = null
let container: HTMLElement

async function mount(agent: string) {
  const Card = w.LiveAgentCard as React.ComponentType<Record<string, unknown>>
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  await act(async () => {
    root!.render(<Card identity={identity(agent)} model={modelFor(agent)} actions={{}} />)
  })
}

const tabs = () =>
  Array.from(container.querySelectorAll('[role="tab"]')).map((t) => t.getAttribute('data-testid'))

beforeEach(() => {
  w.Icon = () => null
  w.StatusDot = () => null
  w.BridgeRunsList = () => <div data-testid="delegated-stub">runs</div>
})

afterEach(() => {
  if (root) act(() => root!.unmount())
  root = null
  document.body.innerHTML = ''
})

describe('LiveAgentCard console tabs', () => {
  it('Hermes has a Delegated tab after Lifecycle that shows the runs list', async () => {
    await mount('hermes')
    expect(tabs()).toEqual([
      'agent-tab-status',
      'agent-tab-controls',
      'agent-tab-lore',
      'agent-tab-lifecycle',
      'agent-tab-delegated',
    ])
    expect(container.querySelector('[data-testid="delegated-stub"]')).toBeNull()

    const tab = container.querySelector('[data-testid="agent-tab-delegated"]') as HTMLElement
    expect(tab.textContent).toBe('Delegated')
    await act(async () => {
      tab.click()
    })
    expect(tab.getAttribute('aria-selected')).toBe('true')
    expect(container.querySelector('[data-testid="delegated-stub"]')).not.toBeNull()
  })

  it('says so when the runs list did not load, instead of showing Controls under a Delegated tab', async () => {
    delete w.BridgeRunsList
    await mount('hermes')
    await act(async () => {
      ;(container.querySelector('[data-testid="agent-tab-delegated"]') as HTMLElement).click()
    })
    expect(container.textContent).toContain('Delegated runs unavailable')
    expect(container.querySelector('[data-testid="delegated-stub"]')).toBeNull()
    // not the Controls pane
    expect(container.querySelector('[data-testid="agent-tab-controls"]')!.getAttribute('aria-selected')).toBe('false')
  })

  it('arrow keys step through the Delegated tab and wrap around', async () => {
    await mount('hermes')
    const last = container.querySelector('[data-testid="agent-tab-delegated"]') as HTMLElement
    const first = container.querySelector('[data-testid="agent-tab-status"]') as HTMLElement
    await act(async () => {
      first.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowLeft', bubbles: true }))
    })
    expect(last.getAttribute('aria-selected')).toBe('true')
    await act(async () => {
      last.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }))
    })
    expect(first.getAttribute('aria-selected')).toBe('true')
  })

  it('Pi keeps its four tabs and no Delegated pane', async () => {
    await mount('pi')
    expect(tabs()).toEqual([
      'agent-tab-status',
      'agent-tab-controls',
      'agent-tab-lore',
      'agent-tab-lifecycle',
    ])
    expect(container.querySelector('[data-testid="agent-tab-delegated"]')).toBeNull()
  })
})
