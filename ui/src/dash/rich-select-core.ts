// rich-select-core.ts — Task 10: pure state/logic for the shared RichSelect
// listbox (drawer Runtime/Profile/Model selects, Task 11). No React, no DOM —
// same split as slot-status.js/breaker-chip.jsx: every stateful rule lives
// here and is vitest-pinned directly; rich-select.jsx is a thin rendering
// shell that calls into these functions and owns nothing but markup, focus,
// and the click-outside listener.
//
// Arrow/Home/End navigation does not wrap (mirrors command-palette.jsx's
// `Math.min/Math.max` clamp) and always skips disabled options — an active id
// only ever lands on an option that is actually selectable.

export interface RichSelectOption {
  id: string
  disabled?: boolean
  // row / desc / right are opaque render content the shell owns (ReactNode in
  // practice); the core never looks past id/disabled.
}

export interface RichSelectState {
  open: boolean
  activeId: string | null
}

export type KeyOutcome =
  | { kind: 'none' }
  | { kind: 'state'; state: RichSelectState }
  | { kind: 'commit'; state: RichSelectState; id: string }
  | { kind: 'close'; state: RichSelectState }

const isEnabled = (o: RichSelectOption): boolean => !o.disabled

export function firstEnabledId(options: RichSelectOption[]): string | null {
  const found = options.find(isEnabled)
  return found ? found.id : null
}

export function lastEnabledId(options: RichSelectOption[]): string | null {
  for (let i = options.length - 1; i >= 0; i--) {
    if (isEnabled(options[i])) return options[i].id
  }
  return null
}

// Move from `currentId` one step in `dir` (1 = forward, -1 = backward),
// skipping disabled options, clamped at the boundary (no wrap). When
// `currentId` is null/unmatched, starts from the boundary `dir` points at.
export function stepActive(
  options: RichSelectOption[],
  currentId: string | null,
  dir: 1 | -1,
): string | null {
  const idx = currentId == null ? -1 : options.findIndex((o) => o.id === currentId)
  if (idx === -1) return dir === 1 ? firstEnabledId(options) : lastEnabledId(options)
  let i = idx + dir
  while (i >= 0 && i < options.length) {
    if (isEnabled(options[i])) return options[i].id
    i += dir
  }
  return currentId
}

export function initialState(value: string | null): RichSelectState {
  return { open: false, activeId: value }
}

// Opening activates `value` when it names a selectable option, else the
// first enabled option (never lands on a disabled row).
export function openList(options: RichSelectOption[], value: string | null): RichSelectState {
  const valueSelectable = value != null && options.some((o) => o.id === value && isEnabled(o))
  return { open: true, activeId: valueSelectable ? value : firstEnabledId(options) }
}

export function closeList(value: string | null): RichSelectState {
  return { open: false, activeId: value }
}

export function toggle(
  state: RichSelectState,
  options: RichSelectOption[],
  value: string | null,
): RichSelectState {
  return state.open ? closeList(value) : openList(options, value)
}

export interface CommitResult {
  nextState: RichSelectState
  committedId: string | null
}

// Commits a target option (the active one by default, e.g. Enter — but a
// mouse click commits whichever row it lands on regardless of what was last
// active) if — and only if — it is currently enabled. onChange(id) is the
// shell's job to fire, driven off `committedId`.
export function commit(
  state: RichSelectState,
  options: RichSelectOption[],
  targetId: string | null = state.activeId,
): CommitResult {
  const active = targetId != null ? options.find((o) => o.id === targetId) : undefined
  if (!active || !isEnabled(active)) return { nextState: state, committedId: null }
  return { nextState: closeList(active.id), committedId: active.id }
}

// Single entry point for onKeyDown. `value` is the select's committed value
// (used to seed opening from a closed state, and to restore on Escape/Tab).
export function handleKey(
  key: string,
  state: RichSelectState,
  options: RichSelectOption[],
  value: string | null,
): KeyOutcome {
  if (!state.open) {
    if (key === 'ArrowDown' || key === 'ArrowUp' || key === 'Enter' || key === ' ') {
      return { kind: 'state', state: openList(options, value) }
    }
    // #2204: Home/End used to be inert while closed (wired only for the
    // open branch below) — mirror Arrow's "opens the list" behaviour so a
    // keyboard user gets the jump-to-first/last affordance before opening.
    if (key === 'Home') {
      return { kind: 'state', state: { open: true, activeId: firstEnabledId(options) } }
    }
    if (key === 'End') {
      return { kind: 'state', state: { open: true, activeId: lastEnabledId(options) } }
    }
    return { kind: 'none' }
  }
  switch (key) {
    case 'ArrowDown':
      return { kind: 'state', state: { open: true, activeId: stepActive(options, state.activeId, 1) } }
    case 'ArrowUp':
      return { kind: 'state', state: { open: true, activeId: stepActive(options, state.activeId, -1) } }
    case 'Home':
      return { kind: 'state', state: { open: true, activeId: firstEnabledId(options) } }
    case 'End':
      return { kind: 'state', state: { open: true, activeId: lastEnabledId(options) } }
    case 'Enter':
    case ' ': {
      const result = commit(state, options)
      if (result.committedId == null) return { kind: 'none' }
      return { kind: 'commit', state: result.nextState, id: result.committedId }
    }
    case 'Escape':
      return { kind: 'close', state: closeList(value) }
    case 'Tab':
      return { kind: 'close', state: closeList(value) }
    default:
      return { kind: 'none' }
  }
}

export function optionDomId(baseId: string, optionId: string): string {
  return `${baseId}-option-${optionId}`
}

export function activeDescendantId(baseId: string, state: RichSelectState): string | undefined {
  if (!state.open || state.activeId == null) return undefined
  return optionDomId(baseId, state.activeId)
}

// The closed trigger renders this option's row inline.
export function selectedOption(
  options: RichSelectOption[],
  value: string | null,
): RichSelectOption | null {
  if (value == null) return null
  return options.find((o) => o.id === value) ?? null
}
