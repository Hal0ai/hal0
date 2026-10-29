import { describe, expect, it } from 'vitest'
import { coreCount, hostSummary } from '../version-strip-pure'

describe('coreCount', () => {
  it('reads the leading integer of the normalised string', () => {
    expect(coreCount('12c · 24t')).toBe(12)
    expect(coreCount(8)).toBe(8)
  })
  it('is null when unsourced', () => {
    expect(coreCount('')).toBeNull()
    expect(coreCount(undefined)).toBeNull()
    expect(coreCount('0c · 0t')).toBeNull()
  })
})

describe('hostSummary', () => {
  it('unified host gets the uma word', () => {
    expect(hostSummary({ name: 'hal0', cores: '12c · 24t', memoryKind: 'unified' }, 116)).toBe(
      'host hal0 · 12 cores · 116 GB uma',
    )
  })
  it('rounds the pool to one decimal and drops uma off unified', () => {
    expect(hostSummary({ name: 'box', cores: '8c · 16t', memoryKind: 'system' }, 62.44)).toBe(
      'host box · 8 cores · 62.4 GB',
    )
  })
  it('dashes every unsourced figure', () => {
    expect(hostSummary({ name: '', cores: '', memoryKind: 'system' }, 0)).toBe('host — · — cores · — GB')
  })
})
