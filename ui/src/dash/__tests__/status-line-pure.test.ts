// The GPU status line's copy, precedence and actions. Pure: the component reads these.
import { describe, expect, it } from 'vitest'
import type {
  ArbiterEnvelope,
  ArbiterState,
  ComfyuiGate,
  ComfyuiProgress,
  GateRefusal,
} from '@/api/hooks/useComfyui'
import type { MemoryBudgetJson } from '@/api/hooks/useStatsHardware'
import { costLine, statusLine, waitLine, type StatusLineInputs } from '../image/status-line-pure'

function arbiter(over: Partial<ArbiterState> = {}): ArbiterState {
  return {
    mode: 'llm',
    pinned: false,
    saved_llm_slots: [],
    switching: false,
    image_jobs: 0,
    phase: null,
    restore_at: null,
    handoff_until: null,
    drain_in_flight: 0,
    unload_set: ['brain', 'coder'],
    llm_waiters: { count: 0, oldest_s: null },
    ...over,
  }
}

function envelope(over: Partial<ArbiterState> | null = {}, refusal: GateRefusal | null = null): ArbiterEnvelope {
  return {
    mode: null,
    arbiter: over === null ? null : arbiter(over),
    switchover: { active: false, target: null, error: null },
    gate_refusal: refusal,
  }
}

function status(over: Partial<StatusLineInputs> = {}): StatusLineInputs {
  return {
    queue: { running: 0, pending: 0 },
    progress: null,
    gate: { state: 'current', version_seen: '1' } as ComfyuiGate,
    gate_refusal: null,
    ...over,
  }
}

function progress(over: Partial<ComfyuiProgress> = {}): ComfyuiProgress {
  return { model: 'flux-dev', step: 7, total: 20, node: '12', elapsed_s: 9.4, prompt_id: 'p1', ...over }
}

function memory(over: Partial<MemoryBudgetJson> = {}): MemoryBudgetJson {
  return {
    gpu: {
      kind: 'shared',
      total_gb: 96,
      used_gb: 40,
      this_box: [
        { slot: 'brain', gb: 20.2, state: 'ready' },
        { slot: 'coder', gb: 9.1, state: 'ready' },
      ],
      image_engine_gb: 0,
      image_engine_slot: 'img',
      image_engine_estimated: false,
      other_guests_gb: 0,
      attribution_excess_gb: 0,
      free_gb: 56,
    },
    ram: { limit_gb: 64, used_gb: 20, free_gb: 44, source: 'container' },
    render_free_gb: 50,
    ...over,
  }
}

const OPTS = { now: 1000, engineAvailable: true }

// Epoch seconds for a local wall-clock time, so the hh:mm check does not depend on the zone.
const localEpoch = (h: number, m: number) => new Date(2026, 9, 25, h, m).getTime() / 1000

describe('statusLine', () => {
  it('statusLine chat mode headline and hold action', () => {
    const line = statusLine(envelope({ mode: 'llm' }), status(), memory(), OPTS)
    expect(line).toMatchObject({ tone: 'ok', head: 'GPU · chat mode', actions: ['hold'] })
    // The detail is the cost of an image: the slots it pauses and what they hold.
    expect(line?.detail).toBe('Pauses brain and coder (29.3 GB). They reload about 10 s after the last image.')
  })

  it('has nothing to say about an unknown unload set', () => {
    const line = statusLine(envelope({ unload_set: null }), status(), memory(), OPTS)
    expect(line?.head).toBe('GPU · chat mode')
    expect(line?.detail).toBe('')
  })

  it('returns null without a gpu, without an engine or without an arbiter answer', () => {
    expect(statusLine(envelope(), status(), memory({ gpu: null, render_free_gb: null }), OPTS)).toBeNull()
    expect(statusLine(envelope(), status(), memory(), { ...OPTS, engineAvailable: false })).toBeNull()
    expect(statusLine(envelope(null), status(), memory(), OPTS)).toBeNull()
    // The models list has not answered yet: that is not "no engine".
    expect(statusLine(envelope(), status(), memory(), { now: 1000 })).not.toBeNull()
  })

  it('names the switch for every step of it, and for the gap before a step is set', () => {
    for (const phase of ['draining', 'unloading', 'starting_engine']) {
      const line = statusLine(envelope({ phase, switching: true }), status(), memory(), OPTS)
      expect(line).toMatchObject({ tone: 'img', head: 'Switching to image mode' })
    }
    // switching is true a beat before any phase is set; the top-level mode still reads inference.
    expect(statusLine(envelope({ phase: null, switching: true }), status(), memory(), OPTS)?.head).toBe(
      'Switching to image mode',
    )
    // A failed unload leaves switching false with the phase on `unloading`.
    expect(statusLine(envelope({ mode: 'img', phase: 'unloading', switching: false }), status(), memory(), OPTS)?.head).toBe(
      'Switching to image mode',
    )
    const draining = statusLine(envelope({ phase: 'draining', switching: true, drain_in_flight: 2 }), status(), memory(), OPTS)
    expect(draining?.detail).toBe('Waiting for 2 chat replies to finish.')
    expect(
      statusLine(envelope({ phase: 'draining', switching: true, drain_in_flight: 1 }), status(), memory(), OPTS)?.detail,
    ).toBe('Waiting for 1 chat reply to finish.')
  })

  it('offers Cancel on a switch only to the browser whose Generate run started it', () => {
    const switching = envelope({ phase: 'unloading', switching: true })
    expect(statusLine(switching, status(), memory(), OPTS)?.actions).toEqual([])
    expect(statusLine(switching, status(), memory(), { ...OPTS, ownRun: true })?.actions).toEqual(['cancel'])
  })

  it('shows the hand-off while the editor hand-off lasts, and offers keep', () => {
    const line = statusLine(envelope({ mode: 'img', phase: 'handoff', handoff_until: 1004 }), status(), memory(), OPTS)
    expect(line).toMatchObject({ tone: 'img', head: 'Image mode · hand-off', actions: ['keep'] })
  })

  it('statusLine rendering uses progress step for hal0 renders and queue count for editor renders', () => {
    const rendering = envelope({ mode: 'img', phase: 'rendering', image_jobs: 1 })
    // A hal0 render: its own step.
    let line = statusLine(rendering, status({ progress: progress() }), memory(), OPTS)
    expect(line).toMatchObject({ tone: 'img', head: 'Rendering · flux-dev · step 7/20', actions: ['cancel_render', 'keep'] })
    // The first frame has not arrived yet.
    line = statusLine(rendering, status({ progress: progress({ step: null, total: null }) }), memory(), OPTS)
    expect(line?.head).toBe('Rendering · flux-dev')
    // hal0 opened the job but ComfyUI has not accepted the prompt, and nothing else is queued.
    expect(statusLine(rendering, status(), memory(), OPTS)?.head).toBe('Rendering')
    // An editor render is only a queue count, and the arbiter reads it as grace (or nothing).
    const editorQueue = { running: 1, pending: 2 }
    for (const phase of ['grace', 'handoff', null]) {
      line = statusLine(
        envelope({ mode: 'img', phase, restore_at: phase === 'grace' ? 1005 : null }),
        status({ queue: editorQueue }),
        memory(),
        OPTS,
      )
      expect(line).toMatchObject({ head: 'Rendering · editor prompt · 3 queued', actions: ['cancel_render', 'keep'] })
    }
    // A busy queue with a hal0 render in it reads as that render.
    line = statusLine(envelope({ mode: 'img', phase: 'grace', restore_at: 1005 }), status({ queue: editorQueue, progress: progress() }), memory(), OPTS)
    expect(line?.head).toBe('Rendering · flux-dev · step 7/20')
    // A busy queue in chat mode is not image mode: nothing to override.
    expect(statusLine(envelope({ mode: 'llm' }), status({ queue: editorQueue }), memory(), OPTS)?.head).toBe(
      'GPU · chat mode',
    )
  })

  it('statusLine grace counts down from restore_at', () => {
    const grace = (restore_at: number | null) => envelope({ mode: 'img', phase: 'grace', restore_at })
    const line = statusLine(grace(1007.2), status(), memory(), OPTS)
    expect(line).toMatchObject({ tone: 'img', head: 'Image mode ends in 8 s', actions: ['keep'] })
    expect(statusLine(grace(1000.4), status(), memory(), OPTS)?.head).toBe('Image mode ends in 1 s')
    // The clock is a hair ahead of the server's: never a negative count.
    expect(statusLine(grace(995), status(), memory(), OPTS)?.head).toBe('Image mode ends in 0 s')
    expect(statusLine(grace(null), status(), memory(), OPTS)?.head).toBe('Image mode ends in 0 s')
  })

  it('reads reloading as k of n ready from the slots, and as plain text without them', () => {
    const reloading = envelope({ mode: 'img', phase: 'reloading', unload_set: ['brain', 'coder', 'vision'] })
    const slots = [
      { name: 'brain', state: 'ready' },
      { name: 'coder', state: 'serving' },
      { name: 'vision', state: 'starting' },
      { name: 'other', state: 'ready' },
    ]
    expect(statusLine(reloading, status(), memory(), { ...OPTS, slots })).toMatchObject({
      tone: 'img',
      head: 'Reloading GPU slots · 2 of 3 ready',
      actions: [],
    })
    expect(statusLine(reloading, status(), memory(), OPTS)?.head).toBe('Reloading GPU slots')
    expect(
      statusLine(envelope({ mode: 'img', phase: 'reloading', unload_set: [] }), status(), memory(), { ...OPTS, slots })?.head,
    ).toBe('Reloading GPU slots')
  })

  it('does not promise a reload after the last image on a pinned box', () => {
    // A pinned image mode never restores on its own: the slots stay paused until it is unpinned.
    const pinnedRendering = envelope({ mode: 'img', pinned: true, phase: 'rendering', image_jobs: 1 })
    const hal0 = statusLine(pinnedRendering, status({ progress: progress() }), memory(), OPTS)
    expect(hal0?.detail).toBe('The paused GPU slots stay paused until you unpin image mode.')
    const editor = statusLine(
      envelope({ mode: 'img', pinned: true, phase: 'grace', restore_at: 1005 }),
      status({ queue: { running: 1, pending: 0 } }),
      memory(),
      OPTS,
    )
    expect(editor?.head).toBe('Rendering · editor prompt · 1 queued')
    expect(editor?.detail).not.toMatch(/10 s|reload/)
    // Unpinned, the promise stands.
    const unpinned = statusLine(envelope({ mode: 'img', phase: 'rendering', image_jobs: 1 }), status(), memory(), OPTS)
    expect(unpinned?.detail).toBe('The paused GPU slots reload about 10 s after the last image.')
  })

  it('does not mention paused slots when image mode paused none', () => {
    for (const unload_set of [[], null]) {
      const states: Array<[string, Partial<ArbiterState>]> = [
        ['rendering', { mode: 'img', phase: 'rendering', image_jobs: 1 }],
        ['pinned rendering', { mode: 'img', pinned: true, phase: 'rendering', image_jobs: 1 }],
        ['grace', { mode: 'img', phase: 'grace', restore_at: 1005 }],
        ['pinned idle', { mode: 'img', pinned: true }],
        ['image mode on', { mode: 'img', phase: null }],
      ]
      for (const [name, over] of states) {
        const line = statusLine(envelope({ ...over, unload_set }), status(), memory(), OPTS)
        expect(line, name).not.toBeNull()
        expect(line?.detail, name).not.toMatch(/paused|reload|slots/i)
      }
    }
    // The chat-wait line still shows when something waits.
    const waiting = { count: 1, oldest_s: 3 }
    expect(
      statusLine(envelope({ mode: 'img', phase: 'rendering', unload_set: [], llm_waiters: waiting }), status(), memory(), OPTS)
        ?.detail,
    ).toBe('1 chat request waiting, longest 0:03 of 2:00.')
  })

  it('statusLine pinned offers unpin and stop', () => {
    const line = statusLine(envelope({ mode: 'img', pinned: true }), status(), memory(), OPTS)
    expect(line).toMatchObject({ tone: 'pin', head: 'Image mode pinned', actions: ['unpin', 'stop'] })
  })

  it('shows the work a pinned box is doing, with unpin among the actions and no keep', () => {
    const pinned = (phase: string) => envelope({ mode: 'img', pinned: true, phase })
    const rendering = statusLine(pinned('rendering'), status({ progress: progress() }), memory(), OPTS)
    expect(rendering?.head).toBe('Rendering · flux-dev · step 7/20')
    expect(rendering?.actions).toEqual(['cancel_render', 'unpin'])
    expect(statusLine(pinned('handoff'), status(), memory(), OPTS)?.actions).toEqual(['unpin'])
    // Pinned in LLM mode is not "image mode pinned".
    expect(statusLine(envelope({ mode: 'llm', pinned: true }), status(), memory(), OPTS)?.head).toBe('GPU · chat mode')
  })

  it('says image mode is on, and offers keep, in the beat after an api restart', () => {
    const line = statusLine(envelope({ mode: 'img', phase: null }), status(), memory(), OPTS)
    expect(line).toMatchObject({ tone: 'img', head: 'Image mode is on', actions: ['keep'] })
  })

  it('statusLine gate refused outranks phase until dismissed', () => {
    const at = localEpoch(14, 5)
    const refusal: GateRefusal = { at, reason: 'hal0 did not finish switching the GPU <b>in time</b>' }
    const grace = envelope({ mode: 'img', phase: 'grace', restore_at: 1005 }, refusal)
    const shown = statusLine(grace, status({ gate_refusal: refusal }), memory(), OPTS)
    expect(shown).toMatchObject({
      tone: 'warn',
      head: 'GPU gate refused a prompt · 14:05',
      actions: ['open_editor', 'dismiss'],
    })
    // The reason is text for a text node: passed through as is, never turned into markup.
    expect(shown?.detail).toBe('hal0 did not finish switching the GPU <b>in time</b>')
    // It outranks pinned and chat too.
    expect(statusLine(envelope({ mode: 'img', pinned: true }, refusal), status({ gate_refusal: refusal }), memory(), OPTS)?.head)
      .toBe('GPU gate refused a prompt · 14:05')
    expect(statusLine(envelope({}, refusal), status({ gate_refusal: refusal }), memory(), OPTS)?.head)
      .toBe('GPU gate refused a prompt · 14:05')

    // Dismissed by timestamp: this refusal is gone, the phase shows.
    const dismissed = statusLine(grace, status({ gate_refusal: refusal }), memory(), { ...OPTS, dismissedAt: at })
    expect(dismissed?.head).toBe('Image mode ends in 5 s')
    expect(statusLine(grace, status({ gate_refusal: refusal }), memory(), { ...OPTS, dismissedAt: at + 60 })?.head).toBe(
      'Image mode ends in 5 s',
    )
    // A newer refusal is a new event.
    const newer: GateRefusal = { at: at + 120, reason: 'again' }
    expect(statusLine(grace, status({ gate_refusal: newer }), memory(), { ...OPTS, dismissedAt: at })?.head).toBe(
      'GPU gate refused a prompt · 14:07',
    )
  })

  it('lets work in progress outrank a gate refusal', () => {
    const refusal: GateRefusal = { at: localEpoch(9, 30), reason: 'nope' }
    for (const [over, head] of [
      [{ phase: 'unloading', switching: true }, 'Switching to image mode'],
      [{ mode: 'img', phase: 'handoff' }, 'Image mode · hand-off'],
      [{ mode: 'img', phase: 'rendering' }, 'Rendering'],
      [{ mode: 'img', phase: 'reloading' }, 'Reloading GPU slots'],
    ] as const) {
      expect(statusLine(envelope(over, refusal), status({ gate_refusal: refusal }), memory(), OPTS)?.head).toBe(head)
    }
  })

  it('reads the refusal from whichever response has the newer one', () => {
    const old: GateRefusal = { at: localEpoch(8, 0), reason: 'old' }
    const fresh: GateRefusal = { at: localEpoch(8, 30), reason: 'fresh' }
    expect(statusLine(envelope({}, fresh), status({ gate_refusal: old }), memory(), OPTS)?.detail).toBe('fresh')
    expect(statusLine(envelope({}, old), status({ gate_refusal: fresh }), memory(), OPTS)?.detail).toBe('fresh')
    expect(statusLine(envelope({}, null), status({ gate_refusal: old }), memory(), OPTS)?.detail).toBe('old')
  })

  it('statusLine gate stale offers restart engine', () => {
    const line = statusLine(envelope({}), status({ gate: { state: 'stale', version_seen: null } }), memory(), OPTS)
    expect(line).toMatchObject({
      tone: 'warn',
      head: 'The image engine runs an older GPU gate',
      actions: ['restart_engine'],
    })
    // Below a refusal, above pinned and grace.
    const refusal: GateRefusal = { at: localEpoch(9, 30), reason: 'nope' }
    const stale = status({ gate: { state: 'stale', version_seen: null }, gate_refusal: refusal })
    expect(statusLine(envelope({}, refusal), stale, memory(), OPTS)?.head).toBe('GPU gate refused a prompt · 09:30')
    const staleOnly = status({ gate: { state: 'stale', version_seen: null } })
    expect(statusLine(envelope({ mode: 'img', pinned: true }), staleOnly, memory(), OPTS)?.head).toBe(
      'The image engine runs an older GPU gate',
    )
    expect(
      statusLine(envelope({ mode: 'img', phase: 'grace', restore_at: 1005 }), staleOnly, memory(), OPTS)?.head,
    ).toBe('The image engine runs an older GPU gate')
    // An unknown or current gate is not news.
    expect(statusLine(envelope({}), status({ gate: { state: 'unknown', version_seen: null } }), memory(), OPTS)?.head).toBe(
      'GPU · chat mode',
    )
  })

  it('statusLine gate no credential outranks everything and is not dismissible', () => {
    const at = localEpoch(14, 5)
    const refusal: GateRefusal = { at, reason: 'nope' }
    const blocked = status({ gate: { state: 'no_credential', version_seen: null }, gate_refusal: refusal })
    const busy = envelope({ mode: 'img', pinned: true, phase: 'rendering', image_jobs: 1 }, refusal)
    const line = statusLine(busy, blocked, memory(), { ...OPTS, dismissedAt: at })
    expect(line).toMatchObject({
      tone: 'err',
      head: "Image generation is blocked: hal0 requires sign-in and ComfyUI's GPU gate has no credential for it yet",
      actions: [],
    })
    expect(line?.actions).not.toContain('dismiss')
    // Points at the real fix, names the opt-out without recommending it, and no closed issue.
    expect(line?.detail).toContain('ADR-0040')
    expect(line?.detail).toContain('operate/auth')
    expect(line?.detail).toContain('HAL0_COMFYUI_GATE_FAIL_OPEN=1')
    expect(line?.detail).not.toContain('#3058')
    expect(line?.detail).not.toMatch(/recommend|we suggest|you should set/i)
    // It does not need the arbiter to answer.
    expect(statusLine(envelope(null), blocked, memory(), OPTS)?.tone).toBe('err')
    // It still yields to a box with no GPU or no engine, which has no image generation to block.
    expect(statusLine(busy, blocked, memory({ gpu: null, render_free_gb: null }), OPTS)).toBeNull()
  })

  it('shows a gate refusal even when the arbiter did not answer', () => {
    const refusal: GateRefusal = { at: localEpoch(9, 30), reason: 'nope' }
    expect(statusLine(envelope(null, refusal), status({ gate_refusal: refusal }), memory(), OPTS)?.head).toBe(
      'GPU gate refused a prompt · 09:30',
    )
  })

  it('adds the chat-wait line to the detail while image mode holds the GPU', () => {
    const waiting = { count: 2, oldest_s: 75.4 }
    const line = statusLine(envelope({ mode: 'img', pinned: true, llm_waiters: waiting }), status(), memory(), OPTS)
    expect(line?.detail).toContain('2 chat requests waiting, longest 1:15 of 2:00.')
  })
})

describe('costLine', () => {
  it('costLine lists unload set with summed GB from memory.gpu.this_box', () => {
    const a = arbiter({ unload_set: ['brain', 'coder', 'vision'] })
    // `vision` has no row on this box (starting rows aside, a slot unloaded earlier has none),
    // so the sum is of what is known.
    expect(costLine(a.unload_set, memory(), a)).toEqual([
      'Pauses brain, coder and vision (29.3 GB). They reload about 10 s after the last image.',
    ])
    expect(costLine(['brain'], memory(), arbiter({ unload_set: ['brain'] }))).toEqual([
      'Pauses brain (20.2 GB). They reload about 10 s after the last image.',
    ])
    // None of them known: no figure, no empty brackets.
    expect(costLine(['vision', 'tts'], memory(), arbiter({ unload_set: ['vision', 'tts'] }))).toEqual([
      'Pauses vision and tts. They reload about 10 s after the last image.',
    ])
    expect(costLine(['brain'], memory({ gpu: null, render_free_gb: null }), arbiter())).toEqual([
      'Pauses brain. They reload about 10 s after the last image.',
    ])
  })

  it('costLine on a box with no gpu slots', () => {
    expect(costLine([], memory(), arbiter({ unload_set: [] }))).toEqual(['No GPU slots to pause on this box.'])
  })

  it('says nothing for an unknown unload set, never that there are no slots', () => {
    expect(costLine(null, memory(), arbiter({ unload_set: null }))).toEqual([])
  })

  it('says a new image starts at once while image mode is on', () => {
    for (const over of [
      { phase: 'grace', restore_at: 1005 },
      { phase: 'handoff' },
      { phase: 'rendering', image_jobs: 1 },
      { pinned: true, phase: null },
      { phase: null },
    ]) {
      const a = arbiter({ mode: 'img', unload_set: ['brain'], ...over })
      expect(costLine(a.unload_set, memory(), a)).toEqual(['Image mode is still on, so this one starts at once.'])
    }
  })

  it('appends the chat replies the switch is waiting for', () => {
    const one = arbiter({ phase: 'draining', switching: true, drain_in_flight: 1, unload_set: ['brain'] })
    expect(costLine(one.unload_set, memory(), one)).toEqual([
      'Pauses brain (20.2 GB). They reload about 10 s after the last image.',
      '1 chat reply is finishing first.',
    ])
    const many = arbiter({ phase: 'draining', switching: true, drain_in_flight: 3, unload_set: [] })
    expect(costLine(many.unload_set, memory(), many)).toEqual([
      'No GPU slots to pause on this box.',
      '3 chat replies are finishing first.',
    ])
  })
})

describe('waitLine', () => {
  it('waitLine formats m:ss of 2:00', () => {
    expect(waitLine({ count: 3, oldest_s: 75.4 })).toBe('3 chat requests waiting, longest 1:15 of 2:00.')
    expect(waitLine({ count: 1, oldest_s: 7 })).toBe('1 chat request waiting, longest 0:07 of 2:00.')
    expect(waitLine({ count: 2, oldest_s: 120 })).toBe('2 chat requests waiting, longest 2:00 of 2:00.')
  })

  it('has no line without a waiter', () => {
    expect(waitLine({ count: 0, oldest_s: null })).toBeNull()
  })

  it('drops the age when the server gives none', () => {
    expect(waitLine({ count: 2, oldest_s: null })).toBe('2 chat requests waiting.')
  })
})

// `kind` names the state the line is in, so a consumer (the step chips, the progress bar, the
// stale-error reset) never has to read it back out of the headline's text. It is the same for the
// whole of a state: a countdown or a step counter changes the headline, not the kind.
describe('statusLine kind', () => {
  const kind = (env: ArbiterEnvelope, st: StatusLineInputs = status(), opts = OPTS) =>
    statusLine(env, st, memory(), opts)?.kind

  it('names every state', () => {
    const noCredential = status({ gate: { state: 'no_credential', version_seen: null } as ComfyuiGate })
    expect(kind(envelope(), noCredential)).toBe('blocked')

    for (const phase of ['draining', 'unloading', 'starting_engine']) {
      expect(kind(envelope({ mode: 'img', phase, switching: true }))).toBe('switching')
    }
    expect(kind(envelope({ mode: 'img', phase: null, switching: true }))).toBe('switching')

    expect(kind(envelope({ mode: 'img', phase: 'rendering' }))).toBe('rendering')
    // An editor render is only a queue count.
    const busy = status({ queue: { running: 1, pending: 0 } })
    expect(kind(envelope({ mode: 'img', phase: 'grace', restore_at: 1005 }), busy)).toBe('rendering')

    expect(kind(envelope({ mode: 'img', phase: 'handoff', handoff_until: 1004 }))).toBe('handoff')
    expect(kind(envelope({ mode: 'img', phase: 'reloading' }))).toBe('reloading')

    expect(kind(envelope({}, { at: 900, reason: 'busy' }))).toBe('refused')
    expect(kind(envelope(), status({ gate: { state: 'stale', version_seen: '0' } as ComfyuiGate }))).toBe('stale')

    expect(kind(envelope({ mode: 'img', pinned: true }))).toBe('pinned')
    expect(kind(envelope({ mode: 'img', phase: 'grace', restore_at: 1005 }))).toBe('grace')
    expect(kind(envelope({ mode: 'img', phase: null }))).toBe('img_on')
    expect(kind(envelope({ mode: 'llm' }))).toBe('chat')
  })

  it('is the work, not the pin, on a pinned box that is working', () => {
    expect(kind(envelope({ mode: 'img', pinned: true, phase: 'rendering' }))).toBe('rendering')
  })

  it('stays put while the headline text moves', () => {
    const grace = (restore_at: number) => envelope({ mode: 'img', phase: 'grace', restore_at })
    const a = statusLine(grace(1008), status(), memory(), OPTS)
    const b = statusLine(grace(1008), status(), memory(), { ...OPTS, now: 1003 })
    expect(a?.head).not.toBe(b?.head)
    expect(a?.kind).toBe('grace')
    expect(b?.kind).toBe('grace')

    const rendering = envelope({ mode: 'img', phase: 'rendering' })
    const step3 = statusLine(rendering, status({ progress: progress({ step: 3 }) }), memory(), OPTS)
    const step4 = statusLine(rendering, status({ progress: progress({ step: 4 }) }), memory(), OPTS)
    expect(step3?.head).not.toBe(step4?.head)
    expect(step3?.kind).toBe('rendering')
    expect(step4?.kind).toBe('rendering')
  })
})
