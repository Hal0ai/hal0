import { describe, expect, it } from 'vitest'
import {
  ageSeconds,
  formatCount,
  formatDate,
  formatDateTime,
  formatDuration,
  formatRelativeTime,
} from './format-pure'

const ISO_SHAPE = /\d{4}-\d{2}-\d{2}T/

describe('formatCount', () => {
  it.each([
    [0, '0'],
    [7, '7'],
    [999, '999'],
    [1000, '1,000'],
    [1093, '1,093'],
    [52955, '52,955'],
    [1234567, '1,234,567'],
    [12.6, '13'],
    [-4200, '-4,200'],
  ])('%s → %s', (input, expected) => {
    expect(formatCount(input)).toBe(expected)
  })

  it.each([[null], [undefined], [Number.NaN], [Number.POSITIVE_INFINITY]])(
    'answers null for %s, never a printed zero',
    (input) => {
      expect(formatCount(input as number | null | undefined)).toBeNull()
    },
  )
})

describe('formatDuration', () => {
  it.each([
    [0, '0 s'],
    [45, '45 s'],
    [59.9, '59 s'],
    [60, '1 min'],
    [12 * 60, '12 min'],
    [59 * 60 + 59, '59 min'],
    [3600, '1 h'],
    [6 * 3600, '6 h'],
    [6 * 3600 - 1, '5 h'],
    [47 * 3600, '47 h'],
    [48 * 3600, '2 d'],
    [9 * 86400, '9 d'],
    [1500 * 86400, '1,500 d'],
  ])('%s s → %s', (input, expected) => {
    expect(formatDuration(input)).toBe(expected)
  })

  it.each([[null], [undefined], [-1], [Number.NaN]])('answers null for %s', (input) => {
    expect(formatDuration(input as number | null | undefined)).toBeNull()
  })

  it('never spells the unit out', () => {
    for (const s of [30, 600, 7200, 400000]) {
      expect(formatDuration(s)).not.toMatch(/hour|minute|second|day/)
    }
  })
})

describe('formatDate and formatDateTime', () => {
  it.each([
    ['2026-11-05T09:14:00Z', '5 Nov 2026'],
    ['2026-11-06T00:00:00.000Z', '6 Nov 2026'],
    // The engine's own stamp: microseconds and a numeric offset.
    ['2026-11-11T02:00:39.102207+00:00', '11 Nov 2026'],
    ['2026-09-30T12:00:00Z', '30 Sep 2026'],
    ['2026-01-01T00:00:00Z', '1 Jan 2026'],
  ])('%s → %s', (input, expected) => {
    expect(formatDate(input, 'UTC')).toBe(expected)
  })

  it('reads the day in the zone it is given', () => {
    expect(formatDate('2026-11-05T23:30:00Z', 'UTC')).toBe('5 Nov 2026')
    expect(formatDate('2026-11-05T23:30:00Z', 'Asia/Tokyo')).toBe('6 Nov 2026')
  })

  it('takes a Date and an epoch number as well as a string', () => {
    const at = Date.UTC(2026, 10, 5, 9, 14)
    expect(formatDate(at, 'UTC')).toBe('5 Nov 2026')
    expect(formatDate(new Date(at), 'UTC')).toBe('5 Nov 2026')
  })

  it('adds a 24-hour time', () => {
    expect(formatDateTime('2026-11-05T09:14:00Z', 'UTC')).toBe('5 Nov 2026, 09:14')
    expect(formatDateTime('2026-11-05T21:05:00Z', 'UTC')).toBe('5 Nov 2026, 21:05')
    expect(formatDateTime('2026-11-05T00:00:00Z', 'UTC')).toBe('5 Nov 2026, 00:00')
  })

  it.each([[null], [undefined], [''], ['not a date']])('answers null for %j', (input) => {
    expect(formatDate(input, 'UTC')).toBeNull()
    expect(formatDateTime(input, 'UTC')).toBeNull()
  })
})

describe('formatRelativeTime', () => {
  const now = '2026-11-11T12:00:00Z'

  it.each([
    ['2026-11-11T11:59:30Z', 'just now'],
    ['2026-11-11T11:48:00Z', '12 min ago'],
    ['2026-11-11T06:00:00Z', '6 h ago'],
    ['2026-11-08T12:00:00Z', '3 d ago'],
    ['2026-11-11T12:12:00Z', 'in 12 min'],
    // Past 30 days the date says more than "41 d ago".
    ['2026-10-01T12:00:00Z', '1 Oct 2026'],
    ['2026-12-25T12:00:00Z', '25 Dec 2026'],
  ])('%s → %s', (input, expected) => {
    expect(formatRelativeTime(input, now, 'UTC')).toBe(expected)
  })

  it('answers null when either time is missing or unreadable', () => {
    expect(formatRelativeTime(null, now)).toBeNull()
    expect(formatRelativeTime('nope', now)).toBeNull()
    expect(formatRelativeTime(now, 'nope')).toBeNull()
  })
})

describe('ageSeconds', () => {
  it('is the gap to now, and never negative', () => {
    expect(ageSeconds('2026-11-11T06:00:00Z', '2026-11-11T12:00:00Z')).toBe(6 * 3600)
    expect(ageSeconds('2026-11-11T13:00:00Z', '2026-11-11T12:00:00Z')).toBe(0)
    expect(ageSeconds(null, '2026-11-11T12:00:00Z')).toBeNull()
  })
})

describe('no raw ISO string reaches the page', () => {
  it.each([['2026-11-05T09:14:00Z'], ['2026-11-11T02:00:39.102207+00:00']])('%s', (iso) => {
    const now = '2026-11-11T12:00:00Z'
    for (const out of [
      formatDate(iso, 'UTC'),
      formatDateTime(iso, 'UTC'),
      formatRelativeTime(iso, now, 'UTC'),
    ]) {
      expect(out).not.toBeNull()
      expect(out).not.toMatch(ISO_SHAPE)
    }
  })
})
