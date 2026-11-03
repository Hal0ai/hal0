import { describe, expect, it } from 'vitest'
import { logsHashForUnit, parseLogsUnit, slotForUnit } from '../logs-hash'

describe('parseLogsUnit', () => {
  it('reads the unit off the logs hash', () => {
    expect(parseLogsUnit('#logs?unit=hal0-agent@hermes.service')).toBe('hal0-agent@hermes.service')
  })
  it('decodes an encoded unit', () => {
    expect(parseLogsUnit('#logs?unit=hal0-agent%40hermes.service')).toBe('hal0-agent@hermes.service')
  })
  it('ignores unknown keys', () => {
    expect(parseLogsUnit('#logs?x=1&unit=hal0-api&y=2')).toBe('hal0-api')
  })
  it.each(['', '#logs', '#logs?', '#logs?unit=', '#logs?unit=%20', '#logs?other=1', '#slots?unit=hal0-api'])(
    'returns null for %j',
    (hash) => {
      expect(parseLogsUnit(hash)).toBeNull()
    },
  )
})

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

describe('logsHashForUnit', () => {
  it('round-trips through parseLogsUnit', () => {
    const unit = 'hal0-agent@hermes.service'
    expect(logsHashForUnit(unit)).toBe('#logs?unit=hal0-agent%40hermes.service')
    expect(parseLogsUnit(logsHashForUnit(unit))).toBe(unit)
  })
})
