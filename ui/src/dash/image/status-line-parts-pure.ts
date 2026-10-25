// What the status line component draws besides its headline and detail: the step chips of a
// switch and of a reload, the render's progress figures, and the note on a paused slot card.
// Pure, so the rules are tested without rendering (__tests__/status-line-parts-pure.test.ts).
// The copy that has a fixed wording (the headlines, the wait line) is status-line-pure.ts's, and
// so is the line's `kind`, which says which of these parts a line gets.

import type { ArbiterState, ComfyuiProgress } from '@/api/hooks/useComfyui'
import { slotIsBack, waitLine } from './status-line-pure'

export type StepState = 'done' | 'active' | 'todo'

export interface StepChip {
  key: string
  label: string
  state: StepState
}

// The steps of a switch to image mode, in the order the arbiter runs them.
const SWITCH_STEPS: ReadonlyArray<{ key: string; label: string }> = [
  { key: 'draining', label: 'Chat replies' },
  { key: 'unloading', label: 'Unload slots' },
  { key: 'starting_engine', label: 'Start engine' },
]

/** The switch as chips: the steps before `phase` done, `phase` active, the rest to do. With no
 *  phase yet (the lock is held, the first step has not started) every step is to do. */
export function switchSteps(phase: string | null | undefined): StepChip[] {
  const at = SWITCH_STEPS.findIndex((s) => s.key === phase)
  return SWITCH_STEPS.map((s, i) => ({
    ...s,
    state: at < 0 ? 'todo' : i < at ? 'done' : i === at ? 'active' : 'todo',
  }))
}

// A slot on its way back.
const COMING_BACK = new Set(['starting', 'warming', 'pulling'])

/** The reload as chips: one per slot of the paused set, done once that slot is back. Nothing when
 *  the set or the slot list is unknown. */
export function reloadSteps(
  unloadSet: readonly string[] | null | undefined,
  slots: ReadonlyArray<{ name: string; state: string }> | null | undefined,
): StepChip[] {
  if (!unloadSet || unloadSet.length === 0 || !slots) return []
  return unloadSet.map((name) => {
    const state = slots.find((s) => s.name === name)?.state ?? ''
    return {
      key: name,
      label: name,
      state: slotIsBack(state) ? 'done' : COMING_BACK.has(state) ? 'active' : 'todo',
    }
  })
}

/** m:ss of a number of seconds. */
export function clockLabel(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds))
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`
}

/** The render's percentage, only when the step and the total are known (they are null until the
 *  first progress frame, and for a model that sends none). */
export function renderPct(progress: Pick<ComfyuiProgress, 'step' | 'total'> | null | undefined): number | null {
  if (!progress || progress.step == null || progress.total == null || progress.total <= 0) return null
  return Math.min(100, Math.max(0, Math.round((progress.step / progress.total) * 100)))
}

// ── the paused slot card ─────────────────────────────────────────────────────

export interface SlotPause {
  chip: string
  detail: string
  wait: string | null
}

const CHIP_PAUSED = 'paused · image mode'
const DETAIL_RELOAD = 'Reloads about 10 s after the last image.'
// A pin holds image mode against every automatic restore.
const DETAIL_PINNED = 'Stays paused until image mode is unpinned.'
const DETAIL_RELOADING = 'Reloading now.'

/** The note on a slot card that image mode has paused, or null for any other card. The slot is
 *  paused when image mode is on, it is in the saved set and it is not running: a slot of the set
 *  that is still loaded (a failed unload) or already back (a reload under way) is not.
 *  `slotIsOff`: the card's own lifecycle phase is off (neither running nor changing). */
export function slotPause(
  arbiter: Pick<ArbiterState, 'mode' | 'pinned' | 'phase' | 'unload_set' | 'llm_waiters'> | null | undefined,
  slotName: string,
  slotIsOff: boolean,
): SlotPause | null {
  if (!arbiter || arbiter.mode !== 'img' || !slotIsOff) return null
  if (!arbiter.unload_set?.includes(slotName)) return null
  return {
    chip: CHIP_PAUSED,
    detail: arbiter.pinned ? DETAIL_PINNED : arbiter.phase === 'reloading' ? DETAIL_RELOADING : DETAIL_RELOAD,
    wait: waitLine(arbiter.llm_waiters),
  }
}
