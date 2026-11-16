// The top bar's GPU mode toggle: which segment is selected for each line kind, what a click on
// each segment calls, and the confirm's words. Pure, and fed with real `statusLine` views so the
// mapping is checked against the kinds and actions the line really produces.
import { describe, expect, it } from 'vitest'
import type { ArbiterEnvelope, ArbiterState, ComfyuiGate, GateRefusal } from '@/api/hooks/useComfyui'
import type { MemoryBudgetJson } from '@/api/hooks/useStatsHardware'
import {
  actionFacts,
  statusLine,
  type StatusLineInputs,
  type StatusLineKind,
} from '../image/status-line-pure'
import {
  gpuToggleModel,
  pinPopoverQuestion,
  toggleClickAction,
  toggleTitle,
} from '../dock/gpu-toggle-pure'

const arbiter = (over: Partial<ArbiterState> = {}): ArbiterState => ({
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
})

const envelope = (over: Partial<ArbiterState> = {}, refusal: GateRefusal | null = null): ArbiterEnvelope => ({
  mode: null,
  arbiter: arbiter(over),
  switchover: { active: false, target: null, error: null },
  gate_refusal: refusal,
})

const status = (over: Partial<StatusLineInputs> = {}): StatusLineInputs => ({
  queue: { running: 0, pending: 0 },
  progress: null,
  gate: { state: 'current', version_seen: '1' } as ComfyuiGate,
  gate_refusal: null,
  ...over,
})

const memory = (): MemoryBudgetJson =>
  ({
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
    render_ram_free_gb: 44,
  }) as MemoryBudgetJson

const OPTS = { now: 1000, engineAvailable: true }

// The line and the action facts for a box, the way useGpuStatus reads them.
function read(over: Partial<ArbiterState>, st: StatusLineInputs = status(), refusal: GateRefusal | null = null) {
  const env = envelope(over, refusal)
  const view = statusLine(env, st, memory(), OPTS)
  return { view, facts: actionFacts(env, st) }
}

const BOXES: Array<[StatusLineKind, Partial<ArbiterState>]> = [
  ['chat', { mode: 'llm' }],
  ['reloading', { mode: 'llm', phase: 'reloading' }],
  ['switching', { mode: 'llm', phase: 'unloading', switching: true }],
  ['handoff', { mode: 'img', phase: 'handoff' }],
  ['rendering', { mode: 'img', phase: 'rendering', image_jobs: 1 }],
  ['pinned', { mode: 'img', pinned: true }],
  ['grace', { mode: 'img', phase: 'grace', restore_at: 1008 }],
  ['img_on', { mode: 'img' }],
]

describe('gpuToggleModel', () => {
  it.each(BOXES)('%s is the kind the line reads', (kind, over) => {
    expect(read(over).view?.kind).toBe(kind)
  })

  it('selects Chat in chat mode and while the slots reload', () => {
    for (const [kind, over] of BOXES.filter(([k]) => k === 'chat' || k === 'reloading')) {
      const { view, facts } = read(over)
      expect(gpuToggleModel(view, facts)?.selected, kind).toBe('chat')
    }
  })

  it('selects Image for a switch, the hand-off, a render, a pin, grace and image mode on', () => {
    for (const [kind, over] of BOXES.filter(([k]) => k !== 'chat' && k !== 'reloading')) {
      const { view, facts } = read(over)
      expect(gpuToggleModel(view, facts)?.selected, kind).toBe('image')
    }
  })

  it('marks the spinner, the lock and the progress bar', () => {
    const at = (over: Partial<ArbiterState>) => {
      const { view, facts } = read(over)
      return gpuToggleModel(view, facts)!
    }
    expect(at({ mode: 'llm', phase: 'reloading' })).toMatchObject({ reloading: true, switching: false, pinned: false })
    expect(at({ mode: 'llm', phase: 'unloading', switching: true })).toMatchObject({ switching: true, reloading: false })
    expect(at({ mode: 'img', pinned: true })).toMatchObject({ pinned: true, rendering: false })
    expect(at({ mode: 'img', phase: 'rendering', image_jobs: 1 })).toMatchObject({ rendering: true, pinned: false })
    // A pinned box that renders is still locked, and still renders.
    expect(at({ mode: 'img', pinned: true, phase: 'rendering', image_jobs: 1 })).toMatchObject({
      pinned: true,
      rendering: true,
    })
    expect(at({ mode: 'llm' })).toMatchObject({ reloading: false, switching: false, pinned: false, rendering: false })
  })

  it('is hidden when the line is: no view at all', () => {
    expect(gpuToggleModel(null, { pinned: false })).toBeNull()
    const env = envelope()
    const noGpu = { ...memory(), gpu: null } as unknown as MemoryBudgetJson
    expect(statusLine(env, status(), noGpu, OPTS)).toBeNull()
    expect(statusLine(env, status(), memory(), { ...OPTS, engineAvailable: false })).toBeNull()
  })

  it('leaves blocked, refused and stale to the footer', () => {
    const blocked = read({ mode: 'llm' }, status({ gate: { state: 'no_credential' } as ComfyuiGate }))
    expect(blocked.view?.kind).toBe('blocked')
    expect(gpuToggleModel(blocked.view, blocked.facts)).toBeNull()

    const refused = read({ mode: 'llm' }, status(), { at: 1, reason: 'no' } as GateRefusal)
    expect(refused.view?.kind).toBe('refused')
    expect(gpuToggleModel(refused.view, refused.facts)).toBeNull()

    const stale = read({ mode: 'llm' }, status({ gate: { state: 'stale' } as ComfyuiGate }))
    expect(stale.view?.kind).toBe('stale')
    expect(gpuToggleModel(stale.view, stale.facts)).toBeNull()
  })
})

describe('toggleClickAction', () => {
  const click = (segment: 'chat' | 'image', over: Partial<ArbiterState>) => {
    const { view, facts } = read(over)
    return toggleClickAction(segment, view, facts)
  }

  it('Image in chat mode holds', () => {
    expect(click('image', { mode: 'llm' })).toBe('hold')
  })

  it('Image during grace, a render, the hand-off and image mode on keeps', () => {
    expect(click('image', { mode: 'img', phase: 'grace', restore_at: 1008 })).toBe('keep')
    expect(click('image', { mode: 'img', phase: 'rendering', image_jobs: 1 })).toBe('keep')
    expect(click('image', { mode: 'img', phase: 'handoff' })).toBe('keep')
    expect(click('image', { mode: 'img' })).toBe('keep')
  })

  it('Image while pinned does nothing, rendering or not', () => {
    expect(click('image', { mode: 'img', pinned: true })).toBeNull()
    expect(click('image', { mode: 'img', pinned: true, phase: 'rendering', image_jobs: 1 })).toBeNull()
  })

  it('Image mid-change does nothing', () => {
    expect(click('image', { mode: 'llm', phase: 'unloading', switching: true })).toBeNull()
    expect(click('image', { mode: 'llm', phase: 'reloading' })).toBeNull()
  })

  it('Chat in image mode stops, pinned or not', () => {
    expect(click('chat', { mode: 'img', pinned: true })).toBe('stop')
    expect(click('chat', { mode: 'img', phase: 'grace', restore_at: 1008 })).toBe('stop')
    expect(click('chat', { mode: 'img', phase: 'rendering', image_jobs: 1 })).toBe('stop')
    expect(click('chat', { mode: 'img', phase: 'handoff' })).toBe('stop')
    expect(click('chat', { mode: 'img' })).toBe('stop')
  })

  it('Chat in chat mode, while reloading and while switching does nothing', () => {
    expect(click('chat', { mode: 'llm' })).toBeNull()
    expect(click('chat', { mode: 'llm', phase: 'reloading' })).toBeNull()
    expect(click('chat', { mode: 'llm', phase: 'unloading', switching: true })).toBeNull()
  })

  it('does nothing without a line, or on a footer-only one', () => {
    expect(toggleClickAction('image', null, { pinned: false })).toBeNull()
    const blocked = read({ mode: 'llm' }, status({ gate: { state: 'no_credential' } as ComfyuiGate }))
    expect(toggleClickAction('image', blocked.view, blocked.facts)).toBeNull()
    expect(toggleClickAction('chat', blocked.view, blocked.facts)).toBeNull()
  })
})

describe('pinPopoverQuestion', () => {
  it('hold names the slots it pauses and what they hold, from the same facts as the cost line', () => {
    expect(pinPopoverQuestion('hold', ['brain', 'coder'], memory())).toBe(
      'Pause brain and coder (29.3 GB) and refuse GPU chat until you switch back?',
    )
    // One slot, no figure: no memory row for it.
    expect(pinPopoverQuestion('hold', ['agent'], memory())).toBe(
      'Pause agent and refuse GPU chat until you switch back?',
    )
  })

  it('hold with nothing known to pause only says what the pin does', () => {
    expect(pinPopoverQuestion('hold', null, memory())).toBe('Refuse GPU chat until you switch back?')
    expect(pinPopoverQuestion('hold', [], memory())).toBe('Refuse GPU chat until you switch back?')
  })

  it('keep says the paused slots stay paused, when there are any', () => {
    expect(pinPopoverQuestion('keep', ['brain'], memory())).toBe(
      'Refuse GPU chat until you switch back? The paused GPU slots stay paused.',
    )
    expect(pinPopoverQuestion('keep', [], memory())).toBe('Refuse GPU chat until you switch back?')
  })
})

describe('toggleTitle', () => {
  it('is the full cost line in chat mode', () => {
    const env = envelope({ mode: 'llm' })
    const view = statusLine(env, status(), memory(), OPTS)
    expect(toggleTitle(view, env.arbiter!.unload_set, memory(), env.arbiter)).toBe(
      'Pauses brain and coder (29.3 GB). They reload about 10 s after the last image.',
    )
  })

  it('falls back to the headline when the line has no cost', () => {
    const env = envelope({ mode: 'img', phase: 'grace', restore_at: 1008 })
    const view = statusLine(env, status(), memory(), OPTS)
    // Image mode on: the cost line says the next image starts at once.
    expect(toggleTitle(view, env.arbiter!.unload_set, memory(), env.arbiter)).toBe(
      'Image mode is still on, so this one starts at once.',
    )
    expect(toggleTitle({ head: 'Image mode pinned' }, null, memory(), null)).toBe('Image mode pinned')
  })
})
