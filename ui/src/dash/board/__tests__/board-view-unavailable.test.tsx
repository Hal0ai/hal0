// @vitest-environment happy-dom
//
// BoardView: the three Hermes-kanban unavailability codes render an honest
// empty state in place of the lanes; a healthy board still renders lanes.
import React from 'react'
import { createRoot } from 'react-dom/client'
import { act } from 'react-dom/test-utils'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { Hal0Error } from '@/api/client'

;(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true
;(globalThis as any).React = React

await import('../lane.jsx')
await import('../board-view.jsx')
const BoardView = (window as any).BoardView as React.ComponentType

let host: HTMLDivElement
let root: ReturnType<typeof createRoot>
let viewQ: any

function mount() {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  act(() => root.render(<BoardView />))
}

beforeEach(() => {
  ;(window as any).__hal0UseBoardView = () => viewQ
})

afterEach(() => {
  act(() => root.unmount())
  host.remove()
})

const CASES: Array<[string, string, string, string]> = [
  ['board.not_installed', 'No board yet', 'Install the Hermes agent to use the Operator Board.', 'Open Agents'],
  ['board.unreachable', 'Hermes agent is not running', 'The board lives in the Hermes kanban. Start the agent to see it.', 'Open the Hermes card'],
  ['board.no_token', 'Board token missing', 'Restart the Hermes agent so hal0 can mint its dashboard token.', 'Open the Hermes card'],
]

describe('BoardView unavailable states', () => {
  for (const [code, title, body, link] of CASES) {
    it(`shows the ${code} empty state instead of lanes`, () => {
      viewQ = { data: undefined, error: new Hal0Error('x', { code, status: 503 }) }
      mount()
      const state = host.querySelector('[data-testid="board-unavailable"]')
      expect(state).not.toBeNull()
      expect(state!.textContent).toContain(title)
      expect(state!.textContent).toContain(body)
      const a = state!.querySelector('a')!
      expect(a.textContent).toBe(link)
      expect(a.getAttribute('href')).toBe('#agents')
      expect(host.querySelector('[data-testid="board-lane-backlog"]')).toBeNull()
    })
  }

  it('renders lanes when the board loads', () => {
    viewQ = { data: { tasks: [] }, error: null }
    mount()
    expect(host.querySelector('[data-testid="board-unavailable"]')).toBeNull()
    expect(host.querySelector('[data-testid^="board-lane-"]')).not.toBeNull()
  })
})
