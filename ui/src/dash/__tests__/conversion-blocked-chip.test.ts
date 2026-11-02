// conversionBlockedChip (slot-status.js) — the classifier behind the
// "Conversion blocked" badge on the slot cards and drawer (ADR-0029).
import { describe, expect, it } from 'vitest'
import { conversionBlockedChip } from '../slot-status.js'

const FIX = 'delete the tuning keys from the slot file and put their values on a profile'

describe('conversionBlockedChip', () => {
  it('is null when the field is absent or malformed', () => {
    expect(conversionBlockedChip({ name: 'a' })).toBeNull()
    expect(conversionBlockedChip({ conversion_blocked: null })).toBeNull()
    expect(conversionBlockedChip({ conversion_blocked: 'yes' })).toBeNull()
    expect(conversionBlockedChip(undefined)).toBeNull()
  })

  it('carries the reason and the server fix verbatim', () => {
    const c = conversionBlockedChip({
      conversion_blocked: { reason: 'bad toml', fix: FIX },
    })!
    expect(c.label).toBe('Conversion blocked')
    expect(c.reason).toBe('bad toml')
    expect(c.fix).toBe(FIX)
    expect(c.tooltip).toContain('Why: bad toml')
    expect(c.tooltip).toContain(`Fix: ${FIX}`)
  })

  it('still renders when the server sent no text', () => {
    const c = conversionBlockedChip({ conversion_blocked: {} })!
    expect(c.tooltip).toMatch(/tuning keys/)
  })
})
