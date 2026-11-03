import { describe, expect, it } from 'vitest'
import { slotForUnit } from '../logs-hash'

describe('slotForUnit', () => {
  it('maps slot units to the slot name', () => {
    expect(slotForUnit('hal0-slot@primary.service')).toBe('primary')
    expect(slotForUnit('hal0-slot@primary')).toBe('primary')
  })
  it('is null for other units', () => {
    expect(slotForUnit('hal0-agent@hermes.service')).toBeNull()
    expect(slotForUnit('hal0-api')).toBeNull()
  })
})
