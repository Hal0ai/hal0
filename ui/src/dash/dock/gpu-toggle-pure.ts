// The top bar's GPU mode toggle (Chat | Image), as pure rules: whether it shows, which segment is
// selected, what each segment's click calls, and the words of its confirm. The state is
// image/status-line-pure.ts's (the line's `kind` and `actions`, the arbiter facts) and what an
// action calls is image/useStatusLineActions.ts's; this module only maps one to the other, so the
// mapping is tested without rendering (__tests__/gpu-toggle-pure.test.ts).

import type { ArbiterState } from '@/api/hooks/useComfyui'
import type { MemoryBudgetJson } from '@/api/hooks/useStatsHardware'
import {
  costLine,
  pauseFacts,
  pauseLabel,
  type ActionFacts,
  type StatusAction,
  type StatusLineKind,
  type StatusLineView,
} from '@/dash/image/status-line-pure'

export type GpuSegmentId = 'chat' | 'image'

// Kinds the toggle leaves to the footer: a blocked gate, a refused prompt and a stale engine each
// have buttons (and words) the toggle has no place for, and each hides what mode the GPU is in.
const FOOTER_ONLY: ReadonlySet<StatusLineKind> = new Set(['blocked', 'refused', 'stale'])

// Image selected: a switch to image mode, the hand-off, a render, a pin, grace, image mode on
// with no phase. Chat selected: chat mode, and the reload on the way back to it.
const IMAGE_SIDE: ReadonlySet<StatusLineKind> = new Set([
  'switching',
  'handoff',
  'rendering',
  'pinned',
  'grace',
  'img_on',
])

export interface GpuToggleModel {
  /** The selected segment. */
  selected: GpuSegmentId
  /** Chat shows a spinner: the slots are reloading. */
  reloading: boolean
  /** Image shows a spinner: the switch to image mode is under way. */
  switching: boolean
  /** Image shows a lock: image mode is held by a pin. */
  pinned: boolean
  /** Image shows a progress bar: a render is running. */
  rendering: boolean
}

/** The toggle's state, or null when it is not shown (no line to read, or a footer-only kind). */
export function gpuToggleModel(
  view: Pick<StatusLineView, 'kind'> | null | undefined,
  facts: Pick<ActionFacts, 'pinned'>,
): GpuToggleModel | null {
  if (!view || FOOTER_ONLY.has(view.kind)) return null
  return {
    selected: IMAGE_SIDE.has(view.kind) ? 'image' : 'chat',
    reloading: view.kind === 'reloading',
    switching: view.kind === 'switching',
    pinned: facts.pinned,
    rendering: view.kind === 'rendering',
  }
}

/**
 * What a click on a segment calls, or null when it does nothing.
 *   Image, in chat mode                     hold  (asks first: a pin refuses GPU chat)
 *   Image, while image mode is on and       keep  (the line offers it: grace, hand-off, rendering,
 *     not pinned                                  image mode on; asks first as well)
 *   Image, pinned / switching / reloading   nothing (already held, or mid-change)
 *   Chat, in image mode                     stop  (unpins first, and asks before dropping a render:
 *                                                  the hook does both)
 *   Chat, in chat mode / switching          nothing
 * `keep` is read off the line's own actions, so a state the line offers no Keep in (a switch, a
 * pin) is never one the toggle pins in.
 */
export function toggleClickAction(
  segment: GpuSegmentId,
  view: Pick<StatusLineView, 'kind' | 'actions'> | null | undefined,
  facts: Pick<ActionFacts, 'pinned'>,
): StatusAction | null {
  const model = gpuToggleModel(view, facts)
  if (!view || !model) return null
  if (segment === 'chat') {
    return model.selected === 'image' && !model.switching ? 'stop' : null
  }
  if (model.selected === 'chat') return view.kind === 'chat' ? 'hold' : null
  if (model.pinned) return null
  return view.actions.includes('keep') ? 'keep' : null
}

/** The group's tooltip: the line's full cost sentence, or its headline when it has none. */
export function toggleTitle(
  view: Pick<StatusLineView, 'head'> | null | undefined,
  unloadSet: readonly string[] | null | undefined,
  memory: MemoryBudgetJson | null | undefined,
  arbiter: ArbiterState | null | undefined,
): string {
  const cost = costLine(unloadSet, memory, arbiter).join(' ')
  return cost || view?.head || ''
}

export const PIN_POPOVER_LABEL = 'Hold image mode?'
export const PIN_POPOVER_CONFIRM = 'Switch to Image'

const REFUSE = 'Refuse GPU chat until you switch back?'

/**
 * The question the popover asks before a pin. Slot names and the figure are costLine's (the same
 * `pauseFacts`), never typed here. `hold` is the switch out of chat mode, so it names what pauses;
 * `keep` is image mode already on, whose paused slots are the ones that stay paused.
 */
export function pinPopoverQuestion(
  kind: 'hold' | 'keep',
  unloadSet: readonly string[] | null | undefined,
  memory: MemoryBudgetJson | null | undefined,
): string {
  const facts = pauseFacts(unloadSet, memory)
  if (kind === 'hold') {
    return facts ? `Pause ${pauseLabel(facts)} and refuse GPU chat until you switch back?` : REFUSE
  }
  return facts ? `${REFUSE} The paused GPU slots stay paused.` : REFUSE
}
