// Task 10 — RichSelect shared listbox. @testing-library/react is not a ui
// dependency (grep confirms 0 hits in package.json), so — same split as
// slot-status.js/breaker-chip.jsx — every stateful rule (open/close,
// keyboard navigation, disabled-skip, commit-on-Enter, aria-activedescendant
// id derivation) lives in this pure module and is pinned here. rich-select.jsx
// is a thin rendering shell over it with nothing left to unit-test in
// isolation from a DOM.
import { describe, expect, it } from 'vitest'

import {
  activeDescendantId,
  closeList,
  commit,
  firstEnabledId,
  handleKey,
  lastEnabledId,
  openList,
  optionDomId,
  selectedOption,
  stepActive,
  toggle,
  type RichSelectOption,
} from '../rich-select-core'

const OPTIONS: RichSelectOption[] = [
  { id: 'a' },
  { id: 'b', disabled: true },
  { id: 'c' },
  { id: 'd', disabled: true },
  { id: 'e' },
]

describe('firstEnabledId / lastEnabledId', () => {
  it('skip leading/trailing disabled options', () => {
    expect(firstEnabledId(OPTIONS)).toBe('a')
    expect(lastEnabledId(OPTIONS)).toBe('e')
  })

  it('return null when every option is disabled', () => {
    const all = [{ id: 'x', disabled: true }, { id: 'y', disabled: true }]
    expect(firstEnabledId(all)).toBeNull()
    expect(lastEnabledId(all)).toBeNull()
  })
})

describe('stepActive', () => {
  it('steps forward to the next enabled option, skipping disabled', () => {
    expect(stepActive(OPTIONS, 'a', 1)).toBe('c')
    expect(stepActive(OPTIONS, 'c', 1)).toBe('e')
  })

  it('steps backward to the previous enabled option, skipping disabled', () => {
    expect(stepActive(OPTIONS, 'e', -1)).toBe('c')
    expect(stepActive(OPTIONS, 'c', -1)).toBe('a')
  })

  it('clamps at the boundary instead of wrapping', () => {
    expect(stepActive(OPTIONS, 'a', -1)).toBe('a')
    expect(stepActive(OPTIONS, 'e', 1)).toBe('e')
  })

  it('starts from the direction-appropriate boundary when nothing is active', () => {
    expect(stepActive(OPTIONS, null, 1)).toBe('a')
    expect(stepActive(OPTIONS, null, -1)).toBe('e')
  })
})

describe('openList / toggle', () => {
  it('opens with the current value as active when the value is a selectable option', () => {
    expect(openList(OPTIONS, 'c')).toEqual({ open: true, activeId: 'c' })
  })

  it('falls back to the first enabled option when the value is unset', () => {
    expect(openList(OPTIONS, null)).toEqual({ open: true, activeId: 'a' })
  })

  it('falls back to the first enabled option when the value is itself disabled', () => {
    expect(openList(OPTIONS, 'b')).toEqual({ open: true, activeId: 'a' })
  })

  it('toggle opens a closed list and closes an open one', () => {
    const closed = { open: false, activeId: 'c' }
    const opened = toggle(closed, OPTIONS, 'c')
    expect(opened.open).toBe(true)
    const reclosed = toggle(opened, OPTIONS, 'c')
    expect(reclosed).toEqual({ open: false, activeId: 'c' })
  })
})

describe('closeList', () => {
  it('closes and resets activeId to the given value', () => {
    expect(closeList('c')).toEqual({ open: false, activeId: 'c' })
    expect(closeList(null)).toEqual({ open: false, activeId: null })
  })
})

describe('commit', () => {
  it('commits the active option when it is enabled, closing the list', () => {
    const result = commit({ open: true, activeId: 'c' }, OPTIONS)
    expect(result.committedId).toBe('c')
    expect(result.nextState).toEqual({ open: false, activeId: 'c' })
  })

  it('refuses to commit a disabled active option', () => {
    const result = commit({ open: true, activeId: 'b' }, OPTIONS)
    expect(result.committedId).toBeNull()
    expect(result.nextState).toEqual({ open: true, activeId: 'b' })
  })

  it('refuses to commit when nothing is active', () => {
    const result = commit({ open: true, activeId: null }, OPTIONS)
    expect(result.committedId).toBeNull()
  })

  it('commits an explicit target id (mouse click), ignoring what was active', () => {
    const result = commit({ open: true, activeId: 'a' }, OPTIONS, 'e')
    expect(result.committedId).toBe('e')
    expect(result.nextState).toEqual({ open: false, activeId: 'e' })
  })

  it('refuses an explicit target id that is disabled', () => {
    const result = commit({ open: true, activeId: 'a' }, OPTIONS, 'b')
    expect(result.committedId).toBeNull()
    expect(result.nextState).toEqual({ open: true, activeId: 'a' })
  })
})

describe('handleKey', () => {
  it('ArrowDown on a closed list opens it, activating the value', () => {
    const outcome = handleKey('ArrowDown', { open: false, activeId: 'c' }, OPTIONS, 'c')
    expect(outcome).toEqual({ kind: 'state', state: { open: true, activeId: 'c' } })
  })

  it('ArrowDown/ArrowUp while open move the active id, skipping disabled', () => {
    const down = handleKey('ArrowDown', { open: true, activeId: 'a' }, OPTIONS, 'a')
    expect(down).toEqual({ kind: 'state', state: { open: true, activeId: 'c' } })
    const up = handleKey('ArrowUp', { open: true, activeId: 'c' }, OPTIONS, 'a')
    expect(up).toEqual({ kind: 'state', state: { open: true, activeId: 'a' } })
  })

  it('Home/End jump to the first/last enabled option while open', () => {
    expect(handleKey('Home', { open: true, activeId: 'c' }, OPTIONS, 'c')).toEqual({
      kind: 'state',
      state: { open: true, activeId: 'a' },
    })
    expect(handleKey('End', { open: true, activeId: 'c' }, OPTIONS, 'c')).toEqual({
      kind: 'state',
      state: { open: true, activeId: 'e' },
    })
  })

  it('Enter commits the active enabled option and closes', () => {
    const outcome = handleKey('Enter', { open: true, activeId: 'c' }, OPTIONS, 'a')
    expect(outcome).toEqual({ kind: 'commit', state: { open: false, activeId: 'c' }, id: 'c' })
  })

  it('Enter on a disabled active option does nothing', () => {
    const outcome = handleKey('Enter', { open: true, activeId: 'b' }, OPTIONS, 'a')
    expect(outcome).toEqual({ kind: 'none' })
  })

  it('Escape closes without committing and resets to the current value', () => {
    const outcome = handleKey('Escape', { open: true, activeId: 'c' }, OPTIONS, 'a')
    expect(outcome).toEqual({ kind: 'close', state: { open: false, activeId: 'a' } })
  })

  it('Tab closes without committing, leaving focus free to move', () => {
    const outcome = handleKey('Tab', { open: true, activeId: 'c' }, OPTIONS, 'a')
    expect(outcome).toEqual({ kind: 'close', state: { open: false, activeId: 'a' } })
  })

  it('ignores keys it does not handle', () => {
    expect(handleKey('a', { open: true, activeId: 'c' }, OPTIONS, 'a')).toEqual({ kind: 'none' })
    expect(handleKey('Escape', { open: false, activeId: 'c' }, OPTIONS, 'a')).toEqual({ kind: 'none' })
  })
})

describe('activeDescendantId / optionDomId', () => {
  it('derives a stable per-option DOM id from a base id', () => {
    expect(optionDomId('slot-profile', 'c')).toBe('slot-profile-option-c')
  })

  it('is undefined while closed, and the active option id while open', () => {
    expect(activeDescendantId('slot-profile', { open: false, activeId: 'c' })).toBeUndefined()
    expect(activeDescendantId('slot-profile', { open: true, activeId: 'c' })).toBe('slot-profile-option-c')
  })

  it('is undefined while open with nothing active', () => {
    expect(activeDescendantId('slot-profile', { open: true, activeId: null })).toBeUndefined()
  })
})

describe('selectedOption', () => {
  it('returns the option matching the current value', () => {
    expect(selectedOption(OPTIONS, 'c')).toBe(OPTIONS[2])
  })

  it('returns null when the value is unset or matches nothing', () => {
    expect(selectedOption(OPTIONS, null)).toBeNull()
    expect(selectedOption(OPTIONS, 'zzz')).toBeNull()
  })
})
