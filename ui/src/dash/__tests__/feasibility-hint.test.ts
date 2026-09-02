import { describe, it, expect } from 'vitest'
import { feasibilityHint } from '../feasibility-copy'

describe('feasibilityHint', () => {
  const base = { gtt_free_mb: 69632, gtt_total_mb: 98304 }
  it('fits → ok tone with free figure', () => {
    const h = feasibilityHint({ verdict: 'fits', needed_mb: 55296, ...base })
    expect(h.tone).toBe('ok')
    expect(h.text).toMatch(/≈ 54 GB .* 68 GB GTT free — fits/)
  })
  it('tight → warn with the consequence and the fix', () => {
    const h = feasibilityHint({ verdict: 'tight', needed_mb: 65536, ...base })
    expect(h.tone).toBe('warn')
    expect(h.text).toMatch(/tight/i)
  })
  it('exceeds → err tone against the free figure', () => {
    const h = feasibilityHint({ verdict: 'exceeds', needed_mb: 81920, ...base })
    expect(h.tone).toBe('err')
    expect(h.text).toMatch(/exceeds 68 GB free/)
    expect(h.text).toMatch(/Saves anyway/)
  })
  it('exceeds_total → err tone, still saveable copy', () => {
    const h = feasibilityHint({ verdict: 'exceeds_total', needed_mb: 131072, ...base })
    expect(h.tone).toBe('err')
    expect(h.text).toMatch(/exceeds 96 GB total GTT/)
    expect(h.text).toMatch(/Saves anyway/)
  })
  it('unknown → renders nothing', () => {
    expect(feasibilityHint({ verdict: 'unknown', needed_mb: 0, gtt_free_mb: null, gtt_total_mb: null }).tone).toBeNull()
  })
})
