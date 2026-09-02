import { describe, expect, it } from 'vitest'
import { slotsUsingModel } from '../model-usage.js'

describe('slotsUsingModel', () => {
  it('matches by model_default or live model, dedupes, preserves order', () => {
    const slots = [
      { name: 'agent', model_default: 'm1', model: 'm1' },
      { name: 'brain', model_default: 'm2', model: 'm1' },
      { name: 'tts', model_default: 'm3', model: null },
    ]
    expect(slotsUsingModel(slots, 'm1')).toEqual([{ name: 'agent' }, { name: 'brain' }])
    expect(slotsUsingModel(slots, 'm4')).toEqual([])
    expect(slotsUsingModel(undefined, 'm1')).toEqual([])
  })
})
