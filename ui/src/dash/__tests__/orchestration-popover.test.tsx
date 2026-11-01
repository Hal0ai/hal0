// @vitest-environment happy-dom
import React, { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import type { BoardOrchestration } from '../../api/hooks/useBoard'

const LIVE: BoardOrchestration = {
  orchestrator_profile: 'dispatcher',
  default_assignee: 'agent',
  auto_decompose: true,
  auto_promote_children: true,
}

const globals = window as unknown as Record<string, unknown>
let OrchPopover: React.ComponentType<{ orch?: Partial<BoardOrchestration>; set?: ReturnType<typeof vi.fn>; onClose?: ReturnType<typeof vi.fn> }>
let query: { data?: BoardOrchestration; isSuccess: boolean; isError: boolean }
let root: Root
let container: HTMLDivElement
const mutate = vi.fn()
const fallbackSet = vi.fn()
const onClose = vi.fn()
const toast = vi.fn()

beforeAll(async () => {
  vi.stubGlobal('React', React)
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  await import('../board/orchestration-popover.jsx')
  OrchPopover = globals.OrchPopover as typeof OrchPopover
})

beforeEach(() => {
  vi.clearAllMocks()
  query = { isSuccess: false, isError: false }
  globals.__hal0UseBoardOrchestration = () => query
  globals.__hal0UseBoardProfiles = () => ({ data: ['dispatcher', 'operator'] })
  globals.__hal0UseBoardAssignees = () => ({ data: ['agent', 'operator'] })
  globals.__hal0UseUpdateOrchestration = () => ({ mutate })
  globals.__hal0Toast = toast
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
})

afterEach(() => {
  act(() => root.unmount())
  container.remove()
  for (const key of [
    '__hal0UseBoardOrchestration', '__hal0UseBoardProfiles',
    '__hal0UseBoardAssignees', '__hal0UseUpdateOrchestration', '__hal0Toast',
  ]) delete globals[key]
})

function render(orch?: Partial<BoardOrchestration>) {
  act(() => root.render(<OrchPopover orch={orch} set={fallbackSet} onClose={onClose} />))
}

function control(id: string) {
  return container.querySelector(`[data-testid="board-orch-${id}"]`)! as HTMLSelectElement
}

function save() {
  return container.querySelector('[data-testid="board-action-orch-save"]')! as HTMLButtonElement
}

function pick(id: string, value: string) {
  act(() => {
    control(id).value = value
    control(id).dispatchEvent(new Event('change', { bubbles: true }))
  })
}

function expectForm(value: BoardOrchestration) {
  expect(control('profile').value).toBe(value.orchestrator_profile)
  expect(control('assignee').value).toBe(value.default_assignee)
  expect(control('autodecompose').classList.contains('on')).toBe(value.auto_decompose)
  expect(control('autopromote').classList.contains('on')).toBe(value.auto_promote_children)
}

describe('orchestration popover load safety', () => {
  it('blocks Save before the query resolves, even with fallback props', () => {
    render(LIVE)
    expect(save().disabled).toBe(true)
    expect(container.textContent).toContain('Loading orchestration settings')
    act(() => save().click())
    expect(mutate).not.toHaveBeenCalled()
    expect(fallbackSet).not.toHaveBeenCalled()
    expect(toast).not.toHaveBeenCalled()
    expect(onClose).not.toHaveBeenCalled()
  })

  it('re-seeds all four fields on a late success and saves the loaded config', () => {
    render()
    query = { data: LIVE, isSuccess: true, isError: false }
    render()
    expectForm(LIVE)
    expect(save().disabled).toBe(false)
    act(() => save().click())
    expect(mutate).toHaveBeenCalledExactlyOnceWith(LIVE)
  })

  it('fills untouched fields without overwriting an edit made before the load', () => {
    render()
    pick('profile', 'operator')
    query = { data: LIVE, isSuccess: true, isError: false }
    render()
    const expected = { ...LIVE, orchestrator_profile: 'operator' }
    expectForm(expected)
    act(() => save().click())
    expect(mutate).toHaveBeenCalledExactlyOnceWith(expected)
  })

  it('preserves edits to every field across refetches, including cleared values', () => {
    query = { data: LIVE, isSuccess: true, isError: false }
    render()
    pick('profile', '')
    pick('assignee', '')
    act(() => control('autodecompose').click())
    act(() => control('autopromote').click())
    query = { ...query, data: { ...LIVE, orchestrator_profile: 'operator', default_assignee: 'operator' } }
    render()
    const expected = {
      orchestrator_profile: '', default_assignee: '',
      auto_decompose: false, auto_promote_children: false,
    }
    expectForm(expected)
    act(() => save().click())
    expect(mutate).toHaveBeenCalledExactlyOnceWith(expected)
  })

  it('refreshes untouched fields, including false toggles, on refetch', () => {
    query = { data: LIVE, isSuccess: true, isError: false }
    render()
    const refreshed = { ...LIVE, orchestrator_profile: '', default_assignee: '', auto_decompose: false, auto_promote_children: false }
    query = { ...query, data: refreshed }
    render()
    expectForm(refreshed)
  })

  it.each([undefined, LIVE])('keeps Save disabled with a visible reason after a load error (cached: %s)', (data) => {
    render()
    query = { data, isSuccess: false, isError: true }
    render()
    expect(save().disabled).toBe(true)
    expect(container.querySelector('[role="status"]')?.textContent).toContain('Could not load orchestration settings.')
    act(() => save().click())
    expect(mutate).not.toHaveBeenCalled()
    expect(fallbackSet).not.toHaveBeenCalled()
    expect(toast).not.toHaveBeenCalled()
    expect(onClose).not.toHaveBeenCalled()
  })
})
