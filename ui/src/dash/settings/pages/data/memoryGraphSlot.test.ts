import { describe, expect, it } from 'vitest'

import { normalizeMemoryGraphSlot } from './memoryGraphSlot'

describe('normalizeMemoryGraphSlot', () => {
  it('submits canonical slot names instead of hal0 route aliases', () => {
    expect(normalizeMemoryGraphSlot('hal0/utility', ['agent', 'utility'])).toBe('utility')
  })

  it('keeps plain slot names unchanged', () => {
    expect(normalizeMemoryGraphSlot('utility', ['agent', 'utility'])).toBe('utility')
  })

  it('does not strip arbitrary routed model ids when no matching slot exists', () => {
    expect(normalizeMemoryGraphSlot('hal0/unknown', ['agent', 'utility'])).toBe('hal0/unknown')
  })

  it('trims operator input', () => {
    expect(normalizeMemoryGraphSlot('  hal0/agent  ', ['agent', 'utility'])).toBe('agent')
  })
})
