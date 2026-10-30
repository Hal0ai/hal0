// @vitest-environment happy-dom
import { describe, expect, it } from 'vitest'

import {
  TUNE_KEYS,
  eyeState,
  overrideCount,
  profileValueLabel,
  restartNeeded,
  thinkingMark,
} from '../tune-state.js'

describe('tune-state', () => {
  it('TUNE_KEYS is the six keys in order', () => {
    expect(TUNE_KEYS).toEqual([
      'ctx_size',
      'enable_thinking',
      'reasoning_effort',
      'reasoning_budget',
      'vision',
      'speculation',
    ])
  })
  it('overrideCount counts present keys only', () => {
    expect(overrideCount(null)).toBe(0)
    expect(overrideCount({})).toBe(0)
    expect(overrideCount({ vision: 'off', enable_thinking: false, bogus: 1 })).toBe(2)
  })
  describe('thinkingMark', () => {
    it('is null unless thinking is on', () => {
      expect(thinkingMark(null)).toBeNull()
      expect(thinkingMark({ enable_thinking: false })).toBeNull()
      expect(thinkingMark({ enable_thinking: null })).toBeNull()
    })
    it('titles plain, effort and budget cases', () => {
      expect(thinkingMark({ enable_thinking: true, reasoning_effort: 'default', reasoning_budget: -1 })).toEqual({
        title: 'thinking on',
      })
      expect(thinkingMark({ enable_thinking: true, reasoning_effort: 'high', reasoning_budget: -1 })!.title).toBe(
        'thinking on · effort high',
      )
      expect(thinkingMark({ enable_thinking: true, reasoning_effort: 'low', reasoning_budget: 0 })!.title).toBe(
        'thinking on · effort low · budget 0',
      )
    })
  })
  describe('eyeState', () => {
    const vm = { capabilities: ['chat', 'vision'] }
    it('null without vision capability', () => {
      expect(eyeState({ capabilities: ['chat'] }, { vision: 'auto' })).toBeNull()
      expect(eyeState(undefined, null)).toBeNull()
    })
    it('lit unless the slot turns vision off', () => {
      expect(eyeState(vm, { vision: 'auto' })).toBe('lit')
      expect(eyeState(vm, null)).toBe('lit')
      expect(eyeState(vm, { vision: 'off' })).toBe('off')
    })
  })
  it('profileValueLabel formats each key', () => {
    expect(profileValueLabel('ctx_size', { ctx_size: 32768 })).toBe('32K')
    expect(profileValueLabel('ctx_size', {})).toBe('native')
    expect(profileValueLabel('enable_thinking', { enable_thinking: true })).toBe('on')
    expect(profileValueLabel('enable_thinking', { enable_thinking: null })).toBe('default (off)')
    expect(profileValueLabel('reasoning_budget', { reasoning_budget: -1 })).toBe('unlimited')
    expect(profileValueLabel('reasoning_budget', { reasoning_budget: 2048 })).toBe('2048')
    expect(profileValueLabel('reasoning_effort', {})).toBe('default')
    expect(profileValueLabel('vision', { vision: 'off' })).toBe('off')
    expect(profileValueLabel('speculation', {})).toBe('auto')
  })
  it('restartNeeded is false for thinking-only', () => {
    expect(restartNeeded([])).toBe(false)
    expect(restartNeeded(['enable_thinking'])).toBe(false)
    expect(restartNeeded(['enable_thinking', 'vision'])).toBe(true)
    expect(restartNeeded(['ctx_size'])).toBe(true)
  })
})
