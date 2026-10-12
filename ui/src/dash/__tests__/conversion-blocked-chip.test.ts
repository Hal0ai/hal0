// conversionBlockedChip (slot-status.js) — the classifier behind the
// "Conversion blocked" badge on the slot cards and drawer (ADR-0029).
import { describe, expect, it } from 'vitest'
import { conversionBlockedChip } from '../slot-status.js'

const FIX = 'run `hal0 profile migrate` to see why, edit the slot file, then run `sudo -u hal0 hal0 profile migrate --apply`'

describe('conversionBlockedChip', () => {
  it('is null when the field is absent or malformed', () => {
    expect(conversionBlockedChip({ name: 'a' })).toBeNull()
    expect(conversionBlockedChip({ conversion_blocked: null })).toBeNull()
    expect(conversionBlockedChip({ conversion_blocked: 'yes' })).toBeNull()
    expect(conversionBlockedChip(undefined)).toBeNull()
  })

  it('carries the reason and the server fix verbatim', () => {
    const c = conversionBlockedChip({
      conversion_blocked: { reason: 'bad toml', fix: FIX, running_pre_conversion: false },
    })!
    expect(c.label).toBe('Conversion blocked')
    expect(c.reason).toBe('bad toml')
    expect(c.fix).toBe(FIX)
    expect(c.running).toBe(false)
    expect(c.tooltip).toContain('Why: bad toml')
    expect(c.tooltip).toContain(`Fix: ${FIX}`)
    expect(c.tooltip).not.toContain('pre-conversion')
  })

  it('says the slot is still running its pre-conversion tune only when the server says so', () => {
    const on = conversionBlockedChip({
      conversion_blocked: { reason: 'r', fix: 'f', running_pre_conversion: true },
    })!
    expect(on.running).toBe(true)
    expect(on.tooltip).toContain('Still running its pre-conversion tune')
    const off = conversionBlockedChip({
      conversion_blocked: { reason: 'r', fix: 'f' },
    })!
    expect(off.running).toBe(false)
  })

  it('still renders when the server sent no text', () => {
    const c = conversionBlockedChip({ conversion_blocked: {} })!
    expect(c.tooltip).toMatch(/could not convert/)
  })
})
