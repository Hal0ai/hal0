import { describe, it, expect } from 'vitest'
import { overriddenCaps, remainingCaps, overrideSummary, CAP_DEFS } from '../cap-overrides'

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

describe('overrideSummary — per-chip value-specific hint (replaces the joined resting copy)', () => {
  it('every "on" override reduces to the plain fact, regardless of cap', () => {
    for (const def of CAP_DEFS) {
      expect(overrideSummary(def.id, true)).toBe(`${def.label} forced on.`)
    }
  })
  it('vision off names the concrete consequence (panel-07 wording)', () => {
    expect(overrideSummary('vision', false)).toBe(
      'Vision forced off — skips the mmproj projector, saves ~0.9 GB VRAM.',
    )
  })
  it('each cap gets its own off summary — no two caps share the same off text', () => {
    const offTexts = new Set(CAP_DEFS.map((def) => overrideSummary(def.id, false)))
    expect(offTexts.size).toBe(CAP_DEFS.length)
  })
})
