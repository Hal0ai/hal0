import { describe, expect, it } from 'vitest'
import { resolveLoadTarget } from './load-target-pure'

const model = { id: 'qwen', type: 'llm' }
const slot = (name: string, extra: Record<string, unknown> = {}) => ({ name, type: 'llm', ...extra })

describe('resolveLoadTarget', () => {
  it('reports no slot when none accepts the type', () => {
    expect(resolveLoadTarget([{ name: 'tts', type: 'tts' }], model)).toEqual({ kind: 'none' })
    expect(resolveLoadTarget([], model)).toEqual({ kind: 'none' })
  })
  it('uses the slot that already owns the model', () => {
    const slots = [slot('agent'), slot('brain', { model_id: 'qwen' }), slot('coder')]
    expect(resolveLoadTarget(slots, model)).toEqual({ kind: 'target', slot: slots[1] })
  })
  it('uses the only compatible slot', () => {
    const slots = [slot('agent'), { name: 'tts', type: 'tts' }]
    expect(resolveLoadTarget(slots, model)).toEqual({ kind: 'target', slot: slots[0] })
  })
  it('asks the user to pick when several compatible slots exist (fresh install)', () => {
    const slots = [slot('agent'), slot('brain'), slot('coder'), slot('memory')]
    expect(resolveLoadTarget(slots, model)).toEqual({ kind: 'choose', options: slots })
  })
})
