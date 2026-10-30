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

import { changedTuneKeys, effectiveProfileName, tuneBody, tuneErrors, tuneWire } from '../tune-state.js'

describe('staged tune helpers', () => {
  it('tuneBody parses numeric text and drops blanks', () => {
    expect(tuneBody({ ctx_size: '32768', reasoning_budget: '0', vision: 'off', enable_thinking: false, speculation: '' })).toEqual({
      ctx_size: 32768,
      reasoning_budget: 0,
      vision: 'off',
      enable_thinking: false,
    })
  })
  it('tuneErrors mirrors the server ranges', () => {
    expect(tuneErrors({ ctx_size: '64' }).ctx_size).toContain('128')
    expect(tuneErrors({ ctx_size: '32k' }).ctx_size).toContain('whole number')
    expect(tuneErrors({ ctx_size: '128', reasoning_budget: '-1' })).toEqual({})
    expect(tuneErrors({ reasoning_budget: '-2' }).reasoning_budget).toContain('-1')
    expect(tuneErrors({})).toEqual({})
  })
  it('changedTuneKeys ignores text-vs-number differences', () => {
    expect(changedTuneKeys({ ctx_size: 32768 }, { ctx_size: '32768' })).toEqual([])
    expect(changedTuneKeys({ vision: 'off' }, {})).toEqual(['vision'])
  })
  it('tuneWire: unchanged -> undefined, all cleared -> null, otherwise only changed keys', () => {
    expect(tuneWire({ vision: 'off' }, { vision: 'off' })).toBeUndefined()
    expect(tuneWire({}, {})).toBeUndefined()
    expect(tuneWire({ vision: 'off' }, {})).toBeNull()
    expect(tuneWire({}, { enable_thinking: false, vision: 'off' })).toEqual({ enable_thinking: false, vision: 'off' })
    // removed key -> explicit null; untouched key is NOT re-sent
    expect(tuneWire({ vision: 'off', speculation: 'ngram' }, { vision: 'off' })).toEqual({ speculation: null })
  })
  it('tuneWire never re-sends an unchanged enable_thinking (profile may pin it since)', () => {
    expect(tuneWire({ enable_thinking: true }, { enable_thinking: true, vision: 'off' })).toEqual({ vision: 'off' })
    expect(tuneWire({ enable_thinking: true, vision: 'off' }, { enable_thinking: true, vision: 'auto' })).toEqual({
      vision: 'auto',
    })
    // text-vs-number noise is not a change
    expect(tuneWire({ ctx_size: 32768 }, { ctx_size: '32768', vision: 'off' })).toEqual({ vision: 'off' })
  })
  it('effectiveProfileName follows the server resolver: named, else type seed, else chat', () => {
    expect(effectiveProfileName({ profile: 'mine', type: 'llm' })).toBe('mine')
    expect(effectiveProfileName({ profile: '', type: 'llm' })).toBe('chat')
    expect(effectiveProfileName({ type: 'embedding' })).toBe('embedding')
    expect(effectiveProfileName({ type: 'reranking' })).toBe('reranking')
  })
  it('profileValueLabel falls back to profile-sourced tune_effective values without a profile row', () => {
    const eff = {
      ctx_size: 65536,
      enable_thinking: true,
      reasoning_effort: 'high',
      reasoning_budget: 512,
      vision: 'off',
      speculation: 'ngram',
      sources: { ctx_size: 'profile', enable_thinking: 'slot', reasoning_effort: 'profile', reasoning_budget: 'profile', vision: 'profile', speculation: 'profile' },
    }
    expect(profileValueLabel('ctx_size', null, eff)).toBe('64K')
    expect(profileValueLabel('reasoning_effort', null, eff)).toBe('high')
    expect(profileValueLabel('reasoning_budget', null, eff)).toBe('512')
    expect(profileValueLabel('vision', null, eff)).toBe('off')
    expect(profileValueLabel('speculation', null, eff)).toBe('ngram')
    // slot-sourced value is the override, not the profile's: no fallback
    expect(profileValueLabel('enable_thinking', null, eff)).toBe('default (off)')
    // a real profile row always wins
    expect(profileValueLabel('vision', { vision: 'auto' }, eff)).toBe('auto')
  })
})
