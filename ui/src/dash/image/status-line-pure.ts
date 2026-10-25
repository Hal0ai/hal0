// The GPU status line at the head of the Image Gen pane: what the GPU is doing, what an image
// costs chat, and the actions that fit. Pure, so the copy, the precedence and the actions are
// tested without rendering (__tests__/status-line-pure.test.ts). The component only draws the
// result and wires each action id to its call.
//
// It merges two responses: `/api/comfyui/arbiter` (the arbiter block and its envelope, polled
// every second while anything moves) and `/api/comfyui/status` (the editor render's queue
// counts, the hal0 render's progress and the GPU gate), plus the memory budget from
// `/api/stats/hardware`. The line never judges whether anything fits: that is the server's.

import type { ArbiterEnvelope, ArbiterState, ComfyuiStatus, GateRefusal } from '@/api/hooks/useComfyui'
import type { MemoryBudgetJson } from '@/api/hooks/useStatsHardware'

export type StatusTone = 'ok' | 'warn' | 'img' | 'pin' | 'err'

// What a button on the line does. The pure layer only names it; the panel makes the call:
//   hold           switchover {mode: "generation", pin: true} (chat mode only: in image mode the
//                  server answers a no-op and ignores the pin)
//   keep           POST /pin {pinned: true}
//   unpin          POST /pin {pinned: false}
//   stop           POST /pin {pinned: false} first when pinned, then switchover {mode:
//                  "inference"}; `force: true` only after a confirm, and only while a render runs
//   cancel         abort this browser's own Generate run (it owns the AbortController)
//   cancel_render  POST /render/cancel (also clears editor prompts)
//   restart_engine POST /restart
//   open_editor    the ComfyUI editor link from /api/config/urls
//   dismiss        remember `gate_refusal.at` (localStorage, in a try/catch)
export type StatusAction =
  | 'hold'
  | 'cancel'
  | 'cancel_render'
  | 'keep'
  | 'unpin'
  | 'stop'
  | 'open_editor'
  | 'dismiss'
  | 'restart_engine'

export interface StatusLineView {
  tone: StatusTone
  head: string
  // Plain text for a text node. A gate refusal's reason is cleaned by the server but is still
  // text from another container: never render it as HTML.
  detail: string
  actions: StatusAction[]
}

// The part of /status the line reads. Every field may be missing from an older backend or the
// fallback shell, and is read that way.
export type StatusLineInputs = Pick<ComfyuiStatus, 'queue' | 'progress' | 'gate' | 'gate_refusal'>

export interface StatusLineOpts {
  // The slots (`useSlots`), to count how many of the saved set have come back ready.
  slots?: ReadonlyArray<{ name: string; state: string }>
  // `gate_refusal.at` of the refusal the viewer last dismissed (localStorage).
  dismissedAt?: number | null
  // Epoch seconds, the clock `restore_at` is on. A parameter so the countdown is testable.
  now: number
  // `engine.available` of the model catalog. Anything but `false` (including "not loaded yet")
  // shows the line.
  engineAvailable?: boolean
  // True when this browser's own Generate run started the switch in progress: only then does
  // the line offer Cancel (the panel owns the AbortController). Otherwise there is nothing the
  // line can abort.
  ownRun?: boolean
}

// ── copy ─────────────────────────────────────────────────────────────────────

const HEAD_CHAT = 'GPU · chat mode'
const HEAD_SWITCHING = 'Switching to image mode'
const HEAD_HANDOFF = 'Image mode · hand-off'
const HEAD_RELOADING = 'Reloading GPU slots'
const HEAD_PINNED = 'Image mode pinned'
const HEAD_STALE = 'The image engine runs an older GPU gate'
const HEAD_NO_CREDENTIAL =
  "Image generation is blocked: hal0 requires sign-in and ComfyUI's GPU gate has no credential for it yet"

const COST_RELOAD = 'They reload about 10 s after the last image.'
const COST_NO_SLOTS = 'No GPU slots to pause on this box.'
const COST_IMAGE_ON = 'Image mode is still on, so this one starts at once.'
// The chat-wait cap: `_LLM_DEMAND_WAIT_S` in src/hal0/slots/arbiter.py.
const WAIT_CAP = '2:00'

// ADR-0040 and docs/operate/auth.mdx ("ComfyUI's GPU gate credential") have the fix. The
// opt-out is named because the docs name it, not because it is the answer: it also drops the
// guard against rendering next to the LLM slots.
const DETAIL_NO_CREDENTIAL =
  'See ADR-0040 and the GPU gate section of operate/auth for the fix. ' +
  "HAL0_COMFYUI_GATE_FAIL_OPEN=1 opts out of the gate's check, and it also drops the guard against rendering next to the LLM slots."

const pad2 = (n: number) => String(n).padStart(2, '0')

// 1.4 and 3 read the same way in a sentence: "29.3 GB", "20 GB".
const fmtGb = (gb: number) => String(Number(gb.toFixed(1)))

// a / a and b / a, b and c
function nameList(names: readonly string[]): string {
  if (names.length <= 1) return names.join('')
  return `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`
}

// Local wall-clock hh:mm of an epoch-seconds stamp.
function clock(epochS: number): string {
  const d = new Date(epochS * 1000)
  return `${pad2(d.getHours())}:${pad2(d.getMinutes())}`
}

function mmss(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds))
  return `${Math.floor(whole / 60)}:${pad2(whole % 60)}`
}

// ── arbiter phases ───────────────────────────────────────────────────────────

// The steps of a switch to image mode, as `arbiter.phase` names them.
const SWITCH_PHASES: ReadonlySet<string> = new Set(['draining', 'unloading', 'starting_engine'])

// Whether the GPU is in image mode and not being switched or restored: the state in which a
// new image starts at once.
function imageModeOn(arbiter: ArbiterState | null | undefined): boolean {
  if (arbiter?.mode !== 'img') return false
  return !(arbiter.phase && (SWITCH_PHASES.has(arbiter.phase) || arbiter.phase === 'reloading'))
}

// ── cost and wait lines ──────────────────────────────────────────────────────

// What the next image costs chat, one line per sentence group.
//
//   chat mode, known set    Pauses {a, b and c} ({gb} GB). They reload about 10 s after the last image.
//                           ({gb} sums the `memory.gpu.this_box` rows of those slots, and is left
//                           out when none has a row)
//   chat mode, empty set    No GPU slots to pause on this box.
//   chat mode, unknown set  nothing: a cold cache is not "no GPU slots"
//   image mode (grace, hand-off, rendering, pinned)
//                           Image mode is still on, so this one starts at once.
//   a switch waiting on chat replies: + "{n} chat reply is finishing first."
export function costLine(
  unloadSet: readonly string[] | null | undefined,
  memory: MemoryBudgetJson | null | undefined,
  arbiter: ArbiterState | null | undefined,
): string[] {
  const lines: string[] = []
  if (imageModeOn(arbiter)) {
    lines.push(COST_IMAGE_ON)
  } else if (unloadSet) {
    if (unloadSet.length === 0) {
      lines.push(COST_NO_SLOTS)
    } else {
      const known = (memory?.gpu?.this_box ?? []).filter((row) => unloadSet.includes(row.slot))
      const gb = known.reduce((sum, row) => sum + row.gb, 0)
      const figure = known.length > 0 ? ` (${fmtGb(gb)} GB)` : ''
      lines.push(`Pauses ${nameList(unloadSet)}${figure}. ${COST_RELOAD}`)
    }
  }
  const draining = arbiter?.drain_in_flight ?? 0
  if (draining > 0) {
    lines.push(`${draining} chat ${draining === 1 ? 'reply is' : 'replies are'} finishing first.`)
  }
  return lines
}

// Chat requests held up by image mode: "{n} chat requests waiting, longest {m:ss} of 2:00."
export function waitLine(
  waiters: { count: number; oldest_s: number | null } | null | undefined,
): string | null {
  if (!waiters || waiters.count <= 0) return null
  const noun = waiters.count === 1 ? 'request' : 'requests'
  if (waiters.oldest_s == null) return `${waiters.count} chat ${noun} waiting.`
  return `${waiters.count} chat ${noun} waiting, longest ${mmss(waiters.oldest_s)} of ${WAIT_CAP}.`
}

// ── the line ─────────────────────────────────────────────────────────────────

const joinDetail = (...parts: Array<string | null | undefined>) => parts.filter(Boolean).join(' ')

// Image mode paused some slots. `unload_set` is the saved set in image mode; empty or null means
// none were paused (or none are known), and then the line says nothing about them.
const hasPausedSlots = (arbiter: ArbiterState) => (arbiter.unload_set?.length ?? 0) > 0

// What becomes of the paused slots: `whenUnpinned` for an image mode that ends on its own, and
// for a pinned one the plain fact that it does not (a pin holds image mode against every
// automatic restore, so there is no "about 10 s after the last image").
function pausedNote(arbiter: ArbiterState, pinned: boolean, whenUnpinned: string): string | null {
  if (!hasPausedSlots(arbiter)) return null
  return pinned ? 'The paused GPU slots stay paused until you unpin image mode.' : whenUnpinned
}

function queueTotal(status: StatusLineInputs | null | undefined): number {
  return (status?.queue?.running ?? 0) + (status?.queue?.pending ?? 0)
}

// The newer of the two responses' refusals (they are the same server-side record, read at
// different moments).
function latestRefusal(
  env: ArbiterEnvelope | null | undefined,
  status: StatusLineInputs | null | undefined,
): GateRefusal | null {
  const a = env?.gate_refusal ?? null
  const b = status?.gate_refusal ?? null
  if (a && b) return a.at >= b.at ? a : b
  return a ?? b
}

function renderingHead(status: StatusLineInputs | null | undefined): string {
  const progress = status?.progress
  if (progress) {
    return progress.step != null && progress.total != null
      ? `Rendering · ${progress.model} · step ${progress.step}/${progress.total}`
      : `Rendering · ${progress.model}`
  }
  // A render from the ComfyUI editor has no progress entry: only the queue shows it.
  const queued = queueTotal(status)
  return queued > 0 ? `Rendering · editor prompt · ${queued} queued` : 'Rendering'
}

function reloadingHead(arbiter: ArbiterState, opts: StatusLineOpts): string {
  const set = arbiter.unload_set ?? []
  if (!opts.slots || set.length === 0) return HEAD_RELOADING
  const ready = new Set(
    opts.slots.filter((s) => ['ready', 'serving', 'idle'].includes(s.state)).map((s) => s.name),
  )
  const back = set.filter((name) => ready.has(name)).length
  return `${HEAD_RELOADING} · ${back} of ${set.length} ready`
}

function switchingDetail(arbiter: ArbiterState): string {
  switch (arbiter.phase) {
    case 'draining':
      return arbiter.drain_in_flight > 0
        ? `Waiting for ${arbiter.drain_in_flight} chat ${arbiter.drain_in_flight === 1 ? 'reply' : 'replies'} to finish.`
        : 'Waiting for chat replies to finish.'
    case 'unloading':
      return arbiter.unload_set && arbiter.unload_set.length > 0
        ? `Unloading ${nameList(arbiter.unload_set)} to free the GPU.`
        : 'Unloading GPU slots.'
    case 'starting_engine':
      return 'Starting the image engine.'
    default:
      return 'Preparing the switch.'
  }
}

// Work in progress, which a viewer needs to see ahead of anything that blocks nothing: a
// switch, the editor hand-off, a render, a reload. null when the GPU is not doing any.
function workLine(
  arbiter: ArbiterState,
  status: StatusLineInputs | null | undefined,
  opts: StatusLineOpts,
  pinned: boolean,
): StatusLineView | null {
  const phase = arbiter.phase
  const waiting = waitLine(arbiter.llm_waiters)
  // A pinned box that is working shows the work, and can still be unpinned.
  const unpin: StatusAction[] = pinned ? ['unpin'] : []

  // `switching` is true a beat before any phase is set, and the legacy mode still reads
  // "inference" through it, so it is a switch even with no phase.
  if ((phase && SWITCH_PHASES.has(phase)) || (arbiter.switching && phase == null)) {
    return {
      tone: 'img',
      head: HEAD_SWITCHING,
      detail: joinDetail(switchingDetail(arbiter), waiting),
      actions: [...(opts.ownRun ? (['cancel'] as const) : []), ...unpin],
    }
  }

  // A render in ComfyUI's own queue (the editor's) is invisible to the arbiter: it reads as
  // grace, hand-off or nothing, so a busy queue in image mode is a render.
  const queueRender =
    arbiter.mode === 'img' &&
    queueTotal(status) > 0 &&
    (phase === 'grace' || phase === 'handoff' || phase == null)
  if (phase === 'rendering' || queueRender) {
    return {
      tone: 'img',
      head: renderingHead(status),
      detail: joinDetail(
        pausedNote(arbiter, pinned, 'The paused GPU slots reload about 10 s after the last image.'),
        waiting,
      ),
      actions: ['cancel_render', pinned ? 'unpin' : 'keep'],
    }
  }

  if (phase === 'handoff') {
    return {
      tone: 'img',
      head: HEAD_HANDOFF,
      detail: joinDetail('A ComfyUI editor prompt is taking the GPU.', waiting),
      actions: pinned ? ['unpin'] : ['keep'],
    }
  }

  if (phase === 'reloading') {
    return {
      tone: 'img',
      head: reloadingHead(arbiter, opts),
      detail: joinDetail('Chat comes back as each slot loads.', waiting),
      actions: unpin,
    }
  }

  return null
}

// The line, or null when there is nothing to show: no GPU pool reading, no image engine on
// this host, or (without a gate problem to report) no word from the arbiter.
//
// Precedence, first match wins:
//   1. the GPU gate has no credential (never dismissible)
//   2. work in progress: a switch, the hand-off, a render, a reload
//   3. the gate refused a prompt, until dismissed
//   4. the gate runs an older version
//   5. image mode pinned and idle
//   6. grace
//   7. chat
export function statusLine(
  env: ArbiterEnvelope | null | undefined,
  status: StatusLineInputs | null | undefined,
  memory: MemoryBudgetJson | null | undefined,
  opts: StatusLineOpts,
): StatusLineView | null {
  if (!memory?.gpu || opts.engineAvailable === false) return null

  const gate = status?.gate?.state
  if (gate === 'no_credential') {
    return { tone: 'err', head: HEAD_NO_CREDENTIAL, detail: DETAIL_NO_CREDENTIAL, actions: [] }
  }

  const arbiter = env?.arbiter ?? null
  // Pinned means image mode is held: a stray pin flag in chat mode is not "image mode pinned".
  const pinned = arbiter?.pinned === true && arbiter.mode === 'img'

  if (arbiter) {
    const work = workLine(arbiter, status, opts, pinned)
    if (work) return work
  }

  const refusal = latestRefusal(env, status)
  if (refusal && !(opts.dismissedAt != null && opts.dismissedAt >= refusal.at)) {
    // Not "editor prompt": hal0 also records refusals from /v1 and from switch requests, and
    // the record has no source.
    return {
      tone: 'warn',
      head: `GPU gate refused a prompt · ${clock(refusal.at)}`,
      detail: refusal.reason,
      actions: ['open_editor', 'dismiss'],
    }
  }

  if (gate === 'stale') {
    return {
      tone: 'warn',
      head: HEAD_STALE,
      detail: 'Restart the engine to load the current gate.',
      actions: ['restart_engine'],
    }
  }

  if (!arbiter) return null
  const waiting = waitLine(arbiter.llm_waiters)

  if (pinned) {
    return {
      tone: 'pin',
      head: HEAD_PINNED,
      detail: joinDetail(
        'Held until you unpin it.',
        hasPausedSlots(arbiter) ? 'The paused GPU slots stay paused.' : null,
        waiting,
      ),
      actions: ['unpin', 'stop'],
    }
  }

  if (arbiter.phase === 'grace') {
    const left = Math.max(0, Math.ceil((arbiter.restore_at ?? opts.now) - opts.now))
    return {
      tone: 'img',
      head: `Image mode ends in ${left} s`,
      detail: joinDetail(pausedNote(arbiter, false, 'The paused GPU slots reload when it ends.'), waiting),
      actions: ['keep'],
    }
  }

  // Image mode with no phase and no pin: right after an API restart, until the restore loop's
  // first tick starts the quiet clock. Saying "chat mode" would be wrong, and Hold would do
  // nothing (the server ignores the pin when image mode is already on).
  if (arbiter.mode === 'img') {
    return {
      tone: 'img',
      head: 'Image mode is on',
      detail: joinDetail(
        pausedNote(arbiter, false, 'The paused GPU slots reload once the last image is done.'),
        waiting,
      ),
      actions: ['keep'],
    }
  }

  return {
    tone: 'ok',
    head: HEAD_CHAT,
    detail: costLine(arbiter.unload_set, memory, arbiter).join(' '),
    actions: ['hold'],
  }
}
