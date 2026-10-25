// The pieces the status line component draws that are not copy: the step chips, the render
// progress figures, the paused slot card's note and the facts an action needs. Pure.
import { describe, expect, it } from 'vitest'
import type { ArbiterEnvelope, ArbiterState } from '@/api/hooks/useComfyui'
import { actionFacts, statusLine } from '../image/status-line-pure'
import {
  clockLabel,
  lineKind,
  reloadSteps,
  renderPct,
  slotPause,
  switchSteps,
} from '../image/status-line-parts-pure'

function arbiter(over: Partial<ArbiterState> = {}): ArbiterState {
  return {
    mode: 'img',
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

function envelope(over: Partial<ArbiterState> = {}, refusal: ArbiterEnvelope['gate_refusal'] = null): ArbiterEnvelope {
  return {
    mode: null,
    arbiter: arbiter(over),
    switchover: { active: false, target: null, error: null },
    gate_refusal: refusal,
  }
}

describe('lineKind', () => {
  it('names the headline the step chips and the progress bar hang off', () => {
    const memory = { gpu: {} } as never
    const kind = (env: ArbiterEnvelope, status = {}) =>
      lineKind(statusLine(env, status as never, memory, { now: 0 })!.head)
    expect(kind(envelope({ phase: 'draining' }))).toBe('switching')
    expect(kind(envelope({ phase: 'rendering' }))).toBe('rendering')
    expect(kind(envelope({ phase: 'reloading' }))).toBe('reloading')
    expect(kind(envelope({ phase: 'grace', restore_at: 10 }))).toBe('other')
    expect(kind(envelope({ mode: 'llm' }))).toBe('other')
  })
})

describe('switchSteps', () => {
  it('marks the steps before the running phase done and the ones after it to do', () => {
    expect(switchSteps('unloading').map((s) => [s.label, s.state])).toEqual([
      ['Chat replies', 'done'],
      ['Unload slots', 'active'],
      ['Start engine', 'todo'],
    ])
  })

  it('has every step to do before the first phase is set', () => {
    expect(switchSteps(null).every((s) => s.state === 'todo')).toBe(true)
  })
})

describe('reloadSteps', () => {
  it('has one chip per paused slot, done for the ones that are back', () => {
    const slots = [
      { name: 'brain', state: 'ready' },
      { name: 'coder', state: 'warming' },
    ]
    expect(reloadSteps(['brain', 'coder', 'embed'], slots).map((s) => [s.label, s.state])).toEqual([
      ['brain', 'done'],
      ['coder', 'active'],
      ['embed', 'todo'],
    ])
  })

  it('has no chips when the slots or the set are unknown', () => {
    expect(reloadSteps(null, [])).toEqual([])
    expect(reloadSteps(['brain'], undefined)).toEqual([])
  })
})

describe('render figures', () => {
  it('formats seconds as m:ss', () => {
    expect(clockLabel(83.9)).toBe('1:23')
    expect(clockLabel(5)).toBe('0:05')
    expect(clockLabel(-3)).toBe('0:00')
  })

  it('gives the percentage only when the step and total are known', () => {
    expect(renderPct({ step: 5, total: 20 })).toBe(25)
    expect(renderPct({ step: null, total: 20 })).toBeNull()
    expect(renderPct({ step: 1, total: 0 })).toBeNull()
    expect(renderPct(null)).toBeNull()
    expect(renderPct({ step: 30, total: 20 })).toBe(100)
  })
})

describe('slotPause', () => {
  it('names a stopped slot of the paused set, in image mode only', () => {
    const pause = slotPause(arbiter(), 'brain', true)
    expect(pause).toEqual({
      chip: 'paused · image mode',
      detail: 'Reloads about 10 s after the last image.',
      wait: null,
    })
    expect(slotPause(arbiter({ mode: 'llm' }), 'brain', true)).toBeNull()
    expect(slotPause(arbiter(), 'other', true)).toBeNull()
    expect(slotPause(arbiter({ unload_set: null }), 'brain', true)).toBeNull()
    expect(slotPause(null, 'brain', true)).toBeNull()
  })

  it('does not call a slot that is running or starting paused', () => {
    // A failed unload leaves image mode on with a saved slot still loaded, and the reload brings
    // slots back one at a time: neither is paused.
    expect(slotPause(arbiter(), 'brain', false)).toBeNull()
  })

  it('adds the chat-wait line', () => {
    const pause = slotPause(arbiter({ llm_waiters: { count: 2, oldest_s: 75 } }), 'brain', true)
    expect(pause?.wait).toBe('2 chat requests waiting, longest 1:15 of 2:00.')
  })

  it('does not promise a reload on a pinned box', () => {
    expect(slotPause(arbiter({ pinned: true }), 'brain', true)?.detail).toBe(
      'Stays paused until image mode is unpinned.',
    )
  })

  it('says the reload is under way during it', () => {
    expect(slotPause(arbiter({ phase: 'reloading' }), 'brain', true)?.detail).toBe('Reloading now.')
  })
})

describe('actionFacts', () => {
  it('reads pinned the way the line does: image mode only', () => {
    expect(actionFacts(envelope({ pinned: true }), null).pinned).toBe(true)
    expect(actionFacts(envelope({ pinned: true, mode: 'llm' }), null).pinned).toBe(false)
    expect(actionFacts(null, null).pinned).toBe(false)
  })

  it('counts a rendering phase or a busy queue as a render running', () => {
    expect(actionFacts(envelope({ phase: 'rendering' }), null).renderRunning).toBe(true)
    expect(actionFacts(envelope({ phase: 'grace' }), { queue: { running: 1, pending: 0 } }).renderRunning).toBe(true)
    expect(actionFacts(envelope({ phase: 'grace' }), { queue: { running: 0, pending: 2 } }).renderRunning).toBe(true)
    expect(actionFacts(envelope({ phase: 'grace' }), { queue: { running: 0, pending: 0 } }).renderRunning).toBe(false)
    expect(actionFacts(null, null).renderRunning).toBe(false)
  })

  it('gives the newer refusal timestamp', () => {
    const env = envelope({}, { at: 100, reason: 'a' })
    expect(actionFacts(env, null).refusalAt).toBe(100)
    expect(actionFacts(env, { gate_refusal: { at: 150, reason: 'b' } }).refusalAt).toBe(150)
    expect(actionFacts(envelope({}), null).refusalAt).toBeNull()
  })
})
