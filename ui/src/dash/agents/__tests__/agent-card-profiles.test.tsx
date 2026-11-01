// @vitest-environment happy-dom
//
// #3157: the Hermes card names a configured profile whose server is down instead of leaving it out.
// A real render of LiveAgentCard over a model derived by the real deriveCardModel: the front's api
// chip counts the served profiles and flags the down ones; the Status pane's profile chips mark each.
import React, { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import type { AgentOverview } from '@/api/hooks/useAgents'

import { deriveCardModel } from '../agent-phase'

;(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
;(globalThis as unknown as { React: typeof React }).React = React

await import('../agent-card.jsx')

const w = window as unknown as Record<string, unknown>

const identity = {
  id: 'hermes',
  name: 'Hermes',
  model: 'test',
  role: 'test',
  rarity: 2,
  el: '#888',
  elGlow: 'rgba(0,0,0,0.1)',
  logo: '',
  abilities: [],
}

function overview(api: Record<string, unknown>, profiles?: unknown[]): AgentOverview {
  return {
    name: 'hermes',
    kind: 'daemon',
    installed: true,
    status: 'installed',
    unit_active: true,
    phase: 'ready',
    degraded: [],
    job: null,
    units: { agent: 'active', gateway: 'active', gateway_enabled: true },
    api_server: { endpoint: '127.0.0.1:8642', ...api },
    ...(profiles ? { profiles } : {}),
    platforms: ['api_server'],
  } as unknown as AgentOverview
}

let root: Root | null = null
let container: HTMLElement

async function mount(ov: AgentOverview) {
  const m = deriveCardModel({
    agent: 'hermes',
    record: { name: 'hermes', status: 'installed', unit_active: true },
    overview: ov,
    slot: null,
    job: null,
    caps: null,
    urls: null,
  })
  const model = { ...m, overview: ov, health: { tput: null, ctxUsed: null, ctxMax: null, ctxPct: 0 } }
  const Card = w.LiveAgentCard as React.ComponentType<Record<string, unknown>>
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  await act(async () => {
    root!.render(<Card identity={identity} model={model} actions={{}} />)
  })
}

const apiChip = () =>
  Array.from(container.querySelectorAll('.fc-rchip')).find((c) => (c.textContent ?? '').startsWith('api'))

beforeEach(() => {
  w.Icon = () => null
  w.StatusDot = () => null
})

afterEach(() => {
  if (root) act(() => root!.unmount())
  root = null
  document.body.innerHTML = ''
})

const HEALTHY_AND_GHOST = [
  { name: 'default', healthy: true },
  { name: 'coder', healthy: true },
  { name: 'ghost', healthy: false },
]

describe('LiveAgentCard profile health', () => {
  it('flags a down profile on the api chip, which still counts the served ones', async () => {
    await mount(overview({ healthy: true, profiles: ['default', 'coder'] }, HEALTHY_AND_GHOST))
    const chip = apiChip()!
    expect(chip.textContent).toBe('api · 2 profiles · 1 down')
    expect(chip.classList.contains('lit')).toBe(true)
    expect(chip.classList.contains('bad')).toBe(true)
  })

  it('leaves the api chip as it was when every profile answers, or an older server sends no list', async () => {
    await mount(overview({ healthy: true, profiles: ['default', 'coder'] }, HEALTHY_AND_GHOST.slice(0, 2)))
    expect(apiChip()!.textContent).toBe('api · 2 profiles')
    expect(apiChip()!.classList.contains('bad')).toBe(false)
    act(() => root!.unmount())
    document.body.innerHTML = ''
    await mount(overview({ healthy: true, profiles: ['default', 'coder'] }))
    expect(apiChip()!.textContent).toBe('api · 2 profiles')
    expect(apiChip()!.classList.contains('bad')).toBe(false)
  })

  it('marks the down profile in the Status pane, beside the served ones', async () => {
    await mount(overview({ healthy: true, profiles: ['default', 'coder'] }, HEALTHY_AND_GHOST))
    await act(async () => {
      ;(container.querySelector('[data-testid="agent-tab-status"]') as HTMLElement).click()
    })
    const chips = Array.from(container.querySelectorAll('.fcb-pane-in .fc-chip'))
    expect(chips.map((c) => c.textContent)).toEqual(['default', 'coder', 'ghost'])
    expect(chips.map((c) => c.classList.contains('down'))).toEqual([false, false, true])
  })
})
