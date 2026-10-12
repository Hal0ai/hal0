import { describe, it, expect } from 'vitest'
import { overriddenCaps, remainingCaps, overrideSummary, CAP_DEFS } from '../cap-overrides'

describe('file-fact overrides ledger (ADR-0029: only MTP heads + jinja stay on the model)', () => {
  it('offers exactly the two file facts', () => {
    expect(CAP_DEFS.map((d) => d.id)).toEqual(['mtp', 'jinja'])
  })
  it('null = auto = invisible', () => {
    expect(overriddenCaps({ mtp: null, jinja: null })).toEqual([])
  })
  it('non-null renders one chip, remaining feeds the + menu', () => {
    const flags = { mtp: null, jinja: false }
    expect(overriddenCaps(flags)).toEqual([{ id: 'jinja', value: false }])
    expect(remainingCaps(flags).map((c) => c.id)).toEqual(['mtp'])
  })
  it('every cap def carries decision-time consequence copy', () => {
    for (const def of CAP_DEFS) expect(def.consequence.length).toBeGreaterThan(10)
  })
})

describe('overrideSummary — per-chip value-specific hint', () => {
  it('every "on" override reduces to the plain fact, regardless of cap', () => {
    for (const def of CAP_DEFS) {
      expect(overrideSummary(def.id, true)).toBe(`${def.label} forced on.`)
    }
  })
  it('jinja off names the concrete consequence', () => {
    expect(overrideSummary('jinja', false)).toBe(
      'Jinja marked unsupported — this file launches without --jinja.',
    )
  })
  it('each cap gets its own off summary — no two caps share the same off text', () => {
    const offTexts = new Set(CAP_DEFS.map((def) => overrideSummary(def.id, false)))
    expect(offTexts.size).toBe(CAP_DEFS.length)
  })
})
