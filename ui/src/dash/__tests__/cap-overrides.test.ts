import { describe, it, expect } from 'vitest'
import { overriddenCaps, remainingCaps, CAP_DEFS } from '../cap-overrides'

describe('capability overrides ledger', () => {
  it('null = auto = invisible', () => {
    expect(overriddenCaps({ thinking: null, mtp: null, jinja: null, vision: null })).toEqual([])
  })
  it('non-null renders one chip, remaining feeds the + menu', () => {
    const flags = { thinking: null, mtp: null, jinja: null, vision: false }
    expect(overriddenCaps(flags)).toEqual([{ id: 'vision', value: false }])
    expect(remainingCaps(flags).map(c => c.id)).toEqual(['thinking', 'mtp', 'jinja'])
  })
  it('every cap def carries decision-time consequence copy', () => {
    for (const def of CAP_DEFS) expect(def.consequence.length).toBeGreaterThan(10)
  })
})
