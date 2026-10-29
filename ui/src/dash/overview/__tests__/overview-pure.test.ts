import { describe, expect, it } from 'vitest'
import { fmtGb, fmtK, pctOf, relTime } from '../overview-pure'

describe('fmtK', () => {
  it('formats thousands with one decimal under 10k', () => expect(fmtK(1854)).toBe('1.9k'))
  it('rounds to whole k above 10k', () => expect(fmtK(18620)).toBe('19k'))
  it('passes small numbers through', () => expect(fmtK(312)).toBe('312'))
})

describe('fmtGb', () => {
  it('renders a dash for null', () => expect(fmtGb(null)).toBe('—'))
  it('renders one decimal with the unit', () => expect(fmtGb(30.04)).toBe('30.0 GB'))
})

describe('relTime', () => {
  it('says just now under a minute', () => expect(relTime(1000, 1030)).toBe('just now'))
  it('says minutes under an hour', () => expect(relTime(1000, 1000 + 35 * 60)).toBe('35 min ago'))
  it('says hours otherwise', () => expect(relTime(1000, 1000 + 2 * 3600 + 10)).toBe('2 h ago'))
})

describe('pctOf', () => {
  it('returns null when either side is missing', () => expect(pctOf(3, null)).toBeNull())
  it('returns null for a zero total', () => expect(pctOf(3, 0)).toBeNull())
  it('computes the percentage', () => expect(pctOf(25, 100)).toBe(25))
  it('clamps to 100', () => expect(pctOf(130, 100)).toBe(100))
})
