import { describe, expect, it } from 'vitest'

import { normalizeSlot } from './useSlots'

describe('normalizeSlot tune fields', () => {
  it('passes tune and tune_effective through', () => {
    const eff = {
      ctx_size: 32768,
      enable_thinking: true,
      reasoning_effort: 'high',
      reasoning_budget: -1,
      vision: 'off',
      speculation: 'auto',
      sources: { vision: 'slot' },
      reasoning_effort_unavailable: false,
    }
    const s = normalizeSlot({ name: 'agent', tune: { vision: 'off' }, tune_effective: eff, ctx_source: 'slot' })
    expect(s.tune).toEqual({ vision: 'off' })
    expect(s.tune_effective).toEqual(eff)
    expect(s.ctx_source).toBe('slot')
  })
  it('defaults to no overrides when an older server omits them', () => {
    const s = normalizeSlot({ name: 'agent' })
    expect(s.tune).toEqual({})
    expect(s.tune_effective).toBeNull()
  })
})
